"""
Funções puras de parsing para o extrator de metadados do Threads.

Todas as funções deste módulo são *stateless*: recebem estruturas de dados
brutas (dicts/listas) e retornam dados no schema interno do coletor, sem
produzir efeitos colaterais nem realizar I/O.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Extração de tokens da página HTML
# ---------------------------------------------------------------------------

def extract_tokens_from_html(html: str) -> dict[str, str]:
    """
    Extrai ``lsd``, ``fb_dtsg`` e ``app_id`` do HTML do Threads.

    Aplica múltiplas estratégias de regex cobrindo os diferentes formatos
    do bundler do Meta (ScheduledServerJS, JSON inline, input hidden).

    Parameters
    ----------
    html : str
        Conteúdo HTML bruto da página do Threads.

    Returns
    -------
    dict[str, str]
        Dicionário com as chaves ``"lsd"``, ``"fb_dtsg"`` e/ou ``"app_id"``
        encontradas. Chaves ausentes não são incluídas.
    """
    tokens: dict[str, str] = {}

    lsd_patterns = [
        r'"LSD"[^}]*"token"\s*:\s*"([^"]+)"',
        r'"lsd"\s*:\s*\{"token"\s*:\s*"([^"]+)"',
        r'"LSD"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"',
        r'"LSD"\s*,\s*\$\$\s*,\s*\{"token"\s*:\s*"([^"]+)"',
        r'name="lsd"\s+value="([^"]+)"',
        r'"token":"([^"]{10,})"[^}]*"LSD"',
    ]
    for pat in lsd_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            tokens["lsd"] = m.group(1)
            break

    dtsg_patterns = [
        r'"DTSGInitialData"[^}]*"token"\s*:\s*"([^"]+)"',
        r'"dtsg"\s*:\s*\{"token"\s*:\s*"([^"]+)"',
        r'"DTSGInitialData"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"',
        r'"DTSGInitialData"\s*,\s*\$\$\s*,\s*\{"token"\s*:\s*"([^"]+)"',
        r'name="fb_dtsg"\s+value="([^"]+)"',
    ]
    for pat in dtsg_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            tokens["fb_dtsg"] = m.group(1)
            break

    m = re.search(r'"APP_ID"\s*:\s*"?(\d{10,})"?', html)
    if m:
        tokens["app_id"] = m.group(1)

    return tokens


# ---------------------------------------------------------------------------
# Busca recursiva em estruturas aninhadas
# ---------------------------------------------------------------------------

def nested_lookup(key: str, document: Any) -> list:
    """
    Busca recursiva de uma chave em dicts/listas aninhadas.

    Parameters
    ----------
    key : str
        Chave a ser buscada em todos os níveis da estrutura.
    document : Any
        Estrutura de dados a percorrer (dict, list ou valor escalar).

    Returns
    -------
    list
        Lista com todos os valores encontrados para ``key`` em qualquer
        profundidade da estrutura.
    """
    results: list = []
    if isinstance(document, list):
        for item in document:
            results.extend(nested_lookup(key, item))
    elif isinstance(document, dict):
        for k, v in document.items():
            if k == key:
                results.append(v)
            results.extend(nested_lookup(key, v))
    return results


def find_any_cursor(document: Any) -> str:
    """
    Busca recursivamente um ``page_info.end_cursor`` com ``has_next_page=True``.

    Parameters
    ----------
    document : Any
        Estrutura de dados SSR ou resposta GraphQL a percorrer.

    Returns
    -------
    str
        Cursor encontrado ou string vazia se não houver próxima página.
    """
    if isinstance(document, dict):
        pi = document.get("page_info")
        if isinstance(pi, dict):
            cur = pi.get("end_cursor")
            if cur and pi.get("has_next_page"):
                return cur
        for v in document.values():
            r = find_any_cursor(v)
            if r:
                return r
    elif isinstance(document, list):
        for item in document:
            r = find_any_cursor(item)
            if r:
                return r
    return ""


# ---------------------------------------------------------------------------
# Extração de posts de dados SSR
# ---------------------------------------------------------------------------

def extract_posts_from_sjs(data: Any) -> list[dict]:
    """
    Extrai e parseia posts de dados ScheduledServerJS (``data-sjs``).

    Usa ``nested_lookup`` para encontrar todos os ``thread_items`` em
    qualquer profundidade da estrutura, convertendo cada um para o schema
    interno via :func:`parse_thread_item`.

    Parameters
    ----------
    data : Any
        Dados JSON do bloco ``<script data-sjs>`` ou de uma resposta
        GraphQL intercedada pelo browser.

    Returns
    -------
    list[dict]
        Lista de posts no schema interno. Posts inválidos são ignorados.
    """
    posts: list[dict] = []
    all_thread_items = nested_lookup("thread_items", data)
    for thread_list in all_thread_items:
        if not isinstance(thread_list, list):
            continue
        for item in thread_list:
            post = parse_thread_item(item)
            if post:
                posts.append(post)
    return posts


def extract_hidden_data(html: str) -> tuple[list[dict], str, dict[str, str]]:
    """
    Extrai posts, cursor e tokens dos blocos ``<script data-sjs>`` do HTML.

    O Threads embute os dados ocultos (hidden web data) em elementos
    ``<script type="application/json" data-sjs>`` que o React usa para
    hidratar a página.

    Parameters
    ----------
    html : str
        HTML completo da página renderizada pelo browser.

    Returns
    -------
    posts : list[dict]
        Posts encontrados nos blocos SSR.
    cursor : str
        Último cursor de paginação encontrado (ou string vazia).
    tokens : dict[str, str]
        Tokens de autenticação encontrados no HTML (``lsd``, ``fb_dtsg``).
    """
    pattern = (
        r'<script[^>]*type="application/json"[^>]*data-sjs[^>]*>'
        r"(.*?)</script>"
    )
    all_posts: list[dict] = []
    latest_cursor: str = ""

    for match in re.finditer(pattern, html, re.DOTALL):
        raw = match.group(1).strip()
        if not raw or '"ScheduledServerJS"' not in raw:
            continue
        if "thread_items" not in raw and "post" not in raw:
            continue
        try:
            data = json.loads(raw)
            all_posts.extend(extract_posts_from_sjs(data))
            c = find_any_cursor(data)
            if c:
                latest_cursor = c
        except Exception:
            pass

    tokens = extract_tokens_from_html(html)
    return all_posts, latest_cursor, tokens


# ---------------------------------------------------------------------------
# Parsing de posts individuais
# ---------------------------------------------------------------------------

def parse_thread_item(node: dict) -> dict | None:
    """
    Converte um nó GraphQL do Threads para o schema interno de posts.

    A estrutura do GraphQL do Threads pode variar:
    - ``node → thread_items[] → post``
    - ``node → post``
    - ``node`` diretamente como post

    Parameters
    ----------
    node : dict
        Nó bruto retornado pelo GraphQL ou extraído dos dados SSR.

    Returns
    -------
    dict or None
        Post no schema interno, ou ``None`` se o nó for inválido.
    """
    if not isinstance(node, dict):
        return None

    thread_items = node.get("thread_items") or []
    if thread_items:
        post_node = thread_items[0].get("post") if thread_items else None
    else:
        post_node = node.get("post") or node

    if not post_node:
        return None

    post_id = post_node.get("pk") or post_node.get("id")
    if not post_id:
        return None

    user        = post_node.get("user") or {}
    author      = user.get("username") or user.get("full_name") or "Desconhecido"
    author_id   = str(user.get("pk") or user.get("id") or "")
    author_name = user.get("full_name") or author
    is_verified    = bool(user.get("is_verified"))
    follower_count = int(user.get("follower_count") or 0)

    caption_node = post_node.get("caption") or {}
    if isinstance(caption_node, dict):
        description = caption_node.get("text") or ""
    else:
        description = str(caption_node or "")

    media_type = post_node.get("media_type")
    thumbnail  = ""
    video_url  = ""

    image_versions = post_node.get("image_versions2") or {}
    candidates = image_versions.get("candidates") or []
    if candidates:
        best = max(
            candidates,
            key=lambda c: (c.get("width", 0) * c.get("height", 0)),
            default={},
        )
        thumbnail = best.get("url", "")

    video_versions = post_node.get("video_versions") or []
    if video_versions:
        video_url = video_versions[0].get("url", "")

    carousel = post_node.get("carousel_media") or []
    if carousel and not thumbnail:
        first_media = carousel[0]
        imgs = (first_media.get("image_versions2") or {}).get("candidates") or []
        if imgs:
            thumbnail = imgs[0].get("url", "")

    like_count   = int(post_node.get("like_count") or 0)
    reply_count  = int(
        post_node.get("text_post_app_info", {}).get("direct_reply_count")
        or post_node.get("reply_count")
        or 0
    )
    repost_count = int(
        post_node.get("text_post_app_info", {}).get("repost_count") or 0
    )
    quote_count  = int(
        post_node.get("text_post_app_info", {}).get("quote_count") or 0
    )

    raw_ts     = post_node.get("taken_at") or post_node.get("device_timestamp")
    created_at = ""
    if raw_ts:
        try:
            ts = int(raw_ts)
            if ts > 1e10:
                ts = ts // 1000
            created_at = datetime.fromtimestamp(
                ts, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    code = post_node.get("code") or ""
    url  = (
        f"https://www.threads.net/@{author}/post/{code}"
        if code
        else f"https://www.threads.net/t/{post_id}"
    )

    hashtags    = re.findall(r"#(\w+)", description)
    mentions    = re.findall(r"@(\w+)", description)
    link_preview = (
        post_node.get("text_post_app_info", {}).get("link_preview_attachment")
        or {}
    )
    external_url = link_preview.get("url") or ""

    return {
        "url":            url,
        "post_id":        str(post_id),
        "code":           code,
        "author":         author,
        "author_id":      author_id,
        "author_name":    author_name,
        "is_verified":    is_verified,
        "follower_count": follower_count,
        "description":    description.strip(),
        "media_type":     media_type,
        "video_url":      video_url,
        "thumbnail":      thumbnail,
        "likes":          like_count,
        "replies":        reply_count,
        "reposts":        repost_count,
        "quotes":         quote_count,
        "hashtags":       hashtags,
        "mentions":       mentions,
        "external_url":   external_url,
        "created_at":     created_at,
        "platform":       "threads",
    }


# ---------------------------------------------------------------------------
# Parsing de replies (comentários)
# ---------------------------------------------------------------------------

def parse_reply(item: dict) -> dict | None:
    """
    Converte um item de reply para o schema interno de comentários.

    Replies no Threads têm estrutura similar a posts normais.
    Aceita tanto edges com ``node`` quanto ``thread_item`` com ``post``.

    Parameters
    ----------
    item : dict
        Item de reply bruto vindo da resposta GraphQL.

    Returns
    -------
    dict or None
        Comentário no schema interno, ou ``None`` se o item for inválido.
    """
    if not isinstance(item, dict):
        return None

    node = item.get("node") or item
    post = node.get("post") or node

    comment_id = post.get("pk") or post.get("id")
    if not comment_id:
        return None

    user        = post.get("user") or {}
    author_name = user.get("username") or user.get("full_name") or ""
    author_id   = str(user.get("pk") or user.get("id") or "")

    caption = post.get("caption") or {}
    content = (
        caption.get("text")
        if isinstance(caption, dict)
        else str(caption or "")
    )

    like_count = int(post.get("like_count") or 0)

    raw_ts     = post.get("taken_at") or post.get("device_timestamp")
    created_at = None
    if raw_ts:
        try:
            ts = int(raw_ts)
            if ts > 1e10:
                ts = ts // 1000
            created_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

    return {
        "comment_id":  str(comment_id),
        "author_name": str(author_name).strip(),
        "author_id":   author_id,
        "content":     str(content or "").strip(),
        "likes":       like_count,
        "created_at":  created_at,
    }


# ---------------------------------------------------------------------------
# Extração de edges e cursores de respostas GraphQL
# ---------------------------------------------------------------------------

def extract_search_edges(data: dict) -> tuple[list, str, bool]:
    """
    Navega na resposta GraphQL do Threads e extrai edges de busca.

    Tenta múltiplos caminhos conhecidos na estrutura de resposta, pois o
    Threads altera o formato com frequência. Se nenhum caminho funcionar,
    aplica busca recursiva via :func:`deep_find_edges`.

    Parameters
    ----------
    data : dict
        Resposta JSON bruta do endpoint GraphQL.

    Returns
    -------
    edges : list
        Lista de nós de posts encontrados.
    cursor : str
        Cursor para a próxima página (vazio se não houver).
    has_next : bool
        ``True`` se houver mais páginas disponíveis.
    """
    if not isinstance(data, dict):
        return [], "", False

    SEARCH_PATHS = [
        ["data", "searchResults"],
        ["data", "search_results"],
        ["data", "xdt_api__v1__fbid__media_info__connection"],
        ["data", "data", "searchResults"],
    ]

    result_node = None
    for path in SEARCH_PATHS:
        node = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node:
            result_node = node
            break

    if result_node is None:
        return deep_find_edges(data), "", False

    raw_edges = result_node.get("edges") or []
    edges     = raw_edges if isinstance(raw_edges, list) else []
    page_info = result_node.get("page_info") or {}
    has_next  = bool(page_info.get("has_next_page"))
    cursor    = page_info.get("end_cursor") or ""

    if not cursor:
        cursor = (
            data.get("data", {}).get("page_info", {}).get("end_cursor")
            or data.get("page_info", {}).get("end_cursor")
            or ""
        )

    return edges, cursor, has_next


def extract_user_edges(data: dict) -> tuple[list, str, bool]:
    """
    Extrai edges da resposta GraphQL de posts de um usuário específico.

    Parameters
    ----------
    data : dict
        Resposta JSON bruta do endpoint de perfil/timeline do GraphQL.

    Returns
    -------
    edges : list
        Lista de nós de posts do usuário.
    cursor : str
        Cursor para a próxima página (vazio se não houver).
    has_next : bool
        ``True`` se houver mais páginas disponíveis.
    """
    PATHS = [
        ["data", "mediaData"],
        ["data", "userData", "user", "threads"],
        ["data", "xdt_api__v1__feed__user_timeline_graphql_connection"],
    ]

    for path in PATHS:
        node = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node and isinstance(node, dict):
            edges    = node.get("edges") or []
            pi       = node.get("page_info") or {}
            has_next = bool(pi.get("has_next_page"))
            cursor   = pi.get("end_cursor") or ""
            if edges:
                return edges, cursor, has_next

    return [], "", False


def extract_replies(data: dict) -> tuple[list, str, bool]:
    """
    Extrai lista de replies da resposta GraphQL de comentários.

    Parameters
    ----------
    data : dict
        Resposta JSON bruta do endpoint de replies do GraphQL.

    Returns
    -------
    replies : list
        Lista de itens de reply (thread_items ou edges).
    cursor : str
        Cursor para a próxima página (vazio se não houver).
    has_next : bool
        ``True`` se houver mais páginas disponíveis.
    """
    PATHS = [
        ["data", "data", "containing_thread", "thread_items"],
        ["data", "containing_thread", "thread_items"],
        ["data", "replies"],
        ["data", "xdt_api__v1__media__media_id__comments__connection"],
    ]

    for path in PATHS:
        node = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)

        if node:
            if isinstance(node, list):
                return node, "", False
            if isinstance(node, dict):
                edges    = node.get("edges") or []
                pi       = node.get("page_info") or {}
                has_next = bool(pi.get("has_next_page"))
                cursor   = pi.get("end_cursor") or ""
                return edges, cursor, has_next

    return [], "", False


def deep_find_edges(data: dict, depth: int = 0) -> list:
    """
    Busca recursiva por listas de edges em qualquer nível da resposta.

    Usado como fallback quando a estrutura GraphQL muda e os caminhos
    conhecidos em :func:`extract_search_edges` não encontram nada.

    Parameters
    ----------
    data : dict
        Estrutura de dados a percorrer recursivamente.
    depth : int, optional
        Profundidade atual da recursão. Limita-se a 5 níveis para evitar
        recursão infinita em estruturas muito profundas. Padrão: 0.

    Returns
    -------
    list
        Primeira lista de edges encontrada, ou lista vazia.
    """
    if depth > 5 or not isinstance(data, dict):
        return []

    if "edges" in data and isinstance(data["edges"], list):
        return data["edges"]

    for value in data.values():
        if isinstance(value, dict):
            result = deep_find_edges(value, depth + 1)
            if result:
                return result
        elif isinstance(value, list) and value:
            if isinstance(value[0], dict) and (
                value[0].get("pk") or value[0].get("post")
            ):
                return [{"node": item} for item in value]

    return []
