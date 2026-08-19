"""
信源信誉系统 — 总入口。

支持两种运行模式:
  1. 阿里云函数计算 HTTP 触发器 → handler(event, context)
  2. Docker / gunicorn → create_app() 返回 Flask 实例

日志: 北京时间 + 毫秒级精度，同时输出到 stdout 和文件。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handlers.check import handler as check_handler
from handlers.feedback import handler as feedback_handler
from handlers.offnet import handler as offnet_handler
from handlers.sources import handler as sources_handler
from handlers.vote import handler as vote_handler
from jobs.scheduler import run_job, start_background_jobs
from services.feedback_write_worker import start_feedback_write_worker
from services.request_guard import enforce_request_limits
from services.runtime_metrics import begin_request, finish_request
from services.test_access import enforce_test_access
from utils.health_probe import record_health_probe
from utils.logger import get_logger
from utils.request_trace import (
    begin_request_trace,
    clear_request_trace,
    get_request_trace_payload,
    log_external_user_call,
    log_trace_exception,
    log_trace_node,
    log_trace_response,
)

logger = get_logger("api")


def _health_response(event: dict) -> dict:
    access_response = enforce_test_access("/health", "GET", event)
    if access_response is not None:
        return access_response

    headers = event.get("headers") or {}
    client_ip = (
        headers.get("X-Forwarded-For")
        or headers.get("x-forwarded-for")
        or headers.get("X-Real-IP")
        or headers.get("x-real-ip")
        or "unknown"
    )
    record_health_probe(str(client_ip).split(",", 1)[0].strip())
    return _json_ok({"status": "ok"})


# #region debug-point B:route-runtime
def _debug_event(hypothesis_id: str, location: str, msg: str, data: dict | None = None) -> None:
    payload = {
        "sessionId": "core-loop-runtime-block",
        "runId": "pre-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "msg": f"[DEBUG] {msg}",
        "data": data or {},
        "ts": int(time.time() * 1000),
    }
    debug_url = "http://127.0.0.1:7777/event"
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        ".dbg",
        "core-loop-runtime-block.env",
    )
    try:
        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if line.startswith("DEBUG_SERVER_URL="):
                    debug_url = line.split("=", 1)[1].strip() or debug_url
                    break
    except OSError:
        pass
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                debug_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=1,
        ).read()
    except Exception:
        pass
# #endregion


# ---- 阿里云函数计算入口 ----

def handler(event, context):
    """
    阿里云函数计算 HTTP 触发器入口。
    根据请求路径路由到对应 handler。
    """
    path = event.get("path", "")
    method = event.get("httpMethod", "GET")

    if path == "/health":
        return _health_response(event)

    begin_request()
    t0 = time.time()
    status = 500
    begin_request_trace(path, method, event)
    try:
        log_trace_node("route.dispatch", "route dispatch start")
        result = _route(path, method, event, context)
        status = result.get("statusCode", 500)
        log_trace_response(status, _decode_response_body(result))
        return result
    except Exception as exc:
        log_trace_exception("route.dispatch", exc, message="unhandled exception escaped main handler")
        raise
    finally:
        elapsed_ms = int((time.time() - t0) * 1000)
        _log_access(method, path, status, elapsed_ms, event.get("headers"))
        clear_request_trace()


# ---- Flask / Docker 入口 ----

def create_app():
    """创建 Flask 应用，供 gunicorn 启动。"""
    from flask import Flask, request

    app = Flask(__name__)

    @app.route("/health")
    def health():
        result = _health_response(
            {
                "path": "/health",
                "httpMethod": "GET",
                "headers": dict(request.headers),
            }
        )
        return (
            result.get("body", "{}"),
            result.get("statusCode", 500),
            result.get("headers", {"Content-Type": "application/json"}),
        )

    @app.route("/v1/sources/check", methods=["POST"])
    def flask_sources_check():
        return _flask_route(check_handler, request)

    @app.route("/v1/sources/vote", methods=["POST"])
    def flask_vote():
        return _flask_route(vote_handler, request)

    @app.route("/v1/sources/search", methods=["GET"])
    def flask_sources_search():
        return _flask_route(sources_handler, request)

    @app.route("/v1/articles/search", methods=["GET"])
    def flask_articles_search():
        return _flask_route(sources_handler, request)

    @app.route("/v1/sources", methods=["GET"])
    def flask_sources():
        return _flask_route(sources_handler, request)

    @app.route("/v1/research-feedback", methods=["POST"])
    def flask_feedback():
        return _flask_route(feedback_handler, request)

    @app.route("/v1/offnet-analysis", methods=["POST"])
    def flask_offnet_analysis():
        return _flask_route(offnet_handler, request)

    @app.route("/v1/internal/jobs/run", methods=["POST"])
    def flask_jobs_run():
        return _flask_route(_jobs_handler, request)

    start_background_jobs()
    start_feedback_write_worker()
    return app


def _flask_route(fn, flask_request):
    """将 Flask request 转为 FC event 格式，调用 handler，再转回 Flask response。"""
    # 解析 query string: "a=1&b=2" → {"a": "1", "b": "2"}
    params = {}
    for k, v in flask_request.args.items(multi=False):
        params[k] = v

    event = {
        "path": flask_request.path,
        "httpMethod": flask_request.method,
        "queryParameters": params,
        "headers": dict(flask_request.headers),
        "body": flask_request.get_data(as_text=True) or "{}",
    }

    method = flask_request.method
    path = flask_request.path
    request_token = begin_request()
    t0 = time.time()
    status = 500
    begin_request_trace(path, method, event)
    try:
        log_trace_node("route.dispatch", "route dispatch start")
        result = _route(path, method, event, None)
        status = result.get("statusCode", 500)
        log_trace_response(status, _decode_response_body(result))
        return (
            result.get("body", "{}"),
            result.get("statusCode", 500),
            result.get("headers", {"Content-Type": "application/json"}),
        )
    except Exception as exc:
        log_trace_exception("route.dispatch", exc, message="unhandled exception escaped flask route")
        raise
    finally:
        elapsed_ms = int((time.time() - t0) * 1000)
        _log_access(method, path, status, elapsed_ms, event.get("headers"), request_token=request_token)
        clear_request_trace()


# ---- 路由分发 ----

def _route(path: str, method: str, event: dict, context):
    # #region debug-point B:route-entry
    _debug_event(
        "B",
        "main._route:163",
        "route dispatch start",
        {"path": path, "method": method},
    )
    # #endregion
    access_response = enforce_test_access(path, method, event)
    if access_response is not None:
        return access_response

    limit_response = enforce_request_limits(path, method, event)
    if limit_response is not None:
        log_trace_node(
            "request_guard",
            "request blocked by request guard",
            level="warning",
            data={"status_code": limit_response.get("statusCode", 429)},
        )
        return limit_response

    # /v1/sources/check 必须在泛匹配 /sources 之前
    if path.endswith("/sources/check"):
        if method != "POST":
            return _json_error(405, "method not allowed")
        return check_handler(event, context)

    if path.endswith("/sources/vote"):
        if method != "POST":
            return _json_error(405, "method not allowed")
        return vote_handler(event, context)

    if path.endswith("/research-feedback"):
        if method != "POST":
            return _json_error(405, "method not allowed")
        return feedback_handler(event, context)

    if path.endswith("/offnet-analysis"):
        if method != "POST":
            return _json_error(405, "method not allowed")
        return offnet_handler(event, context)

    if path.endswith("/internal/jobs/run"):
        if method != "POST":
            return _json_error(405, "method not allowed")
        return _jobs_handler(event, context)

    if path.endswith("/articles/search"):
        if method != "GET":
            return _json_error(405, "method not allowed")
        # #region debug-point B:articles-route
        _debug_event(
            "B",
            "main._route:189",
            "articles search routed to sources handler",
            {"path": path, "method": method},
        )
        # #endregion
        return sources_handler(event, context)

    if "/sources" in path:
        return sources_handler(event, context)

    # #region debug-point B:route-404
    _debug_event(
        "B",
        "main._route:201",
        "route dispatch fell through to 404",
        {"path": path, "method": method},
    )
    # #endregion
    return _json_error(404, "not found")


def _log_access(
    method: str,
    path: str,
    status: int,
    elapsed_ms: int,
    headers: dict | None,
    *,
    request_token: str | None = None,
) -> None:
    trace_payload = get_request_trace_payload()
    metrics_snapshot, rollover_summary = finish_request(
        status_code=status,
        elapsed_ms=elapsed_ms,
        request_token=request_token,
    )
    if rollover_summary:
        logger.info("daily skill call summary: %s", json.dumps(rollover_summary, ensure_ascii=False))
    logger.info(
        "%s %s - %d (%dms) | class=%s | skill_calls=%s",
        method,
        path,
        status,
        elapsed_ms,
        trace_payload.get("traffic_class", "unknown"),
        json.dumps(metrics_snapshot, ensure_ascii=False),
    )
    log_external_user_call(status, elapsed_ms)


def _jobs_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return _json_error(400, "invalid JSON")

    job_name = str(body.get("job", "all")).strip() or "all"
    options = body.get("options")
    if options is not None and not isinstance(options, dict):
        return _json_error(400, "options must be an object")
    try:
        result = run_job(job_name, options=options, operator="manual")
    except ValueError as exc:
        return _json_error(400, str(exc))
    except Exception as exc:
        logger.exception("internal job failed: %s", job_name)
        return _json_error(500, str(exc))
    return _json_ok(result)


# ---- 响应辅助 ----

def _json_ok(body: dict) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _json_error(status: int, msg: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}, ensure_ascii=False),
    }


def _decode_response_body(result: dict):
    body = result.get("body", "")
    if not isinstance(body, str):
        return body
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body
