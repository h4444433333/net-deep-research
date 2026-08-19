"""
文章质量贝叶斯评分服务。

基于字符 n-gram + feature hashing + 多项式朴素贝叶斯。
语言无关（中英文混排统一处理），不存词表，只用哈希。

冷启动：开源种子数据集（Wikipedia/ArXiv/假新闻）训练初始先验
运行时：research-feedback 综合评分后映射出的 high / low 标签增量更新

三种质量标签来源：
- high: 综合评分后的高质量标签
- low:  综合评分后的低质量标签
- seed: 开源数据集的标签（权重 0.3，真实反馈权重可变）
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
FEATURE_SPACE = 262144          # 2^18，哈希空间
NGRAM_MIN = 2                   # 最小 n-gram 长度（字符）
NGRAM_MAX = 4                   # 最大 n-gram 长度
DEFAULT_SCORE = 0.50            # 模型冷时返回的默认分
SEED_WEIGHT = 0.3               # 种子数据权重（低于 LLM 真实行为）
LLM_WEIGHT = 1.0                # research-feedback 默认权重
LAPLACE_ALPHA = 1.0             # 拉普拉斯平滑系数

# 加载阈值：种子训练的总有效样本量达到此值才启用贝叶斯分。
# 生产种子训练 (4500 篇 × 0.3 = 1350) 远超此值，设为 5 即可防裸奔。
_MIN_EFFECTIVE_DOCS = 5

# ---------------------------------------------------------------------------
# 特征提取
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
# 纯 ASCII 非字母字符：控制字符、空格、标点、数字。中文/Unicode 字母不受影响。
_ZERO_INFO_RE = re.compile(r"^[\x00-\x40\x5b-\x60\x7b-\x7f]+$")


def _character_ngrams(text: str) -> list[int]:
    """
    字符级 n-gram 提取 + MD5 哈希映射到固定特征空间。

    策略：
    - 统一小写
    - 空白规范化
    - 跳过纯标点/数字 gram（无信息量）
    - 同一文本内去重（避免长文章对短文章的优势偏差）
    """
    if not text:
        return []

    text = _WHITESPACE_RE.sub(" ", text.lower()).strip()
    if not text or len(text) < NGRAM_MIN:
        return []

    seen: set[int] = set()
    features: list[int] = []

    for n in range(NGRAM_MIN, NGRAM_MAX + 1):
        if n > len(text):
            continue
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            if _ZERO_INFO_RE.match(gram):
                continue
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % FEATURE_SPACE
            if idx not in seen:
                seen.add(idx)
                features.append(idx)

    return features


# ---------------------------------------------------------------------------
# 数据库操作
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "nb_quality.db")


def _open_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")         # 8MB 缓存
    conn.execute("PRAGMA mmap_size=268435456")       # 256MB mmap，读多写少场景
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS class_priors (
            class     TEXT PRIMARY KEY,
            doc_count REAL NOT NULL DEFAULT 0,
            log_prior REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS feature_counts (
            feature_hash INTEGER NOT NULL,
            class        TEXT    NOT NULL,
            count        REAL    NOT NULL DEFAULT 0,
            PRIMARY KEY (feature_hash, class)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS model_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)


# ---------------------------------------------------------------------------
# 核心评分器
# ---------------------------------------------------------------------------

class QualityScorer:
    """增量多项式朴素贝叶斯质量评分器。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _default_db_path()
        self._lock = threading.Lock()  # 写操作串行化
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with _open_connection(self._db_path) as conn:
            _ensure_schema(conn)

    # ---- 训练接口 ----

    def train_seed(self, high_texts: list[str], low_texts: list[str]) -> dict:
        """
        用开源种子数据训练初始先验（权重 SEED_WEIGHT）。

        返回训练统计信息。
        """
        with self._lock:
            with _open_connection(self._db_path) as conn:
                self._batch_update(
                    conn, high_texts, "high", SEED_WEIGHT,
                )
                self._batch_update(
                    conn, low_texts, "low", SEED_WEIGHT,
                )
                self._recompute_priors(conn)

            stats = self.get_stats()
            return stats

    def update_high(self, texts: list[str], weight: float = LLM_WEIGHT) -> None:
        """写入高质量标签样本。"""
        if weight <= 0:
            return
        with self._lock:
            with _open_connection(self._db_path) as conn:
                self._batch_update(conn, texts, "high", weight)
                self._recompute_priors(conn)

    def update_low(self, texts: list[str], weight: float = LLM_WEIGHT) -> None:
        """写入低质量标签样本。"""
        if weight <= 0:
            return
        with self._lock:
            with _open_connection(self._db_path) as conn:
                self._batch_update(conn, texts, "low", weight)
                self._recompute_priors(conn)

    # ---- 评分接口 ----

    def score(self, text: str) -> float:
        """
        对单篇文章文本打分。

        Returns:
            0.0 ~ 1.0，越高越像高质量文章。
            若模型尚未就绪（有效样本不足），返回 DEFAULT_SCORE。
        """
        features = _character_ngrams(text)
        if not features:
            return DEFAULT_SCORE

        with _open_connection(self._db_path) as conn:
            # 检查是否就绪
            rows = conn.execute(
                "SELECT class, doc_count, log_prior FROM class_priors"
            ).fetchall()
            if not rows:
                return DEFAULT_SCORE

            total_docs = sum(r[1] for r in rows)
            if total_docs < _MIN_EFFECTIVE_DOCS:
                return DEFAULT_SCORE

            priors: dict[str, float] = {r[0]: float(r[2]) for r in rows}

            # 获取各类别特征总计数（含平滑）
            totals: dict[str, float] = {}
            for cls in ("high", "low"):
                row = conn.execute(
                    "SELECT COALESCE(SUM(count), 0) FROM feature_counts WHERE class = ?",
                    (cls,),
                ).fetchone()
                raw = float(row[0])
                totals[cls] = raw + LAPLACE_ALPHA * FEATURE_SPACE

            # 计算每个特征在各类别下的对数概率
            log_probs: dict[str, float] = {"high": 0.0, "low": 0.0}
            for fh in features:
                for cls in ("high", "low"):
                    row = conn.execute(
                        "SELECT count FROM feature_counts WHERE feature_hash = ? AND class = ?",
                        (fh, cls),
                    ).fetchone()
                    count = (float(row[0]) if row else 0.0) + LAPLACE_ALPHA
                    log_probs[cls] += math.log(count / totals[cls])

            # 后验概率（log-sum-exp 方式防下溢）
            log_high = log_probs["high"] + priors.get("high", 0.0)
            log_low = log_probs["low"] + priors.get("low", 0.0)

            max_log = max(log_high, log_low)

            # 若两类别的 log-prob 完全相等（差异 < 1e-10），说明特征无区分力，返回默认分
            if abs(log_high - log_low) < 1e-10:
                return DEFAULT_SCORE

            prob_high = math.exp(log_high - max_log)
            prob_low = math.exp(log_low - max_log)
            score = prob_high / (prob_high + prob_low)

            return round(min(1.0, max(0.0, score)), 4)

    def is_ready(self) -> bool:
        """模型是否已积累足够数据可用于评分。"""
        with _open_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(doc_count), 0) FROM class_priors"
            ).fetchone()
            return float(row[0]) >= _MIN_EFFECTIVE_DOCS

    def get_stats(self) -> dict:
        """获取模型统计信息（监控用）。"""
        with _open_connection(self._db_path) as conn:
            rows = conn.execute(
                "SELECT class, doc_count FROM class_priors"
            ).fetchall()
            total = sum(float(r[1]) for r in rows)
            count_row = conn.execute(
                "SELECT COUNT(*) FROM feature_counts"
            ).fetchone()
            return {
                "ready": total >= _MIN_EFFECTIVE_DOCS,
                "total_effective_docs": round(total, 1),
                "class_distribution": {
                    r[0]: round(float(r[1]), 1) for r in rows
                },
                "total_unique_features": int(count_row[0]) if count_row else 0,
                "feature_space": FEATURE_SPACE,
                "db_size_mb": round(
                    os.path.getsize(self._db_path) / (1024 * 1024), 2
                ) if os.path.exists(self._db_path) else 0,
            }

    # ---- 内部方法 ----

    def _batch_update(
        self,
        conn: sqlite3.Connection,
        texts: list[str],
        cls: str,
        weight: float,
    ) -> None:
        """批量更新某个类别的特征计数。"""
        if not texts:
            return

        # 聚合所有文本的特征计数（批内合并，减少 SQL 操作）
        feature_delta: dict[int, float] = {}
        for text in texts:
            for fh in _character_ngrams(text):
                feature_delta[fh] = feature_delta.get(fh, 0.0) + weight

        # 批量写入
        conn.execute("BEGIN")
        try:
            for fh, delta in feature_delta.items():
                conn.execute(
                    """INSERT INTO feature_counts (feature_hash, class, count)
                       VALUES (?, ?, ?)
                       ON CONFLICT(feature_hash, class) DO UPDATE
                       SET count = count + excluded.count""",
                    (fh, cls, delta),
                )
            conn.execute(
                """INSERT INTO class_priors (class, doc_count)
                   VALUES (?, ?)
                   ON CONFLICT(class) DO UPDATE
                   SET doc_count = doc_count + excluded.doc_count""",
                (cls, len(texts) * weight),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _recompute_priors(conn: sqlite3.Connection) -> None:
        """重算对数先验概率。"""
        rows = conn.execute(
            "SELECT class, doc_count FROM class_priors"
        ).fetchall()
        total = sum(float(r[1]) for r in rows)
        if total <= 0:
            return
        for cls, count in rows:
            log_prior = math.log(float(count) / total)
            conn.execute(
                "UPDATE class_priors SET log_prior = ? WHERE class = ?",
                (round(log_prior, 6), cls),
            )


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_scorer: QualityScorer | None = None
_scorer_lock = threading.Lock()


def get_quality_scorer() -> QualityScorer:
    global _scorer
    if _scorer is None:
        with _scorer_lock:
            if _scorer is None:
                _scorer = QualityScorer()
    return _scorer


def reset_quality_scorer() -> None:
    """测试用：重置单例。"""
    global _scorer
    _scorer = None
