-- ============================================================
-- 信源信誉系统 - PostgreSQL 建表脚本
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 枚举类型
CREATE TYPE source_status AS ENUM ('active', 'degraded', 'dead', 'unverified');
CREATE TYPE vote_type AS ENUM ('trust', 'untrust');
CREATE TYPE article_data_type AS ENUM ('article', 'api_spec', 'dataset', 'tool_def');

-- ============================================================
-- 自动更新 updated_at 的触发器函数
-- ============================================================
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 表一: sources（信源主表）
-- ============================================================
CREATE TABLE sources (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domain          VARCHAR(255) NOT NULL,
    canonical_url   VARCHAR(500),
    category        VARCHAR(50) NOT NULL,
    subcategory     VARCHAR(50),

    -- 基础权威分（管理员设定，0-2）
    authority_base  SMALLINT NOT NULL DEFAULT 1,

    -- 贝叶斯后验参数 α/β
    alpha           REAL NOT NULL DEFAULT 0,
    beta            REAL NOT NULL DEFAULT 0,

    -- 信誉分 = α / (α + β)
    reputation_score   NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    confidence         NUMERIC(3,2) NOT NULL DEFAULT 0.00,

    -- 投票汇总（冗余，避免 JOIN votes）
    trust_votes     INT NOT NULL DEFAULT 0,
    untrust_votes   INT NOT NULL DEFAULT 0,
    total_votes     INT GENERATED ALWAYS AS (trust_votes + untrust_votes) STORED,

    -- 文档 / 发布路径
    docs_path       VARCHAR(255),
    release_path    VARCHAR(255),
    freshness_url   VARCHAR(500),

    -- 安全门控字段
    security_risk   SMALLINT NOT NULL DEFAULT 0,
    ssl_valid       SMALLINT DEFAULT NULL,
    ssl_expires     DATE DEFAULT NULL,
    xss_flagged     SMALLINT NOT NULL DEFAULT 0,
    sb_flagged      SMALLINT NOT NULL DEFAULT 0,
    last_security_scan TIMESTAMPTZ DEFAULT NULL,

    -- 多语言
    lang            VARCHAR(10) DEFAULT NULL,

    -- 状态与验证
    status          source_status NOT NULL DEFAULT 'unverified',
    last_verified   TIMESTAMPTZ DEFAULT NULL,
    verified_by     VARCHAR(64),

    -- 元数据
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_domain UNIQUE (domain)
);

CREATE INDEX idx_sources_category_score ON sources (category, reputation_score);
CREATE INDEX idx_sources_status ON sources (status);
CREATE INDEX idx_sources_updated ON sources (updated_at);

CREATE TRIGGER trg_sources_updated
    BEFORE UPDATE ON sources
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();


-- ============================================================
-- 表二: votes（投票明细表）
-- ============================================================
CREATE TABLE votes (
    id            BIGINT GENERATED ALWAYS AS IDENTITY,
    source_id     BIGINT NOT NULL,
    vote          vote_type NOT NULL,

    -- 匿名去重: SHA256(IP + UserAgent + SALT)，不存原始IP
    voter_hash    CHAR(64) NOT NULL,

    -- 可选: 对具体文章/页面的投票
    target_url    VARCHAR(2048),

    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id, voter_hash),
    CONSTRAINT uk_voter UNIQUE (voter_hash, source_id, target_url)
) PARTITION BY HASH (voter_hash);

-- 16 个 hash 分区
CREATE TABLE votes_p0  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 0);
CREATE TABLE votes_p1  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 1);
CREATE TABLE votes_p2  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 2);
CREATE TABLE votes_p3  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 3);
CREATE TABLE votes_p4  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 4);
CREATE TABLE votes_p5  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 5);
CREATE TABLE votes_p6  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 6);
CREATE TABLE votes_p7  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 7);
CREATE TABLE votes_p8  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 8);
CREATE TABLE votes_p9  PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 9);
CREATE TABLE votes_p10 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 10);
CREATE TABLE votes_p11 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 11);
CREATE TABLE votes_p12 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 12);
CREATE TABLE votes_p13 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 13);
CREATE TABLE votes_p14 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 14);
CREATE TABLE votes_p15 PARTITION OF votes FOR VALUES WITH (MODULUS 16, REMAINDER 15);

CREATE INDEX idx_votes_source ON votes (source_id, created_at);


-- ============================================================
-- 表三: reputation_changelog（信誉分变更日志）
-- ============================================================
CREATE TABLE reputation_changelog (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   BIGINT NOT NULL,
    old_score   NUMERIC(3,2),
    new_score   NUMERIC(3,2),
    reason      VARCHAR(100) NOT NULL,
    operator    VARCHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_changelog_source ON reputation_changelog (source_id);
CREATE INDEX idx_changelog_created ON reputation_changelog (created_at);


-- ============================================================
-- 表四: source_tags（信源标签，多对多）
-- ============================================================
CREATE TABLE source_tags (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag         VARCHAR(50) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_source_tag UNIQUE (source_id, tag)
);

CREATE INDEX idx_tags_tag ON source_tags (tag);


-- ============================================================
-- 表五: article_sources（文章/数据条目，SimHash 去重后入库）
-- ============================================================
CREATE TABLE article_sources (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    article_id CHAR(16) NOT NULL,
    simhash_fingerprint CHAR(16) NOT NULL,
    canonical_url VARCHAR(2048) NOT NULL,
    alias_urls JSONB DEFAULT NULL,
    parent_domain VARCHAR(255) NOT NULL,
    title VARCHAR(1024) DEFAULT NULL,
    topic_tags JSONB DEFAULT NULL,
    canonical_topic_tag VARCHAR(64) DEFAULT NULL,
    content_summary TEXT DEFAULT NULL,

    -- 全文检索向量
    fts_vector tsvector,

    -- 类型扩展（面向 agent 数据）
    data_type article_data_type NOT NULL DEFAULT 'article',
    interface_signature JSONB DEFAULT NULL,
    content_type VARCHAR(32) DEFAULT NULL,

    -- 评分
    article_score NUMERIC(4,2) DEFAULT NULL,
    implicit_trust REAL NOT NULL DEFAULT 0,
    implicit_total INT NOT NULL DEFAULT 0,
    article_total_n INT NOT NULL DEFAULT 0,
    ref_count_total INT NOT NULL DEFAULT 0,
    cited_count_total INT NOT NULL DEFAULT 0,
    adopted_count_total INT NOT NULL DEFAULT 0,
    contradiction_count INT NOT NULL DEFAULT 0,
    retention_reason VARCHAR(32) NOT NULL DEFAULT 'ephemeral',

    content_date DATE DEFAULT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_article_id UNIQUE (article_id)
);

CREATE INDEX idx_article_domain ON article_sources (parent_domain);
CREATE INDEX idx_article_fingerprint ON article_sources (simhash_fingerprint);
CREATE INDEX idx_article_score ON article_sources (article_score);
CREATE INDEX idx_article_fts ON article_sources USING GIN (fts_vector);
CREATE INDEX idx_article_retention_window ON article_sources (retention_reason, last_referenced_at);

-- tsvector 自动更新触发器
CREATE OR REPLACE FUNCTION article_fts_trigger()
RETURNS TRIGGER AS $$
BEGIN
    NEW.fts_vector := to_tsvector('english',
        COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.content_summary, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_article_fts
    BEFORE INSERT OR UPDATE ON article_sources
    FOR EACH ROW EXECUTE FUNCTION article_fts_trigger();


-- ============================================================
-- 表五点五: claim_evidence_edge（claim 到证据的原子边）
-- ============================================================
CREATE TABLE claim_evidence_edge (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    claim_id VARCHAR(64) NOT NULL,
    claim_text TEXT NOT NULL,
    source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    llm_source_id VARCHAR(64),
    article_id CHAR(16),
    source_domain VARCHAR(255),
    stance VARCHAR(16) NOT NULL,
    evidence_snippet TEXT,
    support_score REAL NOT NULL DEFAULT 0.5,
    source_tier VARCHAR(16) NOT NULL DEFAULT 'tertiary',
    trace_depth SMALLINT NOT NULL DEFAULT 2,
    used_in_final BOOLEAN NOT NULL DEFAULT FALSE,
    exact_match_signal BOOLEAN NOT NULL DEFAULT FALSE,
    exact_match_score REAL NOT NULL DEFAULT 0.0,
    slot_coverage_score REAL NOT NULL DEFAULT 0.0,
    slot_hits JSONB,
    snippet_span_type VARCHAR(32),
    verifiable_carrier_signal BOOLEAN NOT NULL DEFAULT FALSE,
    independent_consensus_signal BOOLEAN NOT NULL DEFAULT FALSE,
    edge_confidence REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_claim_edge_stance CHECK (stance IN ('support', 'oppose', 'partial')),
    CONSTRAINT chk_claim_edge_tier CHECK (source_tier IN ('primary', 'secondary', 'tertiary')),
    CONSTRAINT chk_claim_edge_trace_depth CHECK (trace_depth >= 0 AND trace_depth <= 8)
);

CREATE INDEX idx_claim_edge_session ON claim_evidence_edge (session_id);
CREATE INDEX idx_claim_edge_claim ON claim_evidence_edge (claim_id);
CREATE INDEX idx_claim_edge_llm_source ON claim_evidence_edge (llm_source_id);
CREATE INDEX idx_claim_edge_created_at ON claim_evidence_edge (created_at);

-- ============================================================
-- 表五点六: canonical_source（长期来源实体）
-- ============================================================
CREATE TABLE canonical_source (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_source_id CHAR(16) NOT NULL,
    canonical_key VARCHAR(2048) NOT NULL,
    canonical_url VARCHAR(2048) NOT NULL,
    parent_domain VARCHAR(255) NOT NULL,
    alias_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    first_session_id VARCHAR(64),
    last_session_id VARCHAR(64),
    cluster_hint CHAR(16),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_canonical_source_id UNIQUE (canonical_source_id),
    CONSTRAINT uk_canonical_source_key UNIQUE (canonical_key),
    CONSTRAINT chk_canonical_source_status CHECK (status IN ('active', 'superseded', 'inactive'))
);

CREATE INDEX idx_canonical_source_domain ON canonical_source (parent_domain);
CREATE INDEX idx_canonical_source_status ON canonical_source (status);

CREATE TRIGGER trg_canonical_source_updated
    BEFORE UPDATE ON canonical_source
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- ============================================================
-- 表五点七: claim（长期 claim 最小版本）
-- ============================================================
CREATE TABLE "claim" (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_uid CHAR(16) NOT NULL,
    claim_key CHAR(16) NOT NULL,
    claim_business_id VARCHAR(64),
    claim_text TEXT NOT NULL,
    subject VARCHAR(512) NOT NULL,
    action VARCHAR(512) NOT NULL,
    time VARCHAR(256),
    location VARCHAR(256),
    number VARCHAR(256),
    version_or_policy_name VARCHAR(512),
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    promotion_reason VARCHAR(64) NOT NULL DEFAULT 'test_feedback_minimal',
    first_session_id VARCHAR(64),
    last_session_id VARCHAR(64),
    supporting_source_count INT NOT NULL DEFAULT 0,
    claim_verdict VARCHAR(32),
    valid_to TIMESTAMPTZ DEFAULT NULL,
    superseded_by CHAR(16) DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_claim_uid UNIQUE (claim_uid),
    CONSTRAINT uk_claim_key UNIQUE (claim_key),
    CONSTRAINT chk_claim_status CHECK (status IN ('active', 'superseded', 'inactive'))
);

CREATE INDEX idx_claim_business_id ON "claim" (claim_business_id);
CREATE INDEX idx_claim_status ON "claim" (status);

CREATE TRIGGER trg_claim_updated
    BEFORE UPDATE ON "claim"
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- ============================================================
-- 表五点八: provenance_cluster（长期来源聚簇）
-- ============================================================
CREATE TABLE provenance_cluster (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_id CHAR(16) NOT NULL,
    cluster_key VARCHAR(512) NOT NULL,
    root_source_id VARCHAR(64) NOT NULL,
    root_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    member_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    cluster_type VARCHAR(32) NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    rationale TEXT,
    last_session_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_provenance_cluster_id UNIQUE (cluster_id),
    CONSTRAINT uk_provenance_cluster_key UNIQUE (cluster_key),
    CONSTRAINT chk_provenance_cluster_type CHECK (
        cluster_type IN ('independent', 'derived', 'mirror', 'aggregation')
    )
);

CREATE INDEX idx_provenance_cluster_root ON provenance_cluster (root_source_id);
CREATE INDEX idx_provenance_cluster_type ON provenance_cluster (cluster_type);

CREATE TRIGGER trg_provenance_cluster_updated
    BEFORE UPDATE ON provenance_cluster
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- ============================================================
-- 表五点九: typed_conflict（长期类型化冲突）
-- ============================================================
CREATE TABLE typed_conflict (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conflict_id VARCHAR(64) NOT NULL,
    conflict_key VARCHAR(64) NOT NULL,
    claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    claim_business_id VARCHAR(64),
    slot_name VARCHAR(64) NOT NULL,
    conflict_type VARCHAR(32) NOT NULL,
    source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    conflicting_values JSONB NOT NULL DEFAULT '[]'::jsonb,
    severity VARCHAR(16) NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0.0,
    recommended_action VARCHAR(128),
    cluster_aware BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    first_session_id VARCHAR(64),
    last_session_id VARCHAR(64),
    valid_to TIMESTAMPTZ DEFAULT NULL,
    superseded_by VARCHAR(64) DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_typed_conflict_id UNIQUE (conflict_id),
    CONSTRAINT uk_typed_conflict_key UNIQUE (conflict_key),
    CONSTRAINT chk_typed_conflict_type CHECK (
        conflict_type IN ('value_conflict', 'temporal_conflict', 'logical_conflict', 'derivative_conflict')
    ),
    CONSTRAINT chk_typed_conflict_severity CHECK (severity IN ('low', 'medium', 'high')),
    CONSTRAINT chk_typed_conflict_status CHECK (status IN ('active', 'superseded', 'inactive'))
);

CREATE INDEX idx_typed_conflict_claim_uid ON typed_conflict (claim_uid);
CREATE INDEX idx_typed_conflict_type ON typed_conflict (conflict_type);
CREATE INDEX idx_typed_conflict_status ON typed_conflict (status);

CREATE TRIGGER trg_typed_conflict_updated
    BEFORE UPDATE ON typed_conflict
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- ============================================================
-- 表五点十: accepted_causal_edge（长期接受因果边）
-- ============================================================
CREATE TABLE accepted_causal_edge (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    edge_id VARCHAR(64) NOT NULL,
    from_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    from_claim_id VARCHAR(64) NOT NULL,
    to_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    to_claim_id VARCHAR(64) NOT NULL,
    relation_type VARCHAR(32) NOT NULL,
    time_basis VARCHAR(256),
    mechanism_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    supporting_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    independent_root_count INT NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    acceptance_reason TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    first_session_id VARCHAR(64),
    last_session_id VARCHAR(64),
    valid_to TIMESTAMPTZ DEFAULT NULL,
    superseded_by VARCHAR(64) DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_accepted_causal_edge_id UNIQUE (edge_id),
    CONSTRAINT chk_accepted_causal_relation_type CHECK (
        relation_type IN ('caused', 'influenced', 'precedent_for')
    ),
    CONSTRAINT chk_accepted_causal_status CHECK (status IN ('active', 'superseded', 'inactive'))
);

CREATE INDEX idx_accepted_causal_edge_from_to
    ON accepted_causal_edge (from_claim_id, to_claim_id);
CREATE INDEX idx_accepted_causal_edge_status
    ON accepted_causal_edge (status);

CREATE TRIGGER trg_accepted_causal_edge_updated
    BEFORE UPDATE ON accepted_causal_edge
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- ============================================================
-- 表五点十一: claim_slot_evidence（过程层槽位证据）
-- ============================================================
CREATE TABLE claim_slot_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    claim_business_id VARCHAR(64) NOT NULL,
    claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    slot_name VARCHAR(64) NOT NULL,
    slot_value VARCHAR(512) NOT NULL,
    source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
    llm_source_id VARCHAR(64) NOT NULL,
    canonical_source_id CHAR(16) REFERENCES canonical_source(canonical_source_id) ON DELETE SET NULL,
    provenance_cluster_id CHAR(16) REFERENCES provenance_cluster(cluster_id) ON DELETE SET NULL,
    evidence_snippet TEXT,
    page VARCHAR(64),
    section VARCHAR(255),
    line VARCHAR(64),
    snippet_span_type VARCHAR(32),
    confidence REAL NOT NULL DEFAULT 0.0,
    expires_at TIMESTAMPTZ DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_claim_slot_name CHECK (
        slot_name IN ('subject', 'action', 'time', 'location', 'number', 'version_or_policy_name', 'text')
    ),
    CONSTRAINT chk_claim_slot_span_type CHECK (
        snippet_span_type IS NULL
        OR snippet_span_type IN ('original_sentence', 'summary', 'table_cell', 'title')
    )
);

CREATE INDEX idx_claim_slot_evidence_session ON claim_slot_evidence (session_id);
CREATE INDEX idx_claim_slot_evidence_claim ON claim_slot_evidence (claim_business_id, slot_name);
CREATE INDEX idx_claim_slot_evidence_expiry ON claim_slot_evidence (expires_at);

-- ============================================================
-- 表五点十二: candidate_causal_edge（过程层候选因果边，占位持久化）
-- ============================================================
CREATE TABLE candidate_causal_edge (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    edge_id VARCHAR(64) NOT NULL,
    from_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    from_claim_id VARCHAR(64) NOT NULL,
    to_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    to_claim_id VARCHAR(64) NOT NULL,
    relation_type VARCHAR(32) NOT NULL,
    time_basis VARCHAR(256),
    mechanism_claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    supporting_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence REAL NOT NULL DEFAULT 0.0,
    status VARCHAR(16) NOT NULL DEFAULT 'candidate',
    last_session_id VARCHAR(64),
    expires_at TIMESTAMPTZ DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_candidate_causal_edge_id UNIQUE (edge_id),
    CONSTRAINT chk_candidate_causal_relation_type CHECK (
        relation_type IN ('caused', 'influenced', 'precedent_for')
    ),
    CONSTRAINT chk_candidate_causal_status CHECK (status IN ('candidate', 'accepted', 'rejected'))
);

CREATE INDEX idx_candidate_causal_edge_from_to
    ON candidate_causal_edge (from_claim_id, to_claim_id);
CREATE INDEX idx_candidate_causal_edge_expiry
    ON candidate_causal_edge (expires_at);

-- ============================================================
-- 表五点十三: causal_gap（过程层因果缺口，占位持久化）
-- ============================================================
CREATE TABLE causal_gap (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gap_id VARCHAR(64) NOT NULL,
    from_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    from_claim_id VARCHAR(64) NOT NULL,
    to_claim_uid CHAR(16) REFERENCES "claim"(claim_uid) ON DELETE SET NULL,
    to_claim_id VARCHAR(64) NOT NULL,
    gap_type VARCHAR(64) NOT NULL,
    reason TEXT NOT NULL,
    supporting_source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    last_session_id VARCHAR(64),
    expires_at TIMESTAMPTZ DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_causal_gap_id UNIQUE (gap_id),
    CONSTRAINT chk_causal_gap_type CHECK (
        gap_type IN ('missing_time_anchor', 'missing_mechanism', 'insufficient_independent_support')
    ),
    CONSTRAINT chk_causal_gap_status CHECK (status IN ('open', 'resolved', 'dismissed'))
);

CREATE INDEX idx_causal_gap_from_to ON causal_gap (from_claim_id, to_claim_id);
CREATE INDEX idx_causal_gap_expiry ON causal_gap (expires_at);

-- ============================================================
-- 表六: sources_daily_stats（按日聚合统计，替代 raw_feedback 存储）
-- ============================================================
CREATE TABLE sources_daily_stats (
    id BIGINT GENERATED ALWAYS AS IDENTITY,
    source_id INT NOT NULL,
    stat_date DATE NOT NULL,
    usage_count INT NOT NULL DEFAULT 0,
    implicit_trust REAL NOT NULL DEFAULT 0,
    implicit_untrust INT NOT NULL DEFAULT 0,
    contradictions INT NOT NULL DEFAULT 0,

    PRIMARY KEY (id, stat_date),
    CONSTRAINT uk_source_date UNIQUE (source_id, stat_date)
) PARTITION BY RANGE (stat_date);

CREATE INDEX idx_daily_stats_source ON sources_daily_stats (source_id, stat_date);

-- 按月自动创建分区（部署时需至少创建当前月及下月）
CREATE TABLE sources_daily_stats_202607 PARTITION OF sources_daily_stats
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE sources_daily_stats_202608 PARTITION OF sources_daily_stats
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE sources_daily_stats_202609 PARTITION OF sources_daily_stats
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');


-- ============================================================
-- 表六点五: source_signal_rollup（统一时间粒度账本）
-- ============================================================
CREATE TABLE source_signal_rollup (
    source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    grain VARCHAR(16) NOT NULL,
    bucket_start DATE NOT NULL,
    ref_count_total INT NOT NULL DEFAULT 0,
    cited_count_total INT NOT NULL DEFAULT 0,
    adopted_count_total INT NOT NULL DEFAULT 0,
    discard_count_total INT NOT NULL DEFAULT 0,
    contradiction_count INT NOT NULL DEFAULT 0,
    quality_high_count INT NOT NULL DEFAULT 0,
    quality_low_count INT NOT NULL DEFAULT 0,
    verifiable_carrier_count INT NOT NULL DEFAULT 0,
    exact_match_count INT NOT NULL DEFAULT 0,
    independent_consensus_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (source_id, grain, bucket_start),
    CONSTRAINT chk_signal_rollup_grain CHECK (grain IN ('daily', 'monthly', 'quarterly', 'yearly'))
);

CREATE INDEX idx_signal_rollup_grain_bucket ON source_signal_rollup (grain, bucket_start);


-- ============================================================
-- 表七至十: simhash_bucket（分桶索引，加速去重匹配）
-- ============================================================
CREATE TABLE simhash_bucket_0 (
    bucket_key CHAR(4) NOT NULL,
    article_id CHAR(16) NOT NULL,
    simhash_full CHAR(16) NOT NULL,
    PRIMARY KEY (bucket_key, article_id)
);
CREATE INDEX idx_sb0_article ON simhash_bucket_0 (article_id);

CREATE TABLE simhash_bucket_1 (
    bucket_key CHAR(4) NOT NULL,
    article_id CHAR(16) NOT NULL,
    simhash_full CHAR(16) NOT NULL,
    PRIMARY KEY (bucket_key, article_id)
);
CREATE INDEX idx_sb1_article ON simhash_bucket_1 (article_id);

CREATE TABLE simhash_bucket_2 (
    bucket_key CHAR(4) NOT NULL,
    article_id CHAR(16) NOT NULL,
    simhash_full CHAR(16) NOT NULL,
    PRIMARY KEY (bucket_key, article_id)
);
CREATE INDEX idx_sb2_article ON simhash_bucket_2 (article_id);

CREATE TABLE simhash_bucket_3 (
    bucket_key CHAR(4) NOT NULL,
    article_id CHAR(16) NOT NULL,
    simhash_full CHAR(16) NOT NULL,
    PRIMARY KEY (bucket_key, article_id)
);
CREATE INDEX idx_sb3_article ON simhash_bucket_3 (article_id);


-- ============================================================
-- 表十一: llm_preferences（LLM 自报偏好声明，会话级）
-- ============================================================
CREATE TABLE llm_preferences (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id    VARCHAR(64) NOT NULL,
    source_id     BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    query_category VARCHAR(100),
    source_usefulness_rating REAL,
    answer_quality_gap VARCHAR(200),
    preference_blob JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lp_session ON llm_preferences (session_id);
CREATE INDEX idx_lp_source ON llm_preferences (source_id);
CREATE INDEX idx_lp_query_category ON llm_preferences (query_category);
CREATE INDEX idx_lp_created_at ON llm_preferences (created_at);


-- ============================================================
-- 表十二: source_topic_reputation（话题专精信誉积分）
-- ============================================================
CREATE TABLE source_topic_reputation (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id         BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    topic_tag         VARCHAR(50) NOT NULL,
    alpha             REAL NOT NULL DEFAULT 0,
    beta              REAL NOT NULL DEFAULT 0,
    reputation_score  NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    confidence        NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_source_topic UNIQUE (source_id, topic_tag)
);

CREATE INDEX idx_str_source ON source_topic_reputation (source_id);
CREATE INDEX idx_str_topic ON source_topic_reputation (topic_tag);

CREATE TRIGGER trg_str_updated
    BEFORE UPDATE ON source_topic_reputation
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();


-- ============================================================
-- 表十三: content_type_reputation（内容类型可靠性）
-- ============================================================
CREATE TABLE content_type_reputation (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_type      VARCHAR(50) NOT NULL,
    alpha             REAL NOT NULL DEFAULT 0,
    beta              REAL NOT NULL DEFAULT 0,
    reputation_score  NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    confidence        NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uk_ctype UNIQUE (content_type)
);

CREATE TRIGGER trg_ctr_updated
    BEFORE UPDATE ON content_type_reputation
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();


-- ============================================================
-- 表十四: tag_taxonomy（静态标签池）
-- ============================================================
CREATE TABLE tag_taxonomy (
    canonical_tag VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(128) NOT NULL,
    tag_group VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    protected BOOLEAN NOT NULL DEFAULT TRUE,
    is_static BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================
-- 表十五: tag_dynamic_registry（动态标签观察池）
-- ============================================================
CREATE TABLE tag_dynamic_registry (
    tag_name VARCHAR(128) PRIMARY KEY,
    mapped_canonical_tag VARCHAR(64) REFERENCES tag_taxonomy(canonical_tag),
    usage_count INT NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    backfilled_at TIMESTAMPTZ
);

CREATE INDEX idx_tag_dynamic_status_seen ON tag_dynamic_registry (status, last_seen_at);
