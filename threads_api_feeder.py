"""
Threads Feeder – Integração com GuaIA
======================================
Aguarda consultas pendentes do GuaIA, executa a coleta no Threads
usando o ThreadsScraper de collector.py e devolve os resultados
via sendQuerySingleResult.

Fluxo do feeder:
  1. Conecta à API GuaIA (config.yml)
  2. Busca consultas pendentes a cada 30 s
  3. Renova preventivamente o JWT periodicamente
  4. Para cada consulta com query_source == THREADS_QUERY_SOURCE:
       a. Marca "Em execução" (status 2)
       b. Extrai subject e max_results da consulta
       c. Executa ThreadsScraper.search_posts(subject, max_results)
       d. Envia cada post via sendQuerySingleResult
       e. Marca "Concluído" (status 3) ou "Falhou" (status 5)
  5. Aguarda 30 s e repete
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
import pyGuaIA_API
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

THREADS_QUERY_SOURCE = int(os.getenv("THREADS_QUERY_SOURCE", "8"))

FETCH_COMMENTS = os.getenv(
    "THREADS_FETCH_COMMENTS",
    os.getenv("FETCH_COMMENTS", "false"),
).lower() in ("1", "true", "yes", "on")

COMMENTS_PER_POST = int(
    os.getenv("COMMENTS_PER_POST", "50")
)

COMMENTS_CONCURRENCY = int(
    os.getenv("COMMENTS_CONCURRENCY", "3")
)

TOKEN_REFRESH_INTERVAL = int(
    os.getenv("TOKEN_REFRESH_INTERVAL", "600")
)

QUERY_POLL_INTERVAL = int(
    os.getenv("QUERY_POLL_INTERVAL", "30")
)

env_suffix = os.getenv("SUFFIX", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(
    f"threads_api_feeder{'_' + env_suffix if env_suffix else ''}"
)

logger.setLevel(logging.INFO)

_fmt = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s "
    "[%(funcName)s:%(lineno)d] - %(message)s",
    "%Y-%m-%d %H:%M:%S",
)

_fh = logging.FileHandler(
    f"threads_api_feeder{'_' + env_suffix if env_suffix else ''}.log"
)

_fh.setFormatter(_fmt)
logger.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)

# ---------------------------------------------------------------------------
# JWT / RECONEXÃO
# ---------------------------------------------------------------------------

def get_guaia_connection():
    """
    Cria conexão autenticada com a API GuaIA.

    Returns
    -------
    pyGuaIA_API.GuaIA_API or None
        Instância autenticada da API ou ``None`` em caso de falha.
    """
    try:
        api = pyGuaIA_API.GuaIA_API("config.yml")
        logger.info("Conexão à API do GuaIA realizada com sucesso!")
        return api

    except Exception:
        logger.exception("Erro ao conectar à API GuaIA")
        return None


def reconnect(api) -> bool:
    """
    Renova explicitamente o token JWT da conexão atual.

    Parameters
    ----------
    api : pyGuaIA_API.GuaIA_API
        Instância autenticada da API GuaIA.

    Returns
    -------
    bool
        ``True`` se a renovação ocorreu com sucesso.
    """
    try:
        ok = api.connect()

        if ok:
            logger.info("Token JWT renovado com sucesso")
        else:
            logger.warning(
                "Falha ao renovar token JWT | code=%s | reason=%s",
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )

        return ok

    except Exception:
        logger.exception("Erro ao renovar token JWT")
        return False


def is_auth_error(api) -> bool:
    """
    Verifica se o último erro retornado pelo GuaIA
    corresponde a falha de autenticação.

    Parameters
    ----------
    api : pyGuaIA_API.GuaIA_API

    Returns
    -------
    bool
    """
    return api.getLastReturnCode() in (401, 403)


# ---------------------------------------------------------------------------
# WRAPPERS RESILIENTES
# ---------------------------------------------------------------------------

def safe_get_unprocessed_queries(api):
    """
    Busca consultas pendentes com retry automático
    em caso de expiração do JWT.
    """
    try:
        ret, queries = api.getUnprocessedQueries()

        if not ret and is_auth_error(api):
            logger.warning(
                "JWT expirado ao buscar queries. Reconectando..."
            )

            if reconnect(api):
                ret, queries = api.getUnprocessedQueries()

        return ret, queries

    except ValueError as exc:
        logger.warning(
            "Resposta inválida da API GuaIA: %s",
            exc,
        )
        return False, []

    except requests.exceptions.ConnectionError as exc:
        logger.warning(
            "Erro de conexão com GuaIA: %s",
            exc,
        )
        return False, []

    except requests.exceptions.RequestException as exc:
        logger.warning(
            "Erro HTTP com GuaIA: %s",
            exc,
        )
        return False, []


def safe_set_query_status(api, id_query, status) -> bool:
    """
    Atualiza o status da consulta com retry automático
    em caso de expiração do JWT.
    """
    try:
        ret, _ = api.setQueryStatus(id_query, status)

        if not ret and is_auth_error(api):
            logger.warning(
                "JWT expirado ao alterar status da query %s. "
                "Reconectando...",
                id_query,
            )

            if reconnect(api):
                ret, _ = api.setQueryStatus(id_query, status)

        if not ret:
            logger.error(
                "Falha ao alterar status da query %s para %s "
                "| code=%s | reason=%s",
                id_query,
                status,
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )

        return ret

    except Exception:
        logger.exception(
            "Erro ao alterar status da query %s para %s",
            id_query,
            status,
        )
        return False


def safe_send_result(api, **kwargs) -> bool:
    """
    Envia um resultado ao GuaIA com retry automático
    em caso de expiração do JWT.
    """
    try:
        ret, _ = api.sendQuerySingleResult(**kwargs)

        if not ret and is_auth_error(api):
            logger.warning(
                "JWT expirado durante envio de resultado. "
                "Reconectando..."
            )

            if reconnect(api):
                ret, _ = api.sendQuerySingleResult(**kwargs)

        if not ret:
            logger.error(
                "Falha ao enviar resultado "
                "| code=%s | reason=%s",
                api.getLastReturnCode(),
                api.getLastReturnReason(),
            )

        return ret

    except Exception:
        logger.exception("Erro ao enviar resultado")
        return False


# ---------------------------------------------------------------------------
# FILTRO DE DATA
# ---------------------------------------------------------------------------

def parse_optional_datetime(raw, *, end_of_day: bool = False):
    if raw in (None, ""):
        return None

    raw_text = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
    raw_text = raw_text.strip()

    if not raw_text:
        return None

    date_only = (
        len(raw_text) == 10
        and raw_text[4] == "-"
        and raw_text[7] == "-"
    )
    candidate = raw_text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        logger.warning("Nao foi possivel parsear data '%s'", raw)
        return None

    if end_of_day and date_only:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed


def query_date_window(query: dict):
    return (
        parse_optional_datetime(query.get("start_date")),
        parse_optional_datetime(query.get("end_date"), end_of_day=True),
    )


def post_publication_datetime(post: dict):
    return parse_optional_datetime(
        post.get("created_at")
        or post.get("published_at")
        or post.get("data")
    )


def filter_posts_by_date_window(posts: list[dict], start_date, end_date) -> list[dict]:
    if not start_date and not end_date:
        return posts

    filtered = []
    missing_date = 0
    out_of_range = 0

    for post in posts:
        publication_datetime = post_publication_datetime(post)

        if publication_datetime is None:
            missing_date += 1
            continue

        if start_date and publication_datetime < start_date:
            out_of_range += 1
            continue

        if end_date and publication_datetime > end_date:
            out_of_range += 1
            continue

        filtered.append(post)

    logger.info(
        "Filtro de data aplicado | inicio=%s | fim=%s | antes=%s | depois=%s "
        "| sem_data=%s | fora_periodo=%s",
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
        len(posts),
        len(filtered),
        missing_date,
        out_of_range,
    )

    return filtered


# ---------------------------------------------------------------------------
# Threads – coleta
# ---------------------------------------------------------------------------

async def _init_db():
    """
    Inicializa conexão com PostgreSQL para persistência de comentários.

    Returns
    -------
    db.Database or None
    """
    pg_host = os.getenv("POSTGRES_HOST", "")

    if not pg_host:
        return None

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

        return db

    except Exception as exc:
        logger.warning(
            "DB indisponível para comentários (%s).",
            exc,
        )
        return None


async def run_threads_search(
    subject: str,
    max_results: int,
    id_query=None,
) -> list[dict]:
    """
    Executa o ThreadsScraper com o termo recebido do GuaIA.
    """
    if not subject or not subject.strip():
        logger.error(
            "run_threads_search chamado sem termo de busca. Abortando."
        )
        return []

    from scraper import ThreadsScraper

    headless = os.getenv(
        "HEADLESS",
        "true",
    ).lower() not in ("0", "false", "no")

    scraper = ThreadsScraper(
        headless=headless,
        search_term=subject,
    )

    urls = await scraper.search_posts(
        subject,
        target=max_results,
        fresh=True,
    )

    if not urls:
        return []

    posts = [
        scraper.posts.get(u, {"url": u})
        for u in urls
    ]

    if FETCH_COMMENTS:
        logger.info(
            "Iniciando coleta concorrente de comentários "
            "para %d posts "
            "(máx %d por post, concorrência %d).",
            len(posts),
            COMMENTS_PER_POST,
            COMMENTS_CONCURRENCY,
        )

        await scraper.fetch_comments_all(
            posts,
            max_per_post=COMMENTS_PER_POST,
            concurrency=COMMENTS_CONCURRENCY,
        )

        db = None
        if db:
            total_comments = 0

            for post in posts:
                post_id = post.get("post_id")

                comments = post.get("_comments") or []

                if post_id and comments:
                    try:
                        await db.upsert_posts(
                            [post],
                            subject,
                        )

                        total_comments += len(comments)

                    except Exception:
                        logger.exception(
                            "Erro ao persistir comentários do post %s",
                            post_id,
                        )

            logger.info(
                "Comentários persistidos no banco: %d",
                total_comments,
            )

            await db.close()

    try:
        from media_archiver import archive_posts_media

        posts = await asyncio.to_thread(
            archive_posts_media,
            posts,
            search_term=subject,
            id_query=id_query,
            logger=logger,
        )
    except Exception:
        logger.exception("Falha best-effort ao arquivar midias Threads")

    db = await _init_db()
    if db:
        try:
            run_id = await db.create_run(subject, max_results)
            saved = await db.upsert_posts(posts, subject, run_id)
            await db.finish_run(run_id, saved)
            logger.info("Threads PostgreSQL: %d posts salvos", saved)
        except Exception:
            logger.exception("Erro ao persistir posts Threads no banco")
        finally:
            await db.close()

    return posts


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

def create_threads_dto(post: dict) -> dict:
    """
    Constrói o social_metadata de um post do Threads.
    """
    comment_list = []

    for c in post.get("_comments") or []:
        comment_list.append({
            "author": c.get("author_name", ""),
            "author_id": c.get("author_id", ""),
            "text": c.get("content", ""),
            "score": c.get("likes", 0),
            "created_utc": (
                c["created_at"].isoformat()
                if c.get("created_at")
                else None
            ),
        })

    return {
        "post_id": post.get("post_id"),
        "code": post.get("code"),
        "platform": "threads",

        "author": post.get("author"),
        "author_id": post.get("author_id"),
        "author_name": post.get("author_name"),
        "is_verified": post.get("is_verified", False),
        "follower_count": post.get("follower_count", 0),

        "description": post.get("description"),
        "media_type": post.get("media_type"),
        "video_url": post.get("video_url"),
        "thumbnail": post.get("thumbnail"),
        "drive_file_id": post.get("drive_file_id"),
        "drive_url": post.get("drive_url"),
        "hashtags": post.get("hashtags", []),
        "mentions": post.get("mentions", []),
        "external_url": post.get("external_url", ""),

        "likes": post.get("likes", 0),
        "reply_count": post.get("replies", 0),
        "reposts": post.get("reposts", 0),
        "quotes": post.get("quotes", 0),

        "comments": comment_list,
    }


# ---------------------------------------------------------------------------
# PROCESSAMENTO
# ---------------------------------------------------------------------------

def process_query(api, query: dict) -> None:
    """
    Executa o processamento completo de uma query do Threads.
    """
    id_query = query.get("id_query")

    logger.info(
        "Processando consulta Threads ID (GuaIA): %s",
        id_query,
    )

    if not safe_set_query_status(api, id_query, 2):
        return

    subject = query.get("subject", "").strip()

    if not subject:
        logger.error(
            "Consulta %s sem termo de busca "
            "(subject vazio). Ignorando.",
            id_query,
        )

        safe_set_query_status(api, id_query, 5)

        return

    max_results = query.get("max_results", 200)
    start_date, end_date = query_date_window(query)

    logger.info(
        "Termo recebido do GuaIA: '%s' | max_results=%s | start_date=%s | end_date=%s",
        subject,
        max_results,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
    )

    try:
        posts = asyncio.run(
            run_threads_search(subject, max_results, id_query=id_query)
        )

    except Exception:
        logger.exception(
            "Falha ao executar busca Threads da consulta %s",
            id_query,
        )

        safe_set_query_status(api, id_query, 5)

        return

    posts = filter_posts_by_date_window(posts, start_date, end_date)

    if not posts:
        logger.warning(
            "Nenhum post coletado dentro do periodo para a consulta %s",
            id_query,
        )

        safe_set_query_status(api, id_query, 4)

        return

    logger.info(
        "Encontrados %d posts para a consulta %s",
        len(posts),
        id_query,
    )

    enviados = 0
    falhas = 0

    for post in posts:
        try:
            threads_dto = create_threads_dto(post)

            threads_dto["busca_id_id"] = str(id_query)

            url = post.get("url", "")
            content = post.get("description", "")
            author = post.get("author", "")

            raw_date = post.get("created_at", "")

            try:
                publication_date = (
                    datetime.fromisoformat(
                        raw_date.replace("Z", "+00:00")
                    )
                    if raw_date
                    else datetime.now(tz=timezone.utc)
                )

            except Exception:
                publication_date = datetime.now(
                    tz=timezone.utc
                )

            ok = safe_send_result(
                api,
                id_query=id_query,
                publication_date=publication_date,
                publication_text=content,
                source_type=THREADS_QUERY_SOURCE,
                other_source_name="Threads",
                primary_source_name=author,
                publication_url=url,
                allegation="",
                comment="",
                observation="",
                id_target_type="1,5",
                social_metadata=threads_dto,
            )

            if ok:
                enviados += 1
            else:
                falhas += 1

        except Exception:
            logger.exception(
                "Erro ao processar post %s",
                post.get("url"),
            )

            falhas += 1

    logger.info(
        "Envio concluído | query=%s | enviados=%s | falhas=%s",
        id_query,
        enviados,
        falhas,
    )

    if falhas == 0:
        safe_set_query_status(api, id_query, 3)

        logger.info(
            "Consulta %s concluída com sucesso.",
            id_query,
        )

    else:
        safe_set_query_status(api, id_query, 4)

        logger.warning(
            "Consulta %s concluída com falhas.",
            id_query,
        )


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Loop principal do feeder Threads.
    """
    logger.info(
        "Iniciando a integração com a API GuaIA para Threads"
    )

    api = get_guaia_connection()

    if not api:
        sys.exit(1)

    last_token_refresh = time.time()

    while True:
        try:
            # ---------------------------------------------------------------
            # REFRESH PREVENTIVO DO JWT
            # ---------------------------------------------------------------

            if (
                time.time() - last_token_refresh
                >= TOKEN_REFRESH_INTERVAL
            ):
                logger.info(
                    "Renovando JWT preventivamente..."
                )

                reconnect(api)

                last_token_refresh = time.time()

            # ---------------------------------------------------------------
            # BUSCA QUERIES
            # ---------------------------------------------------------------

            logger.info(
                "Buscando consultas não processadas..."
            )

            ret, list_queries = safe_get_unprocessed_queries(api)

            if not ret:
                logger.warning(
                    "Falha ao buscar queries pendentes."
                )

                time.sleep(QUERY_POLL_INTERVAL)

                continue

            if not list_queries:
                logger.info(
                    "Nenhuma consulta pendente encontrada."
                )

                time.sleep(QUERY_POLL_INTERVAL)

                continue

            logger.info(
                "Total de queries pendentes: %d",
                len(list_queries),
            )

            # ---------------------------------------------------------------
            # PROCESSAMENTO
            # ---------------------------------------------------------------

            for query in list_queries:
                query_source = query.get("query_source")

                try:
                    if int(query_source) != THREADS_QUERY_SOURCE:
                        continue

                except (TypeError, ValueError):
                    logger.warning(
                        "query_source inválido na query %s: %s",
                        query.get("id_query"),
                        query_source,
                    )
                    continue

                try:
                    process_query(api, query)

                except requests.exceptions.ConnectionError:
                    logger.exception(
                        "Erro de rede processando query %s",
                        query.get("id_query"),
                    )

                    safe_set_query_status(
                        api,
                        query.get("id_query"),
                        5,
                    )

                    time.sleep(60)

                except Exception:
                    logger.exception(
                        "Erro processando query %s",
                        query.get("id_query"),
                    )

                    safe_set_query_status(
                        api,
                        query.get("id_query"),
                        5,
                    )

            time.sleep(QUERY_POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info(
                "Encerrando integração do Threads com GuaIA..."
            )
            break

        except Exception:
            logger.exception("Erro geral no loop principal")

            time.sleep(30)


if __name__ == "__main__":
    main()
