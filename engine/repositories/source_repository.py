from __future__ import annotations

try:
    from db.connection import get_read_connection
except ImportError:  # pragma: no cover - unit test fallback
    from db.connection import get_connection as get_read_connection

try:
    from psycopg2.extras import RealDictCursor
except Exception:  # pragma: no cover - unit test fallback
    RealDictCursor = object
from utils.request_trace import log_trace_node


def fetch_active_source(domain: str) -> dict | None:
    sql = """
        SELECT domain, canonical_url, docs_path, release_path, freshness_url,
               reputation_score, confidence, authority_base, category, subcategory,
               status, trust_votes, untrust_votes, security_risk, ssl_valid,
               xss_flagged, sb_flagged
        FROM sources
        WHERE domain = %s AND status != 'dead'
    """
    with get_read_connection(role="primary", reason="sources.lookup") as conn:
        log_trace_node(
            "sources.lookup.db.acquire",
            "source lookup database connection acquired",
            data={"domain": domain, "db_role": "primary", "db_intent": "read"},
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (domain,))
            return cur.fetchone()


def ensure_source(cur, domain: str, category: str = "unknown") -> tuple[int, bool]:
    normalized_domain = domain.strip().lower()
    cur.execute("SELECT id FROM sources WHERE domain = %s", (normalized_domain,))
    row = cur.fetchone()
    if row:
        return row[0], False

    cur.execute(
        """
        INSERT INTO sources (domain, category, status)
        VALUES (%s, %s, 'unverified')
        ON CONFLICT (domain) DO NOTHING
        RETURNING id
        """,
        (normalized_domain, category),
    )
    inserted = cur.fetchone()
    if inserted:
        return inserted[0], True

    cur.execute("SELECT id FROM sources WHERE domain = %s", (normalized_domain,))
    existing = cur.fetchone()
    if existing:
        return existing[0], False
    raise RuntimeError(f"failed to resolve source id for domain: {normalized_domain}")


def update_last_verified(cur, source_id: int) -> None:
    cur.execute(
        "UPDATE sources SET last_verified = CURRENT_TIMESTAMP WHERE id = %s",
        (source_id,),
    )


def fetch_sources_search_rows(
    *,
    category: str | None,
    min_score: float,
    limit: int,
    offset: int,
    include_risky: bool,
    include_non_active: bool,
) -> list[dict]:
    filters = ["reputation_score >= %s"]
    values: list[object] = [min_score]

    if category:
        filters.append("category = %s")
        values.append(category)
    if not include_non_active:
        filters.append("status = 'active'")
    if not include_risky:
        filters.append("security_risk = 0")

    sql = f"""
        SELECT domain, canonical_url, docs_path, release_path, freshness_url,
               reputation_score, confidence, authority_base, category, subcategory,
               status, trust_votes, untrust_votes, security_risk, ssl_valid,
               xss_flagged, sb_flagged
        FROM sources
        WHERE {' AND '.join(filters)}
        ORDER BY reputation_score DESC, confidence DESC, domain ASC
        LIMIT %s OFFSET %s
    """
    values.extend([limit, offset])

    with get_read_connection(role="primary", reason="sources.search") as conn:
        log_trace_node(
            "sources.search.db.acquire",
            "source search database connection acquired",
            data={
                "category": category,
                "min_score": min_score,
                "limit": limit,
                "offset": offset,
                "db_role": "primary",
                "db_intent": "read",
            },
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, values)
            return list(cur.fetchall())


def fetch_article_search_rows(
    *,
    query: str,
    limit: int,
    offset: int,
    min_article_score: float,
) -> list[dict]:
    sql = """
        WITH backfill AS (
            UPDATE article_sources
            SET title = COALESCE(
                    NULLIF(title, ''),
                    NULLIF(initcap(replace(parent_domain, '.', ' ')), '')
                ),
                content_summary = COALESCE(
                    NULLIF(content_summary, ''),
                    NULLIF(
                        trim(
                            concat_ws(
                                ' ',
                                parent_domain,
                                canonical_url,
                                COALESCE(topic_tags::text, '')
                            )
                        ),
                        ''
                    )
                ),
                article_score = COALESCE(
                    article_score,
                    ROUND(
                        (
                            COALESCE(implicit_trust, 0)::numeric
                            / GREATEST(article_total_n, 1)
                        ),
                        2
                    )
                )
            WHERE title IS NULL
               OR title = ''
               OR content_summary IS NULL
               OR content_summary = ''
               OR article_score IS NULL
            RETURNING article_id
        )
        SELECT
            a.article_id,
            a.canonical_url,
            a.parent_domain,
            a.title,
            a.topic_tags,
            a.content_summary,
            a.article_score,
            a.simhash_fingerprint,
            s.docs_path,
            s.reputation_score AS domain_reputation_score,
            s.confidence AS domain_confidence,
            s.category,
            s.status,
            COALESCE(s.security_risk, 0) AS security_risk,
            s.ssl_valid,
            COALESCE(s.xss_flagged, 0) AS xss_flagged,
            COALESCE(s.sb_flagged, 0) AS sb_flagged,
            a.content_date,
            a.last_referenced_at,
            ts_rank_cd(a.fts_vector, plainto_tsquery('english', %s)) AS rank
        FROM article_sources a
        LEFT JOIN sources s ON s.domain = a.parent_domain
        WHERE a.fts_vector @@ plainto_tsquery('english', %s)
          AND COALESCE(a.article_score, 0) >= %s
          AND COALESCE(s.security_risk, 0) = 0
          AND COALESCE(s.status, 'active') != 'dead'
        ORDER BY rank DESC, a.article_score DESC NULLS LAST, a.last_referenced_at DESC
        LIMIT %s OFFSET %s
    """

    with get_read_connection(role="content", reason="articles.search") as conn:
        log_trace_node(
            "articles.search.db.acquire",
            "article search database connection acquired",
            data={
                "query": query,
                "min_article_score": min_article_score,
                "limit": limit,
                "offset": offset,
                "db_role": "content",
                "db_intent": "read",
            },
        )
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (query, query, min_article_score, limit, offset))
            return list(cur.fetchall())
