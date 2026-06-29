import mimetypes
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

import google_drive


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_slug(value: str, default: str = "sem-termo") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:80] or default


def _media_url(post: dict) -> tuple[str, str]:
    if post.get("video_url"):
        return post["video_url"], "video"
    if post.get("thumbnail"):
        return post["thumbnail"], "image"
    return "", ""


def _mimetype(media_url: str, content_type: str = "") -> str:
    if content_type:
        return content_type.split(";")[0].strip()
    guessed, _ = mimetypes.guess_type(urlparse(media_url).path)
    return guessed or "application/octet-stream"


def _extension(media_url: str, mimetype: str) -> str:
    extension = mimetypes.guess_extension(mimetype) or ""
    if extension:
        return extension
    suffix = os.path.splitext(urlparse(media_url).path)[1]
    return suffix if len(suffix) <= 8 else ""


def _publication_date(post: dict) -> str:
    raw = post.get("created_at") or post.get("published_at") or post.get("data")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _download(media_url: str) -> tuple[bytes, str]:
    timeout = int(os.getenv("THREADS_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "60"))
    max_bytes = int(os.getenv("THREADS_MEDIA_MAX_BYTES", str(250 * 1024 * 1024)))
    response = requests.get(media_url, stream=True, timeout=timeout)
    response.raise_for_status()
    mimetype = _mimetype(media_url, response.headers.get("Content-Type", ""))
    chunks = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise RuntimeError("threads_media_max_bytes_exceeded")
    return bytes(chunks), mimetype


def archive_posts_media(posts: list[dict], *, search_term: str, id_query=None, logger=None) -> list[dict]:
    if not _env_bool("THREADS_DOWNLOAD_MEDIA", False):
        return posts
    if not google_drive.is_configured():
        if logger:
            logger.warning("Google Drive nao configurado para arquivamento Threads")
        return posts

    for post in posts:
        media_url, media_type = _media_url(post)
        if not media_url:
            continue
        try:
            data, mimetype = _download(media_url)
            post_id = post.get("post_id") or post.get("code") or _safe_slug(post.get("url", ""), "post")
            filename = f"threads_{_safe_slug(str(post_id), 'post')}_{media_type}{_extension(media_url, mimetype)}"
            folder_path = [
                "threads",
                f"query_{id_query or 'manual'}_{_safe_slug(search_term)}",
                _publication_date(post),
                f"post_{_safe_slug(str(post_id), 'sem-id')}",
                media_type,
            ]
            file_id, drive_url = google_drive.upload_bytes(
                data,
                filename,
                mimetype,
                folder_path=folder_path,
            )
            post["drive_file_id"] = file_id
            post["drive_url"] = drive_url
        except Exception as exc:
            if logger:
                logger.warning("Falha ao arquivar midia Threads %s: %s", media_url, exc)
    return posts
