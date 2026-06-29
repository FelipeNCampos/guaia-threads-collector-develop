"""
Database layer – Threads Collector
===================================
Schema PostgreSQL compatível com os campos específicos do Threads.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import asyncpg


# ---------------------------------------------------------------------------
# DSN builder
# ---------------------------------------------------------------------------

def build_dsn(
    host: str,
    port: Any = 5432,
    user: str = "threads",
    password: str = "",
    dbname: str = "threads_db",
) -> str:
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL = """
-- Tabela de runs (execuções de coleta)
CREATE TABLE IF NOT EXISTS collection_runs (
    id              SERIAL PRIMARY KEY,
    search_term     TEXT        NOT NULL,
    target_posts    INTEGER     NOT NULL DEFAULT 0,
    collected_posts INTEGER     NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
);

-- Tabela principal de posts
CREATE TABLE IF NOT EXISTS threads_posts (
    id              SERIAL PRIMARY KEY,

    -- Identificação
    post_id         TEXT        NOT NULL UNIQUE,
    code            TEXT,
    url             TEXT        NOT NULL,
    platform        TEXT        NOT NULL DEFAULT 'threads',

    -- Autor
    author          TEXT,
    author_id       TEXT,
    author_name     TEXT,
    is_verified     BOOLEAN     DEFAULT FALSE,
    follower_count  INTEGER     DEFAULT 0,

    -- Conteúdo
    description     TEXT,
    media_type      INTEGER,
    video_url       TEXT,
    thumbnail       TEXT,
    drive_file_id   TEXT,
    drive_url       TEXT,
    hashtags        TEXT[],
    mentions        TEXT[],
    external_url    TEXT,

    -- Métricas
    likes           INTEGER     DEFAULT 0,
    replies         INTEGER     DEFAULT 0,
    reposts         INTEGER     DEFAULT 0,
    quotes          INTEGER     DEFAULT 0,

    -- Metadados de coleta
    search_term     TEXT,
    run_id          INTEGER     REFERENCES collection_runs(id),
    created_at      TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tabela de comentários / replies
CREATE TABLE IF NOT EXISTS threads_comments (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT        NOT NULL REFERENCES threads_posts(post_id)
                                ON DELETE CASCADE,
    comment_id      TEXT        NOT NULL UNIQUE,
    author_name     TEXT,
    author_id       TEXT,
    content         TEXT,
    likes           INTEGER     DEFAULT 0,
    created_at      TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_threads_posts_search_term
    ON threads_posts(search_term);

CREATE INDEX IF NOT EXISTS idx_threads_posts_author
    ON threads_posts(author);

CREATE INDEX IF NOT EXISTS idx_threads_posts_created_at
    ON threads_posts(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_threads_posts_run_id
    ON threads_posts(run_id);

CREATE INDEX IF NOT EXISTS idx_threads_comments_post_id
    ON threads_comments(post_id);

CREATE INDEX IF NOT EXISTS idx_threads_posts_hashtags
    ON threads_posts USING GIN(hashtags);

CREATE INDEX IF NOT EXISTS idx_threads_posts_mentions
    ON threads_posts USING GIN(mentions);
"""

MIGRATIONS = """
ALTER TABLE threads_posts ADD COLUMN IF NOT EXISTS drive_file_id TEXT;
ALTER TABLE threads_posts ADD COLUMN IF NOT EXISTS drive_url TEXT;
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, dsn: str):
        self._dsn  = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        """Cria pool de conexões e aplica DDL."""
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=5,
            command_timeout=60,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(DDL)
            await conn.execute(MIGRATIONS)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def create_run(self, search_term: str, target: int) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO collection_runs (search_term, target_posts)
                VALUES ($1, $2)
                RETURNING id
                """,
                search_term,
                target,
            )
            return row["id"]

    async def finish_run(self, run_id: int, collected: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE collection_runs
                SET collected_posts = $1,
                    finished_at     = NOW(),
                    status          = 'done'
                WHERE id = $2
                """,
                collected,
                run_id,
            )

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def upsert_posts(
        self,
        posts: list[dict],
        search_term: str,
        run_id: int | None = None,
    ) -> int:
        """
        Insere ou atualiza posts no banco.
        Retorna o número de posts efetivamente salvos.
        """
        if not posts:
            return 0

        saved = 0
        async with self._pool.acquire() as conn:
            for post in posts:
                post_id = post.get("post_id")
                if not post_id:
                    continue

                # Parse de created_at
                created_at = None
                raw_ts = post.get("created_at")
                if raw_ts:
                    try:
                        if isinstance(raw_ts, str):
                            created_at = datetime.fromisoformat(
                                raw_ts.replace("Z", "+00:00")
                            )
                        elif isinstance(raw_ts, datetime):
                            created_at = raw_ts
                    except Exception:
                        pass

                try:
                    await conn.execute(
                        """
                        INSERT INTO threads_posts (
                            post_id, code, url, platform,
                            author, author_id, author_name,
                            is_verified, follower_count,
                            description, media_type,
                            video_url, thumbnail,
                            drive_file_id, drive_url,
                            hashtags, mentions, external_url,
                            likes, replies, reposts, quotes,
                            search_term, run_id, created_at
                        ) VALUES (
                            $1,  $2,  $3,  $4,
                            $5,  $6,  $7,
                            $8,  $9,
                            $10, $11,
                            $12, $13,
                            $14, $15,
                            $16, $17, $18,
                            $19, $20, $21, $22,
                            $23, $24, $25
                        )
                        ON CONFLICT (post_id) DO UPDATE SET
                            likes          = EXCLUDED.likes,
                            replies        = EXCLUDED.replies,
                            reposts        = EXCLUDED.reposts,
                            quotes         = EXCLUDED.quotes,
                            follower_count = EXCLUDED.follower_count,
                            description    = EXCLUDED.description,
                            drive_file_id  = COALESCE(EXCLUDED.drive_file_id, threads_posts.drive_file_id),
                            drive_url      = COALESCE(EXCLUDED.drive_url, threads_posts.drive_url),
                            hashtags       = EXCLUDED.hashtags,
                            mentions       = EXCLUDED.mentions,
                            updated_at     = NOW()
                        """,
                        post_id,
                        post.get("code") or "",
                        post.get("url") or "",
                        post.get("platform") or "threads",
                        post.get("author") or "",
                        post.get("author_id") or "",
                        post.get("author_name") or "",
                        bool(post.get("is_verified")),
                        int(post.get("follower_count") or 0),
                        post.get("description") or "",
                        post.get("media_type"),
                        post.get("video_url") or "",
                        post.get("thumbnail") or "",
                        post.get("drive_file_id") or "",
                        post.get("drive_url") or "",
                        post.get("hashtags") or [],
                        post.get("mentions") or [],
                        post.get("external_url") or "",
                        int(post.get("likes") or 0),
                        int(post.get("replies") or 0),
                        int(post.get("reposts") or 0),
                        int(post.get("quotes") or 0),
                        search_term,
                        run_id,
                        created_at,
                    )
                    saved += 1

                    # Salva comentários se existirem
                    comments = post.get("_comments") or []
                    if comments:
                        await self._upsert_comments(conn, post_id, comments)

                except Exception as exc:
                    print(f"    DB erro em {post_id}: {exc}")

        return saved

    async def _upsert_comments(
        self,
        conn: asyncpg.Connection,
        post_id: str,
        comments: list[dict],
    ) -> None:
        """Insere ou ignora comentários duplicados."""
        for c in comments:
            comment_id = c.get("comment_id")
            if not comment_id:
                continue
            try:
                await conn.execute(
                    """
                    INSERT INTO threads_comments (
                        post_id, comment_id,
                        author_name, author_id,
                        content, likes, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (comment_id) DO NOTHING
                    """,
                    post_id,
                    comment_id,
                    c.get("author_name") or "",
                    c.get("author_id") or "",
                    c.get("content") or "",
                    int(c.get("likes") or 0),
                    c.get("created_at"),
                )
            except Exception as exc:
                print(f"    DB erro comentário {comment_id}: {exc}")

    # ------------------------------------------------------------------
    # Queries utilitárias
    # ------------------------------------------------------------------

    async def get_posts_by_term(
        self,
        search_term: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Retorna posts de um termo de busca específico."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM threads_posts
                WHERE search_term = $1
                ORDER BY created_at DESC NULLS LAST
                LIMIT $2 OFFSET $3
                """,
                search_term,
                limit,
                offset,
            )
            return [dict(r) for r in rows]

    async def get_posts_by_author(
        self,
        author: str,
        limit: int = 100,
    ) -> list[dict]:
        """Retorna posts de um autor específico."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM threads_posts
                WHERE author = $1
                ORDER BY created_at DESC NULLS LAST
                LIMIT $2
                """,
                author,
                limit,
            )
            return [dict(r) for r in rows]

    async def get_comments_by_post(
        self,
        post_id: str,
    ) -> list[dict]:
        """Retorna todos os comentários de um post."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM threads_comments
                WHERE post_id = $1
                ORDER BY created_at ASC NULLS LAST
                """,
                post_id,
            )
            return [dict(r) for r in rows]

    async def get_run_stats(self, run_id: int) -> dict | None:
        """Retorna estatísticas de uma run específica."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    r.*,
                    COUNT(p.id)         AS posts_in_db,
                    SUM(p.likes)        AS total_likes,
                    SUM(p.replies)      AS total_replies,
                    SUM(p.reposts)      AS total_reposts,
                    AVG(p.follower_count)::BIGINT AS avg_followers
                FROM collection_runs r
                LEFT JOIN threads_posts p ON p.run_id = r.id
                WHERE r.id = $1
                GROUP BY r.id
                """,
                run_id,
            )
            return dict(row) if row else None

    async def search_posts_fulltext(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict]:
        """Busca full-text nos posts já coletados."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *,
                       ts_rank(
                           to_tsvector('portuguese', COALESCE(description, '')),
                           plainto_tsquery('portuguese', $1)
                       ) AS rank
                FROM threads_posts
                WHERE to_tsvector('portuguese', COALESCE(description, ''))
                      @@ plainto_tsquery('portuguese', $1)
                ORDER BY rank DESC, created_at DESC NULLS LAST
                LIMIT $2
                """,
                query,
                limit,
            )
            return [dict(r) for r in rows]

    async def get_top_hashtags(
        self,
        search_term: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Retorna as hashtags mais frequentes nos posts coletados."""
        async with self._pool.acquire() as conn:
            if search_term:
                rows = await conn.fetch(
                    """
                    SELECT
                        LOWER(tag) AS hashtag,
                        COUNT(*)   AS occurrences
                    FROM threads_posts,
                         UNNEST(hashtags) AS tag
                    WHERE search_term = $1
                    GROUP BY LOWER(tag)
                    ORDER BY occurrences DESC
                    LIMIT $2
                    """,
                    search_term,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        LOWER(tag) AS hashtag,
                        COUNT(*)   AS occurrences
                    FROM threads_posts,
                         UNNEST(hashtags) AS tag
                    GROUP BY LOWER(tag)
                    ORDER BY occurrences DESC
                    LIMIT $1
                    """,
                    limit,
                )
            return [dict(r) for r in rows]

    async def get_top_authors(
        self,
        search_term: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Retorna os autores com mais posts coletados."""
        async with self._pool.acquire() as conn:
            if search_term:
                rows = await conn.fetch(
                    """
                    SELECT
                        author,
                        author_name,
                        is_verified,
                        COUNT(*)         AS post_count,
                        SUM(likes)       AS total_likes,
                        SUM(replies)     AS total_replies,
                        MAX(follower_count) AS follower_count
                    FROM threads_posts
                    WHERE search_term = $1
                    GROUP BY author, author_name, is_verified
                    ORDER BY post_count DESC
                    LIMIT $2
                    """,
                    search_term,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        author,
                        author_name,
                        is_verified,
                        COUNT(*)         AS post_count,
                        SUM(likes)       AS total_likes,
                        SUM(replies)     AS total_replies,
                        MAX(follower_count) AS follower_count
                    FROM threads_posts
                    GROUP BY author, author_name, is_verified
                    ORDER BY post_count DESC
                    LIMIT $1
                    """,
                    limit,
                )
            return [dict(r) for r in rows]
