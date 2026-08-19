"""
静态/动态双层标签治理。

目标：
1. 长期信誉层只使用固定 canonical tags
2. LLM 自由生成的标签先进入动态注册表观察
3. 静态标签永不删除；动态标签按频次和时效治理
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict

from db.connection import get_connection
from utils.logger import get_logger

logger = get_logger("tag_taxonomy")

_tables_lock = threading.Lock()
_static_synced = False


def _entry(display_name: str, tag_group: str, aliases: list[str]) -> dict:
    return {
        "display_name": display_name,
        "tag_group": tag_group,
        "aliases": aliases,
    }


STATIC_TAG_DEFINITIONS: dict[str, dict] = {
    "technology_ai": _entry("人工智能", "technology", ["ai", "人工智能"]),
    "technology_llm": _entry("大模型", "technology", ["llm", "大模型", "语言模型"]),
    "technology_rag": _entry("检索增强生成", "technology", ["rag", "检索增强生成"]),
    "technology_search": _entry("搜索与检索", "technology", ["search", "检索", "搜索"]),
    "technology_frontend": _entry("前端框架", "technology", ["frontend", "前端", "web前端"]),
    "technology_backend": _entry("后端服务", "technology", ["backend", "后端", "服务端"]),
    "technology_cloud": _entry("云与基础设施", "technology", ["cloud", "云计算", "基础设施"]),
    "technology_database": _entry("数据库", "technology", ["database", "数据库", "db"]),
    "technology_security": _entry("网络安全", "technology", ["security", "安全", "网络安全"]),
    "technology_blockchain": _entry("区块链", "technology", ["blockchain", "区块链", "web3"]),
    "technology_mobile": _entry("移动开发", "technology", ["mobile", "移动开发", "安卓", "ios"]),
    "technology_hardware": _entry("硬件与芯片", "technology", ["hardware", "芯片", "半导体", "硬件"]),
    "technology_open_source": _entry("开源生态", "technology", ["open_source", "开源", "github"]),
    "technology_devtools": _entry("开发工具", "technology", ["devtools", "开发工具", "编程工具"]),
    "technology_networking": _entry("网络与通信", "technology", ["networking", "网络", "通信"]),
    "technology_robotics": _entry("机器人", "technology", ["robotics", "机器人"]),
    "technology_iot": _entry("物联网", "technology", ["iot", "物联网"]),
    "technology_ar_vr": _entry("AR VR", "technology", ["ar", "vr", "xr", "ar_vr"]),
    "science_ml": _entry("机器学习", "science", ["ml", "机器学习"]),
    "science_math": _entry("数学", "science", ["math", "数学"]),
    "science_biology": _entry("生物科学", "science", ["biology", "生物", "生命科学"]),
    "science_medicine": _entry("医学研究", "science", ["medicine", "医学", "医药研究"]),
    "science_chemistry": _entry("化学", "science", ["chemistry", "化学"]),
    "science_physics": _entry("物理", "science", ["physics", "物理"]),
    "science_astronomy": _entry("天文学", "science", ["astronomy", "天文"]),
    "science_environment": _entry("环境科学", "science", ["environment", "环境", "气候"]),
    "science_geography": _entry("地理与地学", "science", ["geography", "地理", "地学"]),
    "finance_stock": _entry("股票与证券", "finance", ["stock", "股票", "证券", "a股", "美股"]),
    "finance_macro": _entry("宏观经济", "finance", ["macro", "宏观", "宏观经济"]),
    "finance_company_filing": _entry("公司公告", "finance", ["company_filing", "公告", "公司公告", "财报"]),
    "finance_banking": _entry("银行金融", "finance", ["banking", "银行", "金融机构"]),
    "finance_fund": _entry("基金与资管", "finance", ["fund", "基金", "资管"]),
    "finance_crypto": _entry("加密资产", "finance", ["crypto", "加密货币", "数字资产"]),
    "finance_real_estate": _entry("房地产金融", "finance", ["real_estate", "房地产"]),
    "finance_consumer": _entry("消费行业", "finance", ["consumer", "消费", "零售"]),
    "finance_insurance": _entry("保险", "finance", ["insurance", "保险"]),
    "finance_energy": _entry("能源与大宗商品", "finance", ["energy", "commodity", "能源", "大宗商品"]),
    "policy_regulation": _entry("政策法规", "policy", ["policy", "regulation", "政策", "法规"]),
    "policy_government_notice": _entry("政府通知", "policy", ["government_notice", "政府通知", "政府公告"]),
    "policy_legal_case": _entry("法律案例", "policy", ["legal_case", "法律案例", "司法"]),
    "policy_tax": _entry("税务", "policy", ["tax", "税务", "纳税"]),
    "policy_trade": _entry("贸易政策", "policy", ["trade", "贸易", "关税"]),
    "policy_education": _entry("教育政策", "policy", ["education_policy", "教育政策"]),
    "policy_health": _entry("卫生政策", "policy", ["health_policy", "卫生政策"]),
    "business_company": _entry("公司经营", "business", ["company", "企业", "公司"]),
    "business_product": _entry("产品动态", "business", ["product", "产品"]),
    "business_pricing": _entry("定价与计费", "business", ["pricing", "billing", "定价", "计费"]),
    "business_marketing": _entry("市场营销", "business", ["marketing", "市场营销"]),
    "business_sales": _entry("销售", "business", ["sales", "销售"]),
    "business_supply_chain": _entry("供应链", "business", ["supply_chain", "供应链", "物流"]),
    "business_hr": _entry("人力资源", "business", ["hr", "人力资源", "招聘"]),
    "business_startup": _entry("创业与融资", "business", ["startup", "创业", "融资"]),
    "business_management": _entry("组织管理", "business", ["management", "管理", "组织管理"]),
    "healthcare_clinical": _entry("临床证据", "healthcare", ["clinical", "临床", "临床证据"]),
    "healthcare_guideline": _entry("医学指南", "healthcare", ["guideline", "指南", "医学指南"]),
    "healthcare_drug": _entry("药物信息", "healthcare", ["drug", "药物", "药品"]),
    "healthcare_device": _entry("医疗器械", "healthcare", ["device", "医疗器械"]),
    "healthcare_public_health": _entry("公共卫生", "healthcare", ["public_health", "公共卫生"]),
    "healthcare_nutrition": _entry("营养健康", "healthcare", ["nutrition", "营养", "营养健康"]),
    "education_course": _entry("课程学习", "education", ["course", "课程"]),
    "education_admission": _entry("招生录取", "education", ["admission", "招生", "录取"]),
    "education_exam": _entry("考试测评", "education", ["exam", "考试", "测评"]),
    "education_k12": _entry("K12教育", "education", ["k12", "基础教育", "k12教育"]),
    "education_higher_ed": _entry("高等教育", "education", ["higher_ed", "高等教育", "大学"]),
    "education_language": _entry("语言学习", "education", ["language_learning", "语言学习", "英语学习"]),
    "entertainment_anime": _entry("动漫", "entertainment", ["anime", "动漫", "动画", "二次元"]),
    "entertainment_comics": _entry("漫画", "entertainment", ["comics", "漫画"]),
    "entertainment_gaming": _entry("游戏", "entertainment", ["gaming", "game", "游戏", "电子游戏"]),
    "entertainment_mobile_gaming": _entry("手游", "entertainment", ["mobile_game", "手游", "移动游戏"]),
    "entertainment_console_gaming": _entry("主机游戏", "entertainment", ["console_game", "主机游戏"]),
    "entertainment_pc_gaming": _entry("PC 游戏", "entertainment", ["pc_game", "pc游戏", "端游"]),
    "entertainment_film": _entry("电影", "entertainment", ["film", "movie", "电影"]),
    "entertainment_tv": _entry("剧集综艺", "entertainment", ["tv", "drama", "综艺", "电视剧"]),
    "entertainment_music": _entry("音乐", "entertainment", ["music", "音乐"]),
    "entertainment_celebrity": _entry("明星娱乐", "entertainment", ["celebrity", "明星", "娱乐八卦"]),
    "entertainment_streaming": _entry("直播与主播", "entertainment", ["streaming", "直播", "主播"]),
    "fashion_apparel": _entry("服饰穿搭", "fashion", ["apparel", "穿搭", "服饰", "时装"]),
    "fashion_luxury": _entry("奢侈品", "fashion", ["luxury", "奢侈品"]),
    "fashion_beauty": _entry("美妆个护", "fashion", ["beauty", "美妆", "护肤"]),
    "fashion_jewelry": _entry("珠宝配饰", "fashion", ["jewelry", "珠宝", "配饰"]),
    "fashion_runway": _entry("时装秀", "fashion", ["runway", "时装周", "时装秀"]),
    "fashion_streetwear": _entry("潮流文化", "fashion", ["streetwear", "潮流", "街头时尚"]),
    "sports_football": _entry("足球", "sports", ["football", "soccer", "足球"]),
    "sports_basketball": _entry("篮球", "sports", ["basketball", "篮球"]),
    "sports_esports": _entry("电子竞技", "sports", ["esports", "电竞", "电子竞技"]),
    "sports_fitness": _entry("健身", "sports", ["fitness", "健身", "运动训练"]),
    "sports_olympics": _entry("奥运体育", "sports", ["olympics", "奥运", "竞技体育"]),
    "sports_tennis": _entry("网球", "sports", ["tennis", "网球"]),
    "sports_motorsport": _entry("赛车", "sports", ["motorsport", "赛车", "f1"]),
    "lifestyle_food": _entry("美食餐饮", "lifestyle", ["food", "美食", "餐饮"]),
    "lifestyle_travel": _entry("旅游出行", "lifestyle", ["travel", "旅游", "出行"]),
    "lifestyle_automotive": _entry("汽车", "lifestyle", ["automotive", "car", "汽车"]),
    "lifestyle_home": _entry("家居生活", "lifestyle", ["home", "家居", "生活方式"]),
    "lifestyle_parenting": _entry("亲子育儿", "lifestyle", ["parenting", "育儿", "亲子"]),
    "lifestyle_pet": _entry("宠物", "lifestyle", ["pet", "宠物"]),
    "lifestyle_wedding": _entry("婚礼婚庆", "lifestyle", ["wedding", "婚礼", "婚庆"]),
    "lifestyle_outdoor": _entry("户外露营", "lifestyle", ["outdoor", "露营", "户外"]),
    "news_breaking": _entry("突发新闻", "news", ["breaking_news", "突发新闻", "快讯"]),
    "news_investigation": _entry("深度报道", "news", ["investigation", "调查报道", "深度报道"]),
    "news_opinion": _entry("评论观点", "news", ["opinion", "评论", "观点"]),
    "culture_art": _entry("艺术", "culture", ["art", "艺术"]),
    "culture_history": _entry("历史", "culture", ["history", "历史"]),
    "culture_literature": _entry("文学", "culture", ["literature", "文学"]),
    "culture_photography": _entry("摄影", "culture", ["photography", "摄影"]),
    "culture_museum": _entry("博物馆与展览", "culture", ["museum", "博物馆", "展览"]),
    "culture_design": _entry("设计", "culture", ["design", "设计", "视觉设计"]),
}

_TAG_SANITIZE_RE = re.compile(r"[^0-9a-z_\u4e00-\u9fff]+", re.IGNORECASE)


def _normalize_tag_text(raw_tag: str) -> str:
    cleaned = raw_tag.strip().lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"[\s/\-]+", "_", cleaned)
    cleaned = _TAG_SANITIZE_RE.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:128]


STATIC_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical_tag, payload in STATIC_TAG_DEFINITIONS.items():
    STATIC_ALIAS_TO_CANONICAL[_normalize_tag_text(canonical_tag)] = canonical_tag
    for alias in payload["aliases"]:
        STATIC_ALIAS_TO_CANONICAL[_normalize_tag_text(alias)] = canonical_tag


def sync_static_taxonomy() -> None:
    global _static_synced
    if _static_synced:
        return
    with _tables_lock:
        if _static_synced:
            return
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    for canonical_tag, payload in STATIC_TAG_DEFINITIONS.items():
                        cur.execute(
                            """
                            INSERT INTO tag_taxonomy
                                (canonical_tag, display_name, tag_group, status, protected, is_static)
                            VALUES (%s, %s, %s, 'active', TRUE, TRUE)
                            ON CONFLICT (canonical_tag) DO UPDATE SET
                                display_name = EXCLUDED.display_name,
                                tag_group = EXCLUDED.tag_group,
                                status = 'active',
                                protected = TRUE,
                                is_static = TRUE,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (canonical_tag, payload["display_name"], payload["tag_group"]),
                        )
            _static_synced = True
        except Exception:
            logger.exception("failed to sync static tag taxonomy")


def _load_dynamic_mappings(candidate_tags: list[str]) -> dict[str, str]:
    if not candidate_tags:
        return {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tag_name, mapped_canonical_tag
                    FROM tag_dynamic_registry
                    WHERE tag_name = ANY(%s)
                      AND status IN ('active', 'merged')
                      AND mapped_canonical_tag IS NOT NULL
                    """,
                    (candidate_tags,),
                )
                return {
                    row[0]: row[1]
                    for row in cur.fetchall()
                    if row[1]
                }
    except Exception:
        logger.exception("failed to load dynamic tag mappings")
        return {}


def _record_dynamic_tags(dynamic_tags: list[str]) -> None:
    if not dynamic_tags:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for tag_name in dynamic_tags:
                    cur.execute(
                        """
                        INSERT INTO tag_dynamic_registry (tag_name, usage_count, status)
                        VALUES (%s, 1, 'active')
                        ON CONFLICT (tag_name) DO UPDATE SET
                            usage_count = tag_dynamic_registry.usage_count + 1,
                            last_seen_at = CURRENT_TIMESTAMP,
                            status = CASE
                                WHEN tag_dynamic_registry.status = 'deprecated' THEN 'active'
                                ELSE tag_dynamic_registry.status
                            END
                        """,
                        (tag_name,),
                    )
    except Exception:
        logger.exception("failed to record dynamic tags")


def resolve_tags(raw_tags: list[str], *, max_canonical_tags: int = 8) -> dict[str, list[str]]:
    sync_static_taxonomy()

    normalized_unique: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_tags:
        if not isinstance(raw_tag, str):
            continue
        normalized = _normalize_tag_text(raw_tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_unique.append(normalized)

    dynamic_mappings = _load_dynamic_mappings(normalized_unique)
    canonical_tags = OrderedDict()
    dynamic_tags: list[str] = []

    for normalized in normalized_unique:
        canonical_tag = STATIC_ALIAS_TO_CANONICAL.get(normalized) or dynamic_mappings.get(normalized)
        if canonical_tag:
            canonical_tags[canonical_tag] = None
            continue
        dynamic_tags.append(normalized)

    _record_dynamic_tags(dynamic_tags)

    return {
        "canonical_tags": list(canonical_tags.keys())[:max_canonical_tags],
        "dynamic_tags": dynamic_tags,
    }


def get_dynamic_tag_usage_report(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tag_name, mapped_canonical_tag, usage_count, status, first_seen_at, last_seen_at, backfilled_at
                FROM tag_dynamic_registry
                ORDER BY usage_count DESC, last_seen_at DESC
                LIMIT %s
                """,
                (max(1, min(limit, 1000)),),
            )
            return [
                {
                    "tag_name": row[0],
                    "mapped_canonical_tag": row[1],
                    "usage_count": int(row[2]),
                    "status": row[3],
                    "first_seen_at": row[4].isoformat() if row[4] else None,
                    "last_seen_at": row[5].isoformat() if row[5] else None,
                    "backfilled_at": row[6].isoformat() if row[6] else None,
                }
                for row in cur.fetchall()
            ]


def _topic_score(alpha: float, beta: float) -> tuple[float, float]:
    total = alpha + beta
    if total <= 0:
        return 1.0, 0.0
    return round((alpha / total) * 2, 2), round(min(1.0, total / 30.0), 2)


def _merge_topic_tag_list(topic_tags: list[str] | None, old_tag: str, canonical_tag: str) -> list[str]:
    merged = OrderedDict()
    for tag in topic_tags or []:
        normalized = _normalize_tag_text(tag)
        if not normalized:
            continue
        if normalized == old_tag:
            normalized = canonical_tag
        merged[normalized] = None
    if canonical_tag not in merged and old_tag in (topic_tags or []):
        merged[canonical_tag] = None
    return list(merged.keys())


def _backfill_topic_reputation(cur, old_tag: str, canonical_tag: str) -> int:
    cur.execute(
        """
        SELECT source_id, SUM(alpha) AS alpha_sum, SUM(beta) AS beta_sum
        FROM source_topic_reputation
        WHERE topic_tag = ANY(%s)
        GROUP BY source_id
        """,
        ([old_tag, canonical_tag],),
    )
    rows = cur.fetchall()
    for source_id, alpha_sum, beta_sum in rows:
        reputation_score, confidence = _topic_score(float(alpha_sum or 0), float(beta_sum or 0))
        cur.execute(
            """
            INSERT INTO source_topic_reputation
                (source_id, topic_tag, alpha, beta, reputation_score, confidence)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, topic_tag) DO UPDATE SET
                alpha = EXCLUDED.alpha,
                beta = EXCLUDED.beta,
                reputation_score = EXCLUDED.reputation_score,
                confidence = EXCLUDED.confidence,
                updated_at = CURRENT_TIMESTAMP
            """,
            (source_id, canonical_tag, float(alpha_sum or 0), float(beta_sum or 0), reputation_score, confidence),
        )
    cur.execute(
        """
        DELETE FROM source_topic_reputation
        WHERE topic_tag = %s
        """,
        (old_tag,),
    )
    return len(rows)


def _backfill_article_topics(cur, old_tag: str, canonical_tag: str) -> int:
    cur.execute(
        """
        SELECT article_id, topic_tags, canonical_topic_tag
        FROM article_sources
        WHERE canonical_topic_tag = %s
           OR (topic_tags IS NOT NULL AND topic_tags ? %s)
        """,
        (old_tag, old_tag),
    )
    rows = cur.fetchall()
    touched = 0
    for article_id, topic_tags, canonical_topic_tag in rows:
        merged_tags = _merge_topic_tag_list(topic_tags, old_tag, canonical_tag)
        next_canonical = canonical_tag if canonical_topic_tag == old_tag or canonical_tag in merged_tags else canonical_topic_tag
        cur.execute(
            """
            UPDATE article_sources
            SET topic_tags = %s::jsonb,
                canonical_topic_tag = %s
            WHERE article_id = %s
            """,
            (json.dumps(merged_tags, ensure_ascii=False), next_canonical, article_id),
        )
        touched += 1
    return touched


def _execute_merge(cur, old_tag: str, canonical_tag: str) -> dict:
    topic_rows = _backfill_topic_reputation(cur, old_tag, canonical_tag)
    article_rows = _backfill_article_topics(cur, old_tag, canonical_tag)
    cur.execute(
        """
        UPDATE tag_dynamic_registry
        SET mapped_canonical_tag = %s,
            status = 'merged',
            last_seen_at = CURRENT_TIMESTAMP,
            backfilled_at = CURRENT_TIMESTAMP
        WHERE tag_name = %s
        """,
        (canonical_tag, old_tag),
    )
    return {
        "topic_rows": topic_rows,
        "article_rows": article_rows,
    }


def merge_dynamic_tag(tag_name: str, canonical_tag: str, *, backfill: bool = True) -> dict:
    sync_static_taxonomy()
    normalized_tag = _normalize_tag_text(tag_name)
    normalized_canonical = _normalize_tag_text(canonical_tag)
    canonical_resolved = STATIC_ALIAS_TO_CANONICAL.get(normalized_canonical, normalized_canonical)
    if canonical_resolved not in STATIC_TAG_DEFINITIONS:
        raise ValueError(f"unknown canonical tag: {canonical_tag}")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tag_dynamic_registry (tag_name, mapped_canonical_tag, usage_count, status, backfilled_at)
                VALUES (%s, %s, 0, 'merged', CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT (tag_name) DO UPDATE SET
                    mapped_canonical_tag = EXCLUDED.mapped_canonical_tag,
                    status = 'merged',
                    last_seen_at = CURRENT_TIMESTAMP,
                    backfilled_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE tag_dynamic_registry.backfilled_at END
                """,
                (normalized_tag, canonical_resolved, backfill, backfill),
            )
            result = {"topic_rows": 0, "article_rows": 0}
            if backfill:
                result = _execute_merge(cur, normalized_tag, canonical_resolved)
    return {
        "tag_name": normalized_tag,
        "canonical_tag": canonical_resolved,
        "backfill": backfill,
        **result,
    }


def apply_pending_tag_merges(limit: int = 100) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tag_name, mapped_canonical_tag
                FROM tag_dynamic_registry
                WHERE status = 'merged'
                  AND mapped_canonical_tag IS NOT NULL
                  AND backfilled_at IS NULL
                ORDER BY last_seen_at ASC
                LIMIT %s
                """,
                (max(1, min(limit, 1000)),),
            )
            rows = cur.fetchall()
            processed = []
            for old_tag, canonical_tag in rows:
                outcome = _execute_merge(cur, old_tag, canonical_tag)
                processed.append(
                    {
                        "tag_name": old_tag,
                        "canonical_tag": canonical_tag,
                        **outcome,
                    }
                )
    return {"processed_count": len(processed), "processed": processed[:50]}


def prune_dynamic_tags(*, stale_months: int = 18, max_usage: int = 2) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM tag_dynamic_registry
                WHERE status = 'active'
                  AND mapped_canonical_tag IS NULL
                  AND usage_count <= %s
                  AND last_seen_at < (CURRENT_TIMESTAMP - (%s || ' months')::interval)
                RETURNING tag_name
                """,
                (max(0, max_usage), max(1, stale_months)),
            )
            deleted = [row[0] for row in cur.fetchall()]
    if deleted:
        logger.info("pruned dynamic tags: %s", ", ".join(deleted[:20]))
    return {"deleted_count": len(deleted), "deleted_tags": deleted[:100]}
