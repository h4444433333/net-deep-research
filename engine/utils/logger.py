"""
北京时间（UTC+8）+ 毫秒级精度的日志模块。

所有日志输出格式:
  [2026-07-22 15:30:45.123] [INFO] [module] message

同时输出到:
  - stdout（Docker logs 捕获）
  - 文件 {NET_INFO_LOG_DIR}/api.log（由环境变量决定；本地/线上 compose 默认挂载到宿主机 logs 目录）
"""

from __future__ import annotations

import fcntl
import glob
import logging
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

LOG_DIR = os.environ.get("NET_INFO_LOG_DIR", "/var/log/net_info")
LOG_FILE = os.path.join(LOG_DIR, "api.log")
USER_LOG_FILE = os.path.join(LOG_DIR, "user.log")
LOG_LEVEL = os.environ.get("NET_INFO_LOG_LEVEL", "INFO")
LOG_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("NET_INFO_LOG_CLEANUP_INTERVAL_SECONDS", str(3 * 24 * 3600)))
LOG_CLEANUP_CHECK_SECONDS = int(os.environ.get("NET_INFO_LOG_CLEANUP_CHECK_SECONDS", "60"))
LOG_ARCHIVE_COUNT = int(os.environ.get("NET_INFO_LOG_ARCHIVE_COUNT", "8"))

_BEIJING_TZ = timezone(timedelta(hours=8))


class BeijingFormatter(logging.Formatter):
    """格式化为北京时间 + 毫秒"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_BEIJING_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f".{int(dt.microsecond / 1000):03d}"

    def format(self, record):
        ts = self.formatTime(record)
        level = record.levelname
        module = record.name
        msg = record.getMessage()
        rendered = f"[{ts}] [{level}] [{module}] {msg}"

        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"

        if record.stack_info:
            rendered = f"{rendered}\n{self.formatStack(record.stack_info)}"

        return rendered


def _ensure_log_dir() -> None:
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)


class ManagedWeeklyCleanupFileHandler(logging.FileHandler):
    """多进程共享的文件日志 handler，按周归档并清理旧日志。"""

    def __init__(self, filename: str, encoding: str | None = None):
        super().__init__(filename, mode="a", encoding=encoding, delay=False)
        self._lock_path = f"{filename}.lock"
        self._state_path = f"{filename}.cleanup.ts"
        self._last_check_at = 0.0

    @contextmanager
    def _lock(self):
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock():
            self._maybe_cleanup_locked(record.created)
            super().emit(record)

    def _maybe_cleanup_locked(self, now_ts: float) -> None:
        if now_ts - self._last_check_at < LOG_CLEANUP_CHECK_SECONDS:
            return
        self._last_check_at = now_ts

        last_cleanup_at = self._read_last_cleanup_at()
        if last_cleanup_at and (now_ts - last_cleanup_at) < LOG_CLEANUP_INTERVAL_SECONDS:
            return

        if os.path.exists(self.baseFilename) and os.path.getsize(self.baseFilename) > 0:
            archive_suffix = datetime.fromtimestamp(now_ts, tz=_BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
            archive_path = f"{self.baseFilename}.{archive_suffix}"

            if self.stream:
                self.stream.flush()

            shutil.copy2(self.baseFilename, archive_path)
            with open(self.baseFilename, "r+", encoding=self.encoding or "utf-8") as log_file:
                log_file.truncate(0)

        self._prune_archives_locked()
        self._write_last_cleanup_at(now_ts)

    def _read_last_cleanup_at(self) -> float | None:
        try:
            with open(self._state_path, "r", encoding="utf-8") as state_file:
                raw = state_file.read().strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _write_last_cleanup_at(self, cleanup_ts: float) -> None:
        tmp_path = f"{self._state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as state_file:
            state_file.write(str(cleanup_ts))
        os.replace(tmp_path, self._state_path)

    def _prune_archives_locked(self) -> None:
        archive_candidates = [
            path
            for path in glob.glob(f"{self.baseFilename}.*")
            if path not in {self._lock_path, self._state_path, f"{self._state_path}.tmp"}
        ]
        archive_candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        for stale_path in archive_candidates[LOG_ARCHIVE_COUNT:]:
            try:
                os.remove(stale_path)
            except FileNotFoundError:
                continue
            except OSError:
                continue


def _build_logger(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    formatter = BeijingFormatter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _ensure_log_dir()
    file_handler = ManagedWeeklyCleanupFileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    获取指定模块的 logger。

    用法:
        from utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("GET /v1/sources?domain=react.dev - 200 (5ms)")
    """
    return _build_logger(name, LOG_FILE)


def get_user_logger(name: str = "user") -> logging.Logger:
    """获取用户会话溯源日志 logger，输出到 user.log。"""
    return _build_logger(name, USER_LOG_FILE)
