from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TablePlacement:
    table: str
    logical_role: str
    access_pattern: str
    consistency: str
    business_key: str


@dataclass(frozen=True)
class ConstraintPolicy:
    relation: str
    grade: str
    same_database_required: bool
    strategy: str


@dataclass(frozen=True)
class LifecyclePolicy:
    table: str
    temperature: str
    retention_strategy: str
    expansion_strategy: str


@dataclass(frozen=True)
class CutoverStage:
    stage: str
    goal: str
    source_role: str
    target_role: str
    requires_backfill: bool
    requires_dual_write: bool
    validation_group: str


@dataclass(frozen=True)
class SemanticLifecycleEntry:
    object_name: str
    layer: str
    retention_strategy: str
    cleanup_job: str | None
    archive_strategy: str
    soft_delete_strategy: str


TABLE_PLACEMENTS: tuple[TablePlacement, ...] = (
    TablePlacement("sources", "primary", "read_write", "strong", "domain"),
    TablePlacement("votes", "primary", "write_heavy", "strong", "vote_id"),
    TablePlacement("reputation_changelog", "primary", "append_audit", "eventual", "changelog_id"),
    TablePlacement("canonical_source", "primary", "upsert", "strong", "canonical_key"),
    TablePlacement("claim", "primary", "upsert", "strong", "claim_key"),
    TablePlacement("provenance_cluster", "primary", "upsert", "eventual", "cluster_key"),
    TablePlacement("typed_conflict", "primary", "upsert", "eventual", "conflict_key"),
    TablePlacement("accepted_causal_edge", "primary", "upsert", "eventual", "edge_id"),
    TablePlacement("sources_daily_stats", "analytics", "append_rollup", "eventual", "source_id+stat_date"),
    TablePlacement("source_signal_rollup", "analytics", "append_rollup", "eventual", "source_id+grain+bucket_start"),
    TablePlacement("article_sources", "content", "read_write", "eventual", "article_id"),
    TablePlacement("simhash_bucket_0", "content", "index_lookup", "eventual", "bucket_key+article_id"),
    TablePlacement("simhash_bucket_1", "content", "index_lookup", "eventual", "bucket_key+article_id"),
    TablePlacement("simhash_bucket_2", "content", "index_lookup", "eventual", "bucket_key+article_id"),
    TablePlacement("simhash_bucket_3", "content", "index_lookup", "eventual", "bucket_key+article_id"),
    TablePlacement("claim_evidence_edge", "process", "session_write", "eventual", "session_id+claim_id+llm_source_id"),
    TablePlacement("claim_slot_evidence", "process", "session_write", "eventual", "session_id+claim_id+source_id+slot_name"),
    TablePlacement("candidate_causal_edge", "process", "session_write", "eventual", "edge_id"),
    TablePlacement("causal_gap", "process", "session_write", "eventual", "gap_id"),
    TablePlacement("llm_preferences", "process", "session_write", "eventual", "session_id+source_id"),
)


CONSTRAINT_POLICIES: tuple[ConstraintPolicy, ...] = (
    ConstraintPolicy(
        relation="sources -> votes",
        grade="strong_same_db",
        same_database_required=True,
        strategy="继续保留同库强约束，避免投票主键与信源主键漂移。",
    ),
    ConstraintPolicy(
        relation="sources -> source_signal_rollup",
        grade="soft_cross_db_ready",
        same_database_required=False,
        strategy="以 source_id 作为业务键，允许 analytics 库异步汇总并校验 source_id 存在性。",
    ),
    ConstraintPolicy(
        relation="sources -> article_sources",
        grade="soft_cross_db_ready",
        same_database_required=False,
        strategy="以 parent_domain / source_id 双轨承载，允许内容层独立存储后通过业务键回查。",
    ),
    ConstraintPolicy(
        relation="sources -> claim_evidence_edge",
        grade="soft_cross_db_ready",
        same_database_required=False,
        strategy="以 source_id、source_domain、article_id 作为应用层关联键，不依赖跨库外键。",
    ),
    ConstraintPolicy(
        relation="sources -> llm_preferences",
        grade="soft_cross_db_ready",
        same_database_required=False,
        strategy="保留 source_id 业务键，过程库写入前后分别做存在性校验与异常日志。",
    ),
)


LIFECYCLE_POLICIES: tuple[LifecyclePolicy, ...] = (
    LifecyclePolicy(
        "votes",
        "warm",
        "仅保留短期投票明细用于去重与审计，长期票数沉淀到 sources 聚合字段后按窗口清理。",
        "长期信誉依赖 sources 聚合字段，votes 可继续按时间窗口切分与清退。",
    ),
    LifecyclePolicy(
        "reputation_changelog",
        "warm",
        "仅保留近期分数变更轨迹，避免审计日志长期无限累积。",
        "保留窗口内抽样审计，超窗记录按时间批量清理。",
    ),
    LifecyclePolicy(
        "sources_daily_stats",
        "warm",
        "作为 legacy 日统计过渡层，仅在 rollup 缺位时保底；一旦迁移完成按窗口删除。",
        "持续迁移到 analytics rollup，最终弱化为兼容层并可逐步退场。",
    ),
    LifecyclePolicy(
        "sources",
        "hot",
        "长期保留主实体，不依赖删数扩容。",
        "优先纵向分区与只读副本扩展，后续保留在 primary。",
    ),
    LifecyclePolicy(
        "canonical_source",
        "hot",
        "作为长期归一化来源资产长期保留，不做窗口物理删除。",
        "维持业务键 upsert，后续可按 domain 分区或只读副本扩展。",
    ),
    LifecyclePolicy(
        "claim",
        "hot",
        "长期 claim 通过 status / valid_to / superseded_by 软失效，不依赖物理删除。",
        "保持业务键稳定，后续可按状态与时间维度扩展索引。",
    ),
    LifecyclePolicy(
        "provenance_cluster",
        "hot",
        "长期保留 provenance 聚簇结果，重算时覆盖最新视图。",
        "以 cluster_key 稳定 upsert，必要时做批量重算回填。",
    ),
    LifecyclePolicy(
        "typed_conflict",
        "hot",
        "类型化冲突作为长期治理资产保留，通过 status / valid_to / superseded_by 软失效。",
        "保留业务键 upsert，后续可按 conflict_type 或状态拆分热点索引。",
    ),
    LifecyclePolicy(
        "accepted_causal_edge",
        "hot",
        "通过 acceptance 规则进入长期层，后续以 status / valid_to / superseded_by 软失效。",
        "保留稳定 edge_id 业务键，后续可按 relation_type 或状态扩展索引。",
    ),
    LifecyclePolicy(
        "article_sources",
        "warm",
        "按 official/high_value/ephemeral 分层清理，保留高价值页面。",
        "先迁移到 content 角色，再执行回填与路由切换。",
    ),
    LifecyclePolicy(
        "claim_evidence_edge",
        "warm",
        "仅保留窗口期过程证据，长期依赖 user.log 与聚合结论。",
        "优先迁移到 process 角色，必要时分 session 范围归档。",
    ),
    LifecyclePolicy(
        "claim_slot_evidence",
        "warm",
        "按 TTL 保留过程层槽位证据，长期结论沉淀到 claim / typed_conflict。",
        "统一进入 semantic_process_cleanup，必要时按 session 范围归档。",
    ),
    LifecyclePolicy(
        "candidate_causal_edge",
        "warm",
        "仅保留测试期候选因果占位，超窗自动清理，不进入默认长期层。",
        "通过 feature flag 控制写入，统一进入 semantic_process_cleanup。",
    ),
    LifecyclePolicy(
        "causal_gap",
        "warm",
        "仅保留测试期因果缺口占位，超窗自动清理，避免失败信息常驻。",
        "通过 feature flag 控制写入，统一进入 semantic_process_cleanup。",
    ),
    LifecyclePolicy(
        "llm_preferences",
        "warm",
        "保留近期样本，长期经验收敛到固定维度层。",
        "优先迁移到 process 角色，后续做批量归档或聚合。",
    ),
    LifecyclePolicy(
        "source_signal_rollup",
        "hot_to_cold",
        "daily -> monthly -> quarterly -> yearly，长期保留聚合层。",
        "优先迁移到 analytics 角色并保留无删数回填通道。",
    ),
)


SEMANTIC_LIFECYCLE_MATRIX: tuple[SemanticLifecycleEntry, ...] = (
    SemanticLifecycleEntry(
        "canonical_source",
        "long_term",
        "长期保留，按业务键 upsert。",
        None,
        "必要时做只读副本或按域归档。",
        "不走软删除，维持 active 主视图。",
    ),
    SemanticLifecycleEntry(
        "claim",
        "long_term",
        "长期保留，更新时通过 status / valid_to / superseded_by 软失效。",
        None,
        "保留历史版本，可后续接入归档表。",
        "superseded / inactive。",
    ),
    SemanticLifecycleEntry(
        "typed_conflict",
        "long_term",
        "长期保留冲突治理结果，通过状态收敛而非物理删除。",
        None,
        "保留历史冲突轨迹，可后续归档冷数据。",
        "superseded / inactive。",
    ),
    SemanticLifecycleEntry(
        "accepted_causal_edge",
        "long_term",
        "长期保留被接受的因果边，通过 status / valid_to / superseded_by 软失效。",
        None,
        "保留 accepted 边历史，可后续归档冷数据。",
        "superseded / inactive。",
    ),
    SemanticLifecycleEntry(
        "claim_slot_evidence",
        "process",
        "30 天默认 TTL，由 expires_at 驱动。",
        "semantic_process_cleanup",
        "无需长期归档，必要时按 session 导出。",
        "到期直接物理删除。",
    ),
    SemanticLifecycleEntry(
        "candidate_causal_edge",
        "process",
        "30 天默认 TTL，仅在 test-only causal 占位开关开启时写入。",
        "semantic_process_cleanup",
        "不做长期归档，后续接受的边再升级到长期层。",
        "到期直接物理删除。",
    ),
    SemanticLifecycleEntry(
        "causal_gap",
        "process",
        "30 天默认 TTL，仅作为因果缺口占位。",
        "semantic_process_cleanup",
        "不做长期归档，后续可转人工审核产物。",
        "到期直接物理删除。",
    ),
    SemanticLifecycleEntry(
        "semantic_storage.runtime_graph",
        "debug",
        "默认仅随响应返回，不落长期表。",
        None,
        "如需持久化，仅允许 test/debug 环境按文件或日志归档。",
        "会话结束即失效。",
    ),
    SemanticLifecycleEntry(
        "claim_verification.numeric_reasoning",
        "debug",
        "默认仅保留在响应与日志上下文。",
        None,
        "需要排障时走 debug 日志归档，不进主业务表。",
        "会话结束即失效。",
    ),
)


CUTOVER_STAGES: tuple[CutoverStage, ...] = (
    CutoverStage(
        stage="stage_1_read_split",
        goal="启用主写从读与强一致回退。",
        source_role="primary",
        target_role="primary_read",
        requires_backfill=False,
        requires_dual_write=False,
        validation_group="read_write_split",
    ),
    CutoverStage(
        stage="stage_2_process_split",
        goal="将 claim_evidence_edge / llm_preferences 迁移到 process 角色。",
        source_role="primary",
        target_role="process",
        requires_backfill=True,
        requires_dual_write=True,
        validation_group="process_cutover",
    ),
    CutoverStage(
        stage="stage_3_content_split",
        goal="将 article_sources / simhash buckets 迁移到 content 角色。",
        source_role="primary",
        target_role="content",
        requires_backfill=True,
        requires_dual_write=True,
        validation_group="content_cutover",
    ),
    CutoverStage(
        stage="stage_4_analytics_split",
        goal="将信号聚合与统计迁移到 analytics 角色。",
        source_role="primary",
        target_role="analytics",
        requires_backfill=True,
        requires_dual_write=False,
        validation_group="analytics_cutover",
    ),
)


VALIDATION_MATRIX = {
    "single_db_baseline": (
        "sources 查询结果与改造前一致。",
        "research-feedback 正常完成 sources/article/claim edge/rollup 写入。",
        "db.route 日志包含 requested_role、resolved_role、requested_intent、resolved_intent。",
        "强一致读请求不命中只读副本。",
    ),
    "read_write_split": (
        "普通查询默认进入 read 路由。",
        "写后立即读通过 strong consistency 回写库。",
        "读库缺失时安全回退到 role write 或 primary write。",
    ),
    "process_cutover": (
        "claim_evidence_edge 与 llm_preferences 回填总数一致。",
        "双写期间 process 与 primary 样本对账一致。",
        "research-feedback 对过程数据写失败时日志明确标出 process 角色。",
    ),
    "content_cutover": (
        "article_sources 与 simhash buckets 回填校验通过。",
        "articles.search 切换后结果数量、排序与主库基线一致。",
        "simhash 去重命中率与切换前一致。",
    ),
    "analytics_cutover": (
        "source_signal_rollup 聚合口径与迁移前一致。",
        "daily/monthly/quarterly/yearly compaction 无重复累计。",
        "analytics 不可用时错误日志明确暴露目标角色与失败节点。",
    ),
}


def logical_role_for_table(table: str) -> str:
    for placement in TABLE_PLACEMENTS:
        if placement.table == table:
            return placement.logical_role
    raise KeyError(f"unknown table: {table}")
