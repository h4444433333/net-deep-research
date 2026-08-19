from __future__ import annotations


def resolve_existing_article(
    cur,
    *,
    canonical_url: str,
    simhash_fingerprint: str,
    simhash_buckets: list[tuple[int, str]],
    hamming_distance,
):
    cur.execute(
        """
        SELECT article_id, alias_urls, topic_tags
        FROM article_sources
        WHERE canonical_url = %s
        LIMIT 1
        """,
        (canonical_url,),
    )
    row = cur.fetchone()
    if row:
        return row

    candidate_ids: set[str] = set()
    for bucket_idx, bucket_key in simhash_buckets:
        cur.execute(
            f"SELECT article_id FROM simhash_bucket_{bucket_idx} WHERE bucket_key = %s",
            (bucket_key,),
        )
        candidate_ids.update(item[0] for item in cur.fetchall())

    if not candidate_ids:
        return None

    cur.execute(
        """
        SELECT article_id, alias_urls, topic_tags, simhash_fingerprint
        FROM article_sources
        WHERE article_id = ANY(%s)
        """,
        (list(candidate_ids),),
    )
    candidates = cur.fetchall()
    if not candidates:
        return None

    best_row = None
    best_distance = None
    for article_id, alias_urls, topic_tags, existing_simhash in candidates:
        distance = hamming_distance(existing_simhash, simhash_fingerprint)
        if distance <= 3 and (best_distance is None or distance < best_distance):
            best_distance = distance
            best_row = (article_id, alias_urls, topic_tags)
    return best_row


def update_article_source(
    cur,
    *,
    simhash_fingerprint: str,
    merged_alias_urls_json: str,
    derived_title: str | None,
    merged_topic_tags_json: str | None,
    canonical_topic_tag: str | None,
    derived_content_summary: str | None,
    interface_signature: str,
    content_type: str | None,
    content_date,
    article_score: float,
    positive_adoption: int,
    cited_count_increment: int,
    contradiction_count: int,
    retention_reason: str,
    existing_article_id: str,
) -> None:
    cur.execute(
        """
        UPDATE article_sources
        SET simhash_fingerprint = %s,
            alias_urls = %s::jsonb,
            title = COALESCE(%s, title),
            topic_tags = COALESCE(%s::jsonb, topic_tags),
            canonical_topic_tag = COALESCE(%s, canonical_topic_tag),
            content_summary = COALESCE(%s, content_summary),
            interface_signature = %s::jsonb,
            content_type = COALESCE(%s, content_type),
            content_date = COALESCE(%s, content_date),
            article_score = GREATEST(COALESCE(article_score, 0), %s),
            implicit_trust = article_sources.implicit_trust + %s,
            implicit_total = article_sources.implicit_total + 1,
            article_total_n = article_sources.article_total_n + 1,
            ref_count_total = article_sources.ref_count_total + 1,
            cited_count_total = article_sources.cited_count_total + %s,
            adopted_count_total = article_sources.adopted_count_total + %s,
            contradiction_count = article_sources.contradiction_count + %s,
            retention_reason = CASE
                WHEN article_sources.retention_reason = 'official' THEN 'official'
                WHEN %s = 'official' THEN 'official'
                WHEN article_sources.retention_reason = 'high_value' OR %s = 'high_value' THEN 'high_value'
                ELSE 'ephemeral'
            END,
            last_referenced_at = CURRENT_TIMESTAMP
        WHERE article_id = %s
        """,
        (
            simhash_fingerprint,
            merged_alias_urls_json,
            derived_title,
            merged_topic_tags_json,
            canonical_topic_tag,
            derived_content_summary,
            interface_signature,
            content_type,
            content_date,
            article_score,
            positive_adoption,
            cited_count_increment,
            positive_adoption,
            contradiction_count,
            retention_reason,
            retention_reason,
            existing_article_id,
        ),
    )


def insert_article_source(
    cur,
    *,
    article_id: str,
    simhash_fingerprint: str,
    canonical_url: str,
    alias_urls_json: str,
    parent_domain: str,
    title: str | None,
    topic_tags_json: str | None,
    canonical_topic_tag: str | None,
    content_summary: str | None,
    interface_signature: str,
    content_type: str | None,
    article_score: float,
    positive_adoption: int,
    content_date,
    cited_count_increment: int,
    contradiction_count: int,
    retention_reason: str,
) -> None:
    cur.execute(
        """
        INSERT INTO article_sources (
            article_id,
            simhash_fingerprint,
            canonical_url,
            alias_urls,
            parent_domain,
            title,
            topic_tags,
            canonical_topic_tag,
            content_summary,
            interface_signature,
            content_type,
            article_score,
            implicit_trust,
            implicit_total,
            article_total_n,
            content_date,
            ref_count_total,
            cited_count_total,
            adopted_count_total,
            contradiction_count,
            retention_reason
        )
        VALUES (
            %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, 1, 1, %s, 1, %s, %s, %s, %s
        )
        """,
        (
            article_id,
            simhash_fingerprint,
            canonical_url,
            alias_urls_json,
            parent_domain,
            title,
            topic_tags_json,
            canonical_topic_tag,
            content_summary,
            interface_signature,
            content_type,
            article_score,
            positive_adoption,
            content_date,
            cited_count_increment,
            positive_adoption,
            contradiction_count,
            retention_reason,
        ),
    )


def upsert_simhash_buckets(
    cur,
    *,
    article_id: str,
    simhash_fingerprint: str,
    simhash_buckets: list[tuple[int, str]],
) -> None:
    for bucket_idx, bucket_key in simhash_buckets:
        cur.execute(
            f"""
            INSERT INTO simhash_bucket_{bucket_idx} (bucket_key, article_id, simhash_full)
            VALUES (%s, %s, %s)
            ON CONFLICT (bucket_key, article_id) DO UPDATE SET
                simhash_full = EXCLUDED.simhash_full
            """,
            (bucket_key, article_id, simhash_fingerprint),
        )
