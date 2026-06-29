"""
Threads Post Collector — ponto de entrada da linha de comando.

Abordagem:
  1. GraphQL interno   – chama os endpoints GraphQL do Threads/Instagram
                         que o próprio app web utiliza.
  2. Autenticação      – obtém cookies + lsd token + fb_dtsg via Playwright
                         (abre threads.net uma única vez).
  3. Paginação         – cursor opaco retornado em page_info.end_cursor.
  4. Busca por keyword – endpoint /api/graphql com doc_id de busca.
  5. Persistência      – PostgreSQL via db.py.
  6. Comentários       – endpoint de replies do Threads.
  7. Rate limiting     – backoff exponencial + jitter + rotação de delays.

Módulos
-------
config.py   – constantes, variáveis de ambiente e STEALTH_SCRIPT.
parser.py   – funções puras de parsing (posts, replies, edges, tokens).
scraper.py  – :class:`ThreadsScraper` + utilitários de sessão, browser e
              cliente HTTP GraphQL (motor principal de coleta).
"""

import asyncio
import os

from dotenv import load_dotenv

from config import _env_bool, _env_int

# Re-exporta ThreadsScraper para retrocompatibilidade com threads_api_feeder.py
from scraper import ThreadsScraper  # noqa: F401

load_dotenv()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    """
    Analisa os argumentos da linha de comando.

    Returns
    -------
    argparse.Namespace
        Namespace com os atributos ``search``, ``user``, ``target``,
        ``headless``, ``comments`` e ``fresh``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Threads Post Collector",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s", "--search", default=None, metavar="TERMO",
        help="Termo de busca (sobrepõe SEARCH_TERM do .env)",
    )
    parser.add_argument(
        "-u", "--user", default=None, metavar="USERNAME",
        help="Coleta posts de um usuário específico (@username)",
    )
    parser.add_argument(
        "-n", "--target", type=int, default=None, metavar="N",
        help="Quantidade de posts desejada",
    )
    parser.add_argument(
        "--headless", action=argparse.BooleanOptionalAction, default=None,
        help="Modo headless on/off para obtenção de sessão",
    )
    parser.add_argument(
        "--comments", action=argparse.BooleanOptionalAction, default=None,
        help="Coletar comentários/replies dos posts",
    )
    parser.add_argument(
        "--fresh", action=argparse.BooleanOptionalAction, default=None,
        help="Ignora progresso anterior e coleta do zero",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    """
    Ponto de entrada assíncrono do coletor de posts do Threads.

    Lê parâmetros da linha de comando e das variáveis de ambiente,
    inicializa o banco de dados (opcional), executa a coleta via
    :class:`~scraper.ThreadsScraper` e exibe um resumo ao final.

    Raises
    ------
    SystemExit
        Se ``SEARCH_TERM`` e ``TARGET_USER`` não forem definidos.
    """
    args = _parse_args()

    SEARCH_TERM  = args.search or os.getenv("SEARCH_TERM")
    TARGET_USER  = args.user   or os.getenv("TARGET_USER", "")
    TARGET_POSTS = args.target or _env_int("TARGET_POSTS", 200)
    HEADLESS     = (
        args.headless if args.headless is not None
        else _env_bool("HEADLESS", True)
    )
    FETCH_COMMENTS = (
        args.comments if args.comments is not None
        else _env_bool("FETCH_COMMENTS", False)
    )
    FRESH = (
        args.fresh if args.fresh is not None
        else _env_bool("FRESH", False)
    )
    COMMENTS_PER_POST    = _env_int("COMMENTS_PER_POST", 50)
    COMMENTS_CONCURRENCY = _env_int("COMMENTS_CONCURRENCY", 3)

    if not SEARCH_TERM and not TARGET_USER:
        raise SystemExit(
            "ERRO: defina SEARCH_TERM ou TARGET_USER.\n"
            "Use --search <termo> ou --user <username>."
        )

    # --- Banco de dados (opcional) ---
    db     = None
    run_id = None
    pg_host = os.getenv("POSTGRES_HOST", "")
    if pg_host:
        try:
            from db import Database, build_dsn
            dsn = build_dsn(
                host=pg_host,
                port=os.getenv("POSTGRES_PORT", 5432),
                user=os.getenv("POSTGRES_USER", "threads"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
                dbname=os.getenv("POSTGRES_DB", "threads_db"),
            )
            db = Database(dsn)
            await db.init()
            label  = SEARCH_TERM or f"@{TARGET_USER}"
            run_id = await db.create_run(label, TARGET_POSTS)
            print(f"  PostgreSQL conectado | run_id={run_id}\n")
        except Exception as exc:
            print(f"  Aviso: DB indisponível ({exc}). Continuando sem banco.\n")
            db = None

    # --- Coleta ---
    scraper = ThreadsScraper(
        headless=HEADLESS,
        search_term=SEARCH_TERM or TARGET_USER,
    )

    urls: list[str] = []

    if SEARCH_TERM:
        urls = await scraper.search_posts(
            SEARCH_TERM, target=TARGET_POSTS, fresh=FRESH
        )

    if TARGET_USER:
        user_urls = await scraper.fetch_user_posts(TARGET_USER, target=TARGET_POSTS)
        existing  = set(urls)
        for u in user_urls:
            if u not in existing:
                urls.append(u)
                existing.add(u)

    if not urls:
        print("Nenhum post coletado.")
        if db:
            await db.finish_run(run_id, 0)
            await db.close()
        return

    results = [scraper.posts.get(u, {"url": u}) for u in urls]

    # --- Comentários ---
    if FETCH_COMMENTS and results:
        await scraper.fetch_comments_all(
            results,
            max_per_post=COMMENTS_PER_POST,
            concurrency=COMMENTS_CONCURRENCY,
        )

    # --- Persistência ---
    saved_to_db = 0
    if db and results:
        try:
            saved_to_db = await db.upsert_posts(
                results, SEARCH_TERM or f"@{TARGET_USER}", run_id
            )
            await db.finish_run(run_id, saved_to_db)
            print(f"  {saved_to_db} posts salvos no PostgreSQL.")
        except Exception as exc:
            print(f"  Erro ao salvar no banco: {exc}")
        finally:
            await db.close()

    # --- Resumo ---
    with_video     = sum(1 for r in results if r.get("video_url"))
    with_img       = sum(1 for r in results if r.get("thumbnail") and not r.get("video_url"))
    comments_total = sum(len(r.get("_comments") or []) for r in results)

    print(f"\n{'═' * 55}")
    print(f"  Plataforma      : Threads (Meta)")
    if SEARCH_TERM:
        print(f"  Termo de busca  : {SEARCH_TERM}")
    if TARGET_USER:
        print(f"  Usuário alvo    : @{TARGET_USER}")
    print(f"  Posts coletados : {len(results)}")
    print(f"  Com vídeo       : {with_video}")
    print(f"  Só imagem       : {with_img}")
    if FETCH_COMMENTS:
        print(f"  Comentários     : {comments_total}")
    if db:
        print(f"  Salvos no banco : {saved_to_db}")
    print(f"{'═' * 55}\n")


if __name__ == "__main__":
    asyncio.run(main())
