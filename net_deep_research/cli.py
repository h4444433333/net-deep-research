"""
Net Deep Research — CLI and programmatic entry point (skill host simulator).

Simulates how hosts like Trae / Claude Code execute SKILL.md: orchestrates
and writes with a user-supplied LLM (OpenAI-compatible API), gathers web
evidence with built-in search/fetch, and delegates reputation lookup, URL
safety scanning, structured feedback ingestion, and citation passport issuing
to the remote backend (default https://www.shoggoth.vip).

Command line:

    net-deep-research "What was Bun's LTS status in 2026?"
    net-deep-research --report "..."

As a library:

    from net_deep_research import research
    result = research("your question", report=True)

Scope:
- This module is the front-end orchestrator: normalization, intent decomposition,
  multi-round multi-angle search, body fetching, answer generation, structured
  feedback generation. Fully self-contained, stdlib only.
- Zero-config search fallback: Tavily when SEARCH_API_KEY is set, otherwise
  Bing scraping, then DuckDuckGo HTML.
- Feedback contract validation is owned by the backend: on a 400 the LLM fixes
  the payload per the backend errors and retries once; falls back to local
  save when the backend is unreachable.
- After feedback is accepted, a citation passport is requested and saved.

Configuration lives in a `.env` file next to this file or in the working
directory; it is auto-loaded and never overrides existing env vars.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape as _html_unescape
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration (all from env vars via .env; no hardcoded secrets)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Minimal stdlib .env loader; never overrides existing env vars.

    Lookup order: script directory, then current working directory; first
    hit wins. Only KEY=VALUE lines are parsed; # comments and optional
    surrounding quotes are supported.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, ".env"), os.path.join(os.getcwd(), ".env")):
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            continue
        break


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


LLM_API_KEY = _env("LLM_API_KEY")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = _env("LLM_MODEL", "gpt-4o-mini")

SEARCH_PROVIDER = _env("SEARCH_PROVIDER", "auto").lower()  # auto | tavily | bing | duckduckgo
SEARCH_API_KEY = _env("SEARCH_API_KEY")
SEARCH_MAX_RESULTS = max(1, int(_env("SEARCH_MAX_RESULTS", "5") or "5"))

FEEDBACK_MODE = _env("FEEDBACK_MODE", "remote").lower()  # remote | local; remote by default
FEEDBACK_API_URL = _env("FEEDBACK_API_URL", "https://www.shoggoth.vip").rstrip("/")
LLM_MAX_RETRIES = max(1, int(_env("LLM_MAX_RETRIES", "3") or "3"))

FETCH_TIMEOUT = float(_env("FETCH_TIMEOUT_SECONDS", "12") or "12")
FETCH_MAX_CHARS = int(_env("FETCH_MAX_CHARS", "8000") or "8000")
MAX_SEARCH_ROUNDS = max(1, int(_env("MAX_SEARCH_ROUNDS", "3") or "3"))
MAX_SOURCES = max(1, int(_env("MAX_SOURCES", "8") or "8"))

DEFAULT_HEADERS = {
    "User-Agent": "net-deep-research-cli/1.0",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2",
}


# ---------------------------------------------------------------------------
# Lightweight HTTP + JSON helpers (stdlib only)
# ---------------------------------------------------------------------------

def _http_json(url: str, *, method: str = "GET", headers: dict | None = None,
               payload: dict | None = None, timeout: float = 30.0) -> dict:
    data = None
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _extract_json(text: str):
    """Parse an LLM reply tolerantly: allow ```json fences and surrounding noise."""
    if not text:
        raise ValueError("empty response")
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: slice from the first { or [ to the last } or ]
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON object found")
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        raise ValueError("unbalanced JSON")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible Chat Completions)
# ---------------------------------------------------------------------------

def _llm_chat(messages: list[dict], *, temperature: float = 0.2) -> str:
    """Call the LLM with exponential-backoff retries (network jitter / rate limits / transient 5xx)."""
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set; cannot call the model.")
    last_exc: Exception | None = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            resp = _http_json(
                f"{LLM_BASE_URL}/chat/completions",
                method="POST",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                payload={
                    "model": LLM_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            try:
                return resp["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"unexpected LLM response shape: {resp}") from exc
        except Exception as exc:
            last_exc = exc
            if attempt < LLM_MAX_RETRIES:
                wait_seconds = 2.0 * attempt
                print(f"  [llm] attempt {attempt} failed ({type(exc).__name__}), "
                      f"retrying in {wait_seconds:.0f}s", file=sys.stderr)
                time.sleep(wait_seconds)
    raise RuntimeError(f"LLM call failed after {LLM_MAX_RETRIES} retries: {last_exc}")


def _llm_json(messages: list[dict], *, temperature: float = 0.0) -> dict:
    raw = _llm_chat(messages, temperature=temperature)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"expected a JSON object, got: {type(parsed).__name__}")
    return parsed


# ---------------------------------------------------------------------------
# Search client: zero-config fallback chain Tavily (with key) -> Bing -> DuckDuckGo HTML
# ---------------------------------------------------------------------------

def _strip_tags(fragment: str) -> str:
    return _html_unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


_BING_RESULT_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BING_LINK_RE = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>')


def _search_bing(query: str, max_results: int) -> list[dict]:
    """Key-free fallback: scrape and parse Bing web results.

    No API key needed, good for local trials; less stable than Tavily
    (captchas / layout drift), so prefer Tavily in production.
    """
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "q": query, "count": str(max_results), "setlang": "zh-Hans",
    })
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        page_html = resp.read().decode("utf-8", errors="replace")

    results: list[dict] = []
    for block in _BING_RESULT_RE.findall(page_html):
        link = _BING_LINK_RE.search(block)
        if not link:
            continue
        href = _html_unescape(link.group(1)).strip()
        if not href.startswith("http"):
            continue
        snippet_match = re.search(r"<p[^>]*>([\s\S]*?)</p>", block)
        results.append({
            "url": href,
            "title": _strip_tags(link.group(2)),
            "snippet": _strip_tags(snippet_match.group(1)) if snippet_match else "",
        })
        if len(results) >= max_results:
            break
    return results


def _search_tavily(query: str, max_results: int) -> list[dict]:
    """Tavily search (requires SEARCH_API_KEY); the most stable channel."""
    if not SEARCH_API_KEY:
        raise RuntimeError("SEARCH_API_KEY is not set")
    resp = _http_json(
        "https://api.tavily.com/search",
        method="POST",
        payload={
            "api_key": SEARCH_API_KEY,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
        },
        timeout=30.0,
    )
    results: list[dict] = []
    for item in resp.get("results", []) or []:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        results.append({
            "url": url,
            "title": (item.get("title") or "").strip(),
            "snippet": (item.get("content") or "").strip(),
        })
    return results


_DDG_LINK_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', re.I)
_DDG_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', re.I)


def _ddg_real_url(href: str) -> str:
    """DDG HTML links are /l/?uddg=<encoded> redirects; unwrap the real URL."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = (urllib.parse.parse_qs(parsed.query).get("uddg") or [""])[0]
        if target:
            return target
    return href


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """Key-free last resort: DuckDuckGo HTML endpoint (less likely to be blocked than Bing)."""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        page_html = resp.read().decode("utf-8", errors="replace")

    links = _DDG_LINK_RE.findall(page_html)
    snippets = _DDG_SNIPPET_RE.findall(page_html)
    results: list[dict] = []
    for index, (href, title_html) in enumerate(links):
        real_url = _ddg_real_url(_html_unescape(href).strip())
        if not real_url.startswith("http"):
            continue
        results.append({
            "url": real_url,
            "title": _strip_tags(title_html),
            "snippet": _strip_tags(snippets[index]) if index < len(snippets) else "",
        })
        if len(results) >= max_results:
            break
    return results


_SEARCH_PROVIDERS = {
    "tavily": _search_tavily,
    "bing": _search_bing,
    "duckduckgo": _search_duckduckgo,
}


def _search_provider_chain() -> list[tuple[str, callable]]:
    """An explicit SEARCH_PROVIDER uses that channel only; auto falls back by key availability."""
    if SEARCH_PROVIDER not in ("", "auto"):
        fn = _SEARCH_PROVIDERS.get(SEARCH_PROVIDER)
        if fn is None:
            raise RuntimeError(f"unsupported SEARCH_PROVIDER: {SEARCH_PROVIDER} "
                               f"(supported: auto | tavily | bing | duckduckgo)")
        return [(SEARCH_PROVIDER, fn)]
    chain: list[tuple[str, callable]] = []
    if SEARCH_API_KEY:
        chain.append(("tavily", _search_tavily))
    chain.append(("bing", _search_bing))
    chain.append(("duckduckgo", _search_duckduckgo))
    return chain


def _search(query: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    """Return [{"url", "title", "snippet"}]; try the fallback chain, raise only if all fail."""
    errors: list[str] = []
    for name, fn in _search_provider_chain():
        try:
            results = fn(query, max_results)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            print(f"  [search] {name} failed, trying next channel: {exc}", file=sys.stderr)
            continue
        if results:
            return results
        errors.append(f"{name}: empty")
    raise RuntimeError("all search channels returned nothing (" + "; ".join(errors) + ")")


# ---------------------------------------------------------------------------
# Backend integration (optional: used when FEEDBACK_MODE=remote)
# ---------------------------------------------------------------------------

_backend_status: bool | None = None


def backend_online() -> bool:
    """Probe backend /health once per session; result is cached.

    Silent by design (mirrors SKILL.md Runtime Fallback): backend state is
    never surfaced to the user. Offline callers degrade transparently:
    safety scan -> LLM rule-based cross-validation -> inline guards,
    feedback -> local save, passport/reputation -> skipped.
    """
    global _backend_status
    if FEEDBACK_MODE != "remote":
        return False
    if _backend_status is None:
        for _ in range(2):  # first TLS handshake can be slow; retry once
            try:
                _http_json(f"{FEEDBACK_API_URL}/health", timeout=6.0)
                _backend_status = True
                break
            except Exception:
                continue
        if _backend_status is None:
            _backend_status = False
    return _backend_status


def backend_check_urls(urls: list[str]) -> dict[str, bool] | None:
    """URL safety scan via backend /v1/sources/check.

    Returns {url: safe}; None on backend failure so callers fall back to inline guards.
    """
    if not urls:
        return {}
    try:
        resp = _http_json(
            f"{FEEDBACK_API_URL}/v1/sources/check",
            method="POST",
            payload={"urls": urls[:20]},
            timeout=30.0,
        )
    except Exception:
        return None
    verdicts: dict[str, bool] = {}
    for item in resp.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if url:
            verdicts[url] = bool(item.get("safe"))
    return verdicts or None


_URL_CROSS_VALIDATE_SYSTEM = (
    "You are a URL safety and credibility screener for web research. Judge each "
    "candidate source ONLY by the rules below; never browse or invent facts. "
    "Rules: (1) typosquatting / lookalike domains of well-known sites are unsafe; "
    "(2) titles or snippets inconsistent with the domain are unsafe; "
    "(3) content farms, SEO spam, doorway pages and parked domains are unsafe; "
    "(4) raw-IP hosts, URL shorteners and opaque redirectors are unsafe; "
    "(5) known malware / phishing / scam categories are unsafe; "
    "(6) cross-check: if the same claim appears only in this single source and "
    "the domain is unknown, prefer unsafe; reputable domains and mutually "
    "corroborating sources are safe. Output JSON only: "
    "{\"verdicts\":[{\"url\":\"...\",\"safe\":true|false,\"reason\":\"...\"}]}, "
    "one entry per candidate, keep the input order."
)


def _llm_cross_validate_urls(candidates: list[dict]) -> dict[str, bool] | None:
    """LLM rule-based cross-validation fallback for URL safety (mirrors the
    skill-side Runtime Fallback discipline).

    candidates: [{url, title, snippet}]. Returns {url: safe}; None when the LLM
    is unavailable or fails, so callers keep the inline-guard-only verdicts.
    Never raises and prints nothing — degradation must stay invisible.
    """
    if not candidates or not LLM_API_KEY:
        return None
    try:
        data = _llm_json([
            {"role": "system", "content": _URL_CROSS_VALIDATE_SYSTEM},
            {"role": "user", "content": "Candidate sources:\n" + "\n".join(
                f"{i}. url: {c['url']}\n   title: {c.get('title') or '(untitled)'}\n"
                f"   snippet: {c.get('snippet') or '(none)'}"
                for i, c in enumerate(candidates, 1)
            )},
        ])
    except Exception:
        return None
    verdicts: dict[str, bool] = {}
    for item in (data.get("verdicts") or []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("url"):
            verdicts[str(item["url"])] = bool(item.get("safe", True))
    return verdicts or None


def backend_fetch_reputations(domains: list[str]) -> dict[str, float | None]:
    """Fetch current reputation score per domain (GET /v1/sources?domain=); skip silently on failure."""
    reputations: dict[str, float | None] = {}
    for domain in domains[:12]:
        try:
            resp = _http_json(
                f"{FEEDBACK_API_URL}/v1/sources?domain={urllib.parse.quote(domain)}",
                timeout=8.0,
            )
        except Exception:
            continue
        source = resp.get("source") if isinstance(resp, dict) and resp.get("found") else None
        reputations[domain] = source.get("reputation_score") if isinstance(source, dict) else None
    return reputations


def backend_issue_passport(session_id: str) -> dict | None:
    """After feedback is accepted, request a citation passport and save it to disk."""
    try:
        resp = _http_json(
            f"{FEEDBACK_API_URL}/v1/passports/issue",
            method="POST",
            payload={"session_id": session_id},
            timeout=30.0,
        )
    except urllib.error.HTTPError as exc:
        print(f"  [passport] not issued (HTTP {exc.code}, e.g. no verified evidence), skipped",
              file=sys.stderr)
        return
    except Exception as exc:
        print(f"  [passport] issuing failed, skipped: {exc}", file=sys.stderr)
        return
    passport = resp.get("passport") if isinstance(resp, dict) else None
    if not isinstance(passport, dict) or not passport.get("passport_uuid"):
        return
    out_path = os.path.join(os.getcwd(), f"passport-{session_id}.json")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(passport, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"  [passport] failed to save: {exc}", file=sys.stderr)
        return
    print(f"  [passport] issued {passport['passport_uuid']}, saved to {out_path}")
    return passport


# ---------------------------------------------------------------------------
# URL safety check (inline SSRF guard, mirrors check.py `_normalize_url` boundary rules)
# ---------------------------------------------------------------------------

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_LOCAL_HOSTS = {"localhost"}


def check_url_safe(raw_url: str) -> tuple[bool, str]:
    """Return (fetchable, reason). Only scheme and target-address boundary protection."""
    url = (raw_url or "").strip()
    if not url:
        return False, "empty_url"
    match = _SCHEME_RE.match(url)
    if match:
        scheme = match.group(1).lower()
        if scheme not in {"http", "https"}:
            return False, f"blocked_scheme:{scheme}"
    else:
        url = f"https://{url}"
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "missing_host"
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        return False, "local_host_blocked"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local
               or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        return False, "non_public_ip_blocked"
    return True, "ok"


# ---------------------------------------------------------------------------
# Body fetching (urllib + HTMLParser to plain text)
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


def fetch_page(url: str) -> dict:
    """Fetch a page and extract body text. Returns {"ok", "url", "text", "error"}."""
    safe, reason = check_url_safe(url)
    if not safe:
        return {"ok": False, "url": url, "text": "", "error": reason}
    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            final_url = resp.geturl()
            content_type = resp.headers.get_content_type() or ""
            payload = resp.read(FETCH_MAX_CHARS * 2 + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return {"ok": False, "url": url, "text": "", "error": f"{type(exc).__name__}: {exc}"[:240]}

    if "html" not in content_type.lower() and "text" not in content_type.lower():
        return {"ok": False, "url": final_url, "text": "", "error": f"non_text:{content_type or 'unknown'}"}

    charset = resp.headers.get_content_charset() or "utf-8"
    try:
        html = payload.decode(charset, errors="replace")
    except LookupError:
        html = payload.decode("utf-8", errors="replace")

    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # tolerant HTML parsing; never block on parse errors
        pass
    text = parser.text()[:FETCH_MAX_CHARS]
    if not text:
        return {"ok": False, "url": final_url, "text": "", "error": "empty_text"}
    return {"ok": True, "url": final_url, "text": text, "error": None}


# ---------------------------------------------------------------------------
# Prompt templates (SKILL.md / references rules turned into prompts)
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = (
    "You are a deep-research task planner. Decompose the user question into 2-5 "
    "research angles. For each angle output: angle (name), query (search keywords, "
    "do not copy the original question), category, min_score (0-1 minimum source "
    "quality). Output JSON only, shaped like {\"angles\":[{\"angle\":\"...\","
    "\"query\":\"...\",\"category\":\"...\",\"min_score\":0.5}]}."
)

_FINAL_ANSWER_SYSTEM = (
    "You are a rigorous deep-research writer. Write the final answer from the "
    "evidence below. Follow this fixed section order:\n"
    "1) Question Restatement\n"
    "2) Short Answer\n"
    "3) Key Findings (separate verified facts from inference)\n"
    "4) Cross-Source Notes (agreements / conflicts / version / time / region differences)\n"
    "5) Uncertainties or Limits (never hide missing evidence)\n"
    "6) Sources (only the most useful ones, with domain and readable name; no internal ids)\n"
    "7) Explain Why (2 sentences: the real adoption basis of this session, and the main "
    "limitation; never expose backend mechanics/formulas/thresholds/internal pipeline).\n"
    "Be objective, never fabricate, never narrate the internal workflow. "
    "Answer in the same language as the research question."
)

_GAP_ANALYSIS_SYSTEM = (
    "You are the round controller of a deep-research run. Given the research question "
    "and the sources collected so far, decide whether the evidence is sufficient for a "
    "reliable answer. Output JSON only: {\"sufficient\": true} when enough; otherwise "
    "{\"sufficient\": false, \"followups\": [\"extra query 1\", \"extra query 2\"]} "
    "with at most 3 followups, targeting gaps like primary official sources, cross "
    "verification, and time/region/version alignment; never repeat angles already covered."
)

_FEEDBACK_SYSTEM = (
    "You are a structured feedback generator. Organize the evidence of this research "
    "run into the research-feedback JSON contract, used by the backend for source "
    "reputation and claim governance. Output valid JSON only: no Markdown fences, no "
    "trailing commas, no prose."
)


def _feedback_user_prompt(query: str, evidence: str) -> str:
    return (
        "From the research evidence below, generate the research-feedback JSON with these fields:\n"
        "{\n"
        '  "payload_version": "v2",\n'
        '  "session_id": "<provided by the caller; placeholder is fine>",\n'
        '  "sources": [ ... ],\n'
        '  "claims": [ ... ],\n'
        '  "claim_evidence_edges": [ ... ],\n'
        '  "provenance_edges": [],\n'
        '  "contradictions": [],\n'
        '  "typed_conflicts": [ ... optional ... ],\n'
        '  "candidate_causal_edges": [ ... optional ... ],\n'
        '  "causal_gaps": [ ... optional ... ],\n'
        '  "session_confidence": 0.0,\n'
        '  "preference_blob": {"query_category":"...","source_usefulness_ratings":{...},"answer_quality_gap":"..."}\n'
        "}\n\n"
        "Hard rules:\n"
        "- source_id uses src_001, src_002 ...; claim_id uses c1, c2 ...; cross references must point to existing ids.\n"
        "- domain must be a bare hostname (e.g. react.dev), never a full URL.\n"
        "- every source must include: source_id, url, domain, title, content_type, document_form, "
        "is_official_like, structured_markers (at least 1), is_derivative.\n"
        "- content_type must be one of: official_docs | official_blog | third_party | forum | social.\n"
        "- document_form must be one of: article_page | official_notice | other | pdf | policy_page | "
        "release_note | spec_page | table_page.\n"
        "- structured_markers may only contain: date | identifier | table | version; never empty.\n"
        "- every claim must include: claim_id, text (full natural-language statement), subject, action, "
        "and at least one of time/location/number/version_or_policy_name; the field is named text, not claim_text.\n"
        "- claim.supported_by is a list of bare source_ids (e.g. [\"src_001\"]) and must cover every source "
        "of that claim's support/partial edges; never the compound form \"c1:src_001\".\n"
        "- every claim_evidence_edge must include claim_id, source_id, evidence_snippet, supported_slots "
        "(at least 1), snippet_span_type; stance must be support|oppose|partial; source_tier must be "
        "primary|secondary|tertiary; snippet_span_type must be original_sentence|summary|table_cell|title.\n"
        "- supported_slots may only contain: subject|action|time|location|number|version_or_policy_name.\n"
        "- [numeric rule] if a claim's number is non-empty, its numeric_facts must be non-empty; if an edge's "
        "supported_slots contains \"number\", that edge's numeric_facts must be non-empty with subject+metric+unit "
        "aligned to the linked claim.\n"
        "- numeric_fact.comparator must be one of eq|gt|gte|lt|lte|range|approx.\n"
        "- do not use a whole page title as evidence_snippet unless the title itself is decisive evidence.\n"
        "- rely only on the evidence below; never invent numbers or facts absent from it.\n\n"
        f"Research question: {query}\n\n"
        f"Research evidence:\n{evidence}"
    )


# ---------------------------------------------------------------------------
# Research workflow orchestration
# ---------------------------------------------------------------------------

def _session_id(query: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:24] or "research"
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:6]
    day = time.strftime("%Y%m%d")
    return f"dt-{day}-{slug}-{digest}"


def _decompose(query: str) -> list[dict]:
    messages = [
        {"role": "system", "content": _DECOMPOSE_SYSTEM},
        {"role": "user", "content": query},
    ]
    data = _llm_json(messages)
    angles = data.get("angles") or []
    if not isinstance(angles, list) or not angles:
        raise RuntimeError("intent decomposition returned no angles")
    return angles[:5]


def _gather_sources(queries: list[str], sources: dict[str, dict]) -> dict[str, dict]:
    """Search per keyword and fetch bodies, dedupe and accumulate into sources.

    sources shape: {url: {url,title,snippet,text,ok,error}}; only new candidates
    that pass the safety check are fetched; when the backend is online its scan
    verdict takes precedence.
    """
    new_urls: list[str] = []
    for q in queries:
        try:
            results = _search(q)
        except Exception as exc:
            print(f"  [search] failed ({q}): {exc}", file=sys.stderr)
            continue
        for item in results:
            url = item["url"]
            if url in sources or len(sources) >= MAX_SOURCES:
                continue
            safe, reason = check_url_safe(url)
            if not safe:
                continue
            sources[url] = {"url": url, "title": item["title"], "snippet": item["snippet"],
                            "text": "", "ok": False, "error": reason}
            new_urls.append(url)

    # Safety re-check for new candidates. Degradation chain (all silent):
    # backend scan -> LLM rule-based cross-validation -> inline guards only.
    candidates = [sources[u] for u in new_urls if u in sources]
    verdicts: dict[str, bool] | None = None
    if backend_online():
        for start in range(0, len(new_urls), 20):
            batch = new_urls[start:start + 20]
            batch_verdicts = backend_check_urls(batch)
            if batch_verdicts is not None:
                verdicts = dict(verdicts or {})
                verdicts.update(batch_verdicts)
    if verdicts is None:
        for start in range(0, len(candidates), 15):
            batch = candidates[start:start + 15]
            batch_verdicts = _llm_cross_validate_urls(batch)
            if batch_verdicts is not None:
                verdicts = dict(verdicts or {})
                verdicts.update(batch_verdicts)
    for url, safe in (verdicts or {}).items():
        if safe is False and url in sources:
            sources.pop(url)

    # Fetch bodies (serially, to avoid hammering local machine and target sites)
    for url in new_urls:
        src = sources.get(url)
        if src is None:
            continue
        page = fetch_page(url)
        src["ok"] = page["ok"]
        src["text"] = page["text"]
        src["error"] = page["error"]
        if page["ok"]:
            print(f"  [fetch] ok  {url}")
        else:
            print(f"  [fetch] skip {url} ({src['error']})", file=sys.stderr)
    return sources


def _plan_next_round(query: str, sources: dict[str, dict]) -> list[str]:
    """Ask the LLM whether evidence is sufficient; otherwise return followup queries."""
    lines = [f"{i}. {s['title'] or '(untitled)'} — {s['url']}"
             for i, s in enumerate(sources.values(), 1)]
    messages = [
        {"role": "system", "content": _GAP_ANALYSIS_SYSTEM},
        {"role": "user", "content": f"Research question: {query}\n\nSources collected:\n" + "\n".join(lines)},
    ]
    try:
        data = _llm_json(messages)
    except Exception as exc:
        print(f"  [round] next-round check failed, stopping search: {exc}", file=sys.stderr)
        return []
    if data.get("sufficient"):
        return []
    followups = data.get("followups") or []
    return [str(q).strip() for q in followups if str(q).strip()][:3]


def _evidence_text(sources: dict[str, dict]) -> str:
    blocks: list[str] = []
    for i, src in enumerate(sources.values(), 1):
        reputation = src.get("reputation")
        reputation_line = (f"backend_reputation: {reputation}\n"
                           if isinstance(reputation, (int, float)) else "")
        blocks.append(
            f"### source {i}: {src['title'] or '(untitled)'}\n"
            f"url: {src['url']}\n"
            f"{reputation_line}"
            f"search_snippet: {src['snippet']}\n"
            f"body: {src['text'] or '(not fetched, reason: ' + (src['error'] or 'unknown') + ')'}"
        )
    return "\n\n".join(blocks)


_NORMALIZE_SYSTEM = (
    "You are a research-question normalizer. Parse the user question into stable "
    "structured fields and output JSON only: "
    "{\"raw_query\":\"...\", \"normalized_query\":\"...\", \"subject\":\"...\", "
    "\"intent_type\":\"...\", \"query_category\":\"...\", optional \"time_scope\"/"
    "\"region_scope\"/\"version_scope\"/\"target_capability\"/\"topic_tags\" (array)}. "
    "Provide optional fields only when the question clearly supports them; never invent."
)


def _build_query_normalization(query: str) -> dict:
    """Query normalization via LLM; fully self-contained."""
    try:
        data = _llm_json([
            {"role": "system", "content": _NORMALIZE_SYSTEM},
            {"role": "user", "content": query},
        ])
    except Exception as exc:
        print(f"  [normalize] failed, skipped: {exc}", file=sys.stderr)
        return {}
    norm = data if isinstance(data, dict) else {}
    norm.setdefault("raw_query", query)
    keys = ("raw_query", "normalized_query", "subject", "target_capability",
            "time_scope", "region_scope", "version_scope", "intent_type",
            "query_category", "topic_tags")
    return {k: norm.get(k) for k in keys if norm.get(k) not in (None, "", [])}


def _generate_final_answer(query: str, evidence: str) -> str:
    messages = [
        {"role": "system", "content": _FINAL_ANSWER_SYSTEM},
        {"role": "user", "content": f"Research question: {query}\n\nResearch evidence:\n{evidence}"},
    ]
    return _llm_chat(messages, temperature=0.2).strip()


def _generate_feedback(query: str, evidence: str, session_id: str) -> dict:
    messages = [
        {"role": "system", "content": _FEEDBACK_SYSTEM},
        {"role": "user", "content": _feedback_user_prompt(query, evidence)},
    ]
    data = _llm_json(messages)
    data["payload_version"] = "v2"
    data["session_id"] = session_id
    data.setdefault("sources", [])
    data.setdefault("claims", [])
    data.setdefault("claim_evidence_edges", [])
    data.setdefault("provenance_edges", [])
    data.setdefault("contradictions", [])
    return data


def _normalize_numeric_facts(items) -> list:
    """Normalize numeric_facts to contract shape: nf_ id + string value_raw."""
    out = []
    for idx, nf in enumerate(items or [], 1):
        if not isinstance(nf, dict):
            continue
        raw = nf.get("value_raw", nf.get("value", nf.get("raw", "")))
        nf["value_raw"] = str(raw).strip() if raw not in (None, "") else ""
        nf.pop("value", None)
        nf.pop("raw", None)
        if not nf.get("numeric_fact_id"):
            nf["numeric_fact_id"] = f"nf_{idx}"
        out.append(nf)
    return out


def _normalize_typed_conflicts(data: dict) -> None:
    """Normalize typed_conflicts to contract shape: one claim_id per slot."""
    out = []
    for idx, tc in enumerate(data.get("typed_conflicts") or [], 1):
        if not isinstance(tc, dict):
            continue
        if not tc.get("conflict_type") and tc.get("type"):
            tc["conflict_type"] = tc.pop("type")
        values = tc.get("conflicting_values")
        if not values and tc.get("description"):
            values = [str(tc["description"]).strip()[:200]]
        if not isinstance(values, list) or not values:
            continue
        tc["conflicting_values"] = [str(v) for v in values if str(v).strip()]
        if not tc["conflicting_values"]:
            continue
        claim_ids = tc.pop("claim_ids", None) or []
        if not tc.get("claim_id"):
            tc["claim_id"] = claim_ids[0] if claim_ids else ""
        if not tc.get("claim_id"):
            continue
        if not tc.get("slot_name"):
            desc = str(tc.get("conflict_type", "")) + " " + " ".join(tc["conflicting_values"])
            tc["slot_name"] = "number" if re.search(r"%|比例|数值|口径|percent|ratio|value", desc) else "claim"
        if not tc.get("conflict_id"):
            tc["conflict_id"] = f"tc_{idx}"
        out.append(tc)
    data["typed_conflicts"] = out


def _normalize_claim_fields(data: dict) -> dict:
    """Fix common LLM deviations: claim_text -> text, supported_by -> bare source_ids."""
    edges_by_claim: dict[str, list[str]] = {}
    for edge in data.get("claim_evidence_edges") or []:
        if not isinstance(edge, dict):
            continue
        if edge.get("stance") in ("support", "partial"):
            edges_by_claim.setdefault(edge.get("claim_id") or "", []).append(
                edge.get("source_id") or "")
    for claim in data.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if "text" not in claim and claim.get("claim_text"):
            claim["text"] = claim.pop("claim_text")
        supported_by = claim.get("supported_by")
        if not isinstance(supported_by, list):
            supported_by = []
        supported_by = [
            sid.rsplit(":", 1)[-1] if isinstance(sid, str) and ":" in sid else sid
            for sid in supported_by
        ]
        for sid in edges_by_claim.get(claim.get("claim_id") or "", []):
            if sid and sid not in supported_by:
                supported_by.append(sid)
        if supported_by:
            claim["supported_by"] = supported_by
        if claim.get("numeric_facts"):
            claim["numeric_facts"] = _normalize_numeric_facts(claim["numeric_facts"])
    for edge in data.get("claim_evidence_edges") or []:
        if isinstance(edge, dict) and edge.get("numeric_facts"):
            edge["numeric_facts"] = _normalize_numeric_facts(edge["numeric_facts"])
    _normalize_typed_conflicts(data)
    return data


def _validate_feedback(data: dict) -> dict:
    """Local normalization only; contract validation is owned by the backend."""
    return _normalize_claim_fields(data)


def _feedback_error_detail(exc: Exception) -> str:
    """Extract validation errors from a backend 400 body for LLM repair."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            errors = parsed.get("errors") or parsed.get("detail") or body
            return json.dumps(errors, ensure_ascii=False)[:2000]
        except Exception:
            return str(exc)
    return str(exc)


def _emit_feedback(data: dict, session_id: str) -> dict | None:
    """Submit feedback remotely (LLM repair once on 400, then issue passport);
    fall back to local save. Returns the issued passport dict or None."""
    if FEEDBACK_MODE == "remote" and backend_online():
        payload = data
        for attempt in (1, 2):
            try:
                _http_json(
                    f"{FEEDBACK_API_URL}/v1/research-feedback",
                    method="POST",
                    payload=payload,
                    timeout=30.0,
                )
                print(f"  [feedback] submitted to {FEEDBACK_API_URL}/v1/research-feedback")
                return backend_issue_passport(session_id)
            except urllib.error.HTTPError as exc:
                if exc.code == 400 and attempt == 1:
                    detail = _feedback_error_detail(exc)
                    print(f"  [feedback] backend contract validation failed, trying LLM repair: {detail[:300]}",
                          file=sys.stderr)
                    try:
                        fixed = _llm_json([
                            {"role": "system", "content": _FEEDBACK_SYSTEM},
                            {"role": "user", "content": (
                                "The research-feedback JSON below was rejected by the backend with 400. "
                                "Fix it per the backend errors and output valid JSON only; keep the original "
                                "structure and fields, do not drop required fields, follow the hard rules.\n\n"
                                f"Backend errors: {detail}\n\n"
                                f"Original JSON: {json.dumps(payload, ensure_ascii=False)}"
                            )},
                        ])
                        payload = _normalize_claim_fields(fixed)
                        continue
                    except Exception as repair_exc:
                        print(f"  [feedback] LLM repair failed: {repair_exc}", file=sys.stderr)
                print(f"  [feedback] remote submission failed, falling back to local save: {exc}",
                      file=sys.stderr)
                break
            except Exception as exc:
                print(f"  [feedback] remote submission failed, falling back to local save: {exc}",
                      file=sys.stderr)
                break
    out_path = os.path.join(os.getcwd(), f"feedback-{session_id}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"  [feedback] saved to {out_path}")
    return None


# ---------------------------------------------------------------------------
# Research report (--report): template in references/report-format.md
# ---------------------------------------------------------------------------

_REPORT_ANALYSIS_SYSTEM = (
    "You are a research report writer following the pyramid principle: conclusion "
    "first, grouped arguments, facts separated from inference. Output valid JSON only: "
    "no Markdown fences, no prose."
)

_stability_mod = None


def _stability_module():
    """Lazy-load sibling tools/score_stability.py (link stability scorer)."""
    global _stability_mod
    if _stability_mod is None:
        try:
            import importlib.util
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools", "score_stability.py")
            spec = importlib.util.spec_from_file_location("score_stability", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _stability_mod = mod
        except Exception:
            _stability_mod = False
    return _stability_mod or None


def _stability_score(url: str):
    mod = _stability_module()
    if mod is None:
        return None
    try:
        return int(mod.score(url).get("score", 1))
    except Exception:
        return None


def _grade_claim(claim: dict, edges: list[dict], src_by_id: dict) -> str:
    """Grade a claim A/B/C/U per the deterministic rules in report-format.md."""
    supports = [e for e in edges if e.get("stance") in ("support", "partial")]
    opposes = [e for e in edges if e.get("stance") == "oppose"]
    if not supports:
        return "U"
    if opposes:
        return "C"
    domains = set()
    has_primary = False
    for e in supports:
        src = src_by_id.get(e.get("source_id") or "", {})
        dom = (src.get("domain") or "").strip().lower()
        if dom:
            domains.add(dom)
        if (e.get("source_tier") or "") in ("primary", "official"):
            has_primary = True
    if len(supports) >= 2 and len(domains) >= 2 and has_primary:
        return "A"
    if has_primary or len(supports) >= 2:
        return "B"
    return "C"


def _generate_report_analysis(query: str, evidence: str) -> dict:
    """LLM-generated conclusion section and hypothesis verdicts (degrades to empty on failure)."""
    try:
        return _llm_json([
            {"role": "system", "content": _REPORT_ANALYSIS_SYSTEM},
            {"role": "user", "content": (
                "Based on the research evidence, generate JSON for the research report:\n"
                "{\n"
                '  "executive_summary": "conclusion first, 3-5 sentences, including overall confidence",\n'
                '  "hypotheses": [{"hypothesis": "falsifiable proposition", "verdict": "supported|refuted|insufficient", '
                '"key_evidence": "the single strongest evidence in one sentence"}],\n'
                '  "uncertainties": ["main uncertainties or evidence gaps"]\n'
                "}\n"
                "Hypotheses are rewrites of the research question, 1-3 items; verdict must be "
                "supported/refuted/insufficient. Answer text in the language of the research question.\n\n"
                f"Research question: {query}\n\nResearch evidence:\n{evidence}"
            )},
        ]) or {}
    except Exception as exc:
        print(f"  [report] conclusion generation failed, degrading to structured-only report: {exc}",
              file=sys.stderr)
        return {}


def _build_report(query: str, normalization: dict, sources: dict, feedback: dict,
                  answer: str, passport: dict | None, session_id: str) -> dict:
    """Bundle normalization, sources, feedback contract, answer, and passport into one report envelope."""
    usable = [s for s in sources.values() if s["ok"]]
    source_rows = []
    for s in usable:
        source_rows.append({
            "url": s["url"],
            "domain": (urllib.parse.urlparse(s["url"]).hostname or "").lower(),
            "title": s.get("title") or "",
            "reputation": s.get("reputation"),
            "stability": _stability_score(s["url"]),
        })
    return {
        "report_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id,
        "query": query,
        "query_normalization": normalization or {},
        "model": LLM_MODEL,
        "answer": answer,
        "sources": source_rows,
        "claims": feedback.get("claims") or [],
        "claim_evidence_edges": feedback.get("claim_evidence_edges") or [],
        "typed_conflicts": feedback.get("typed_conflicts") or [],
        "candidate_causal_edges": feedback.get("candidate_causal_edges") or [],
        "causal_gaps": feedback.get("causal_gaps") or [],
        "session_confidence": feedback.get("session_confidence"),
        "passport": {
            "passport_uuid": passport.get("passport_uuid"),
            "content_hash": passport.get("content_hash"),
            "issued_at": passport.get("issued_at"),
        } if passport else None,
    }


def _render_report_md(report: dict) -> str:
    """Render the report envelope to Markdown (section order matches report-format.md)."""
    lines: list[str] = []
    lines.append(f"# Research Report: {report['query']}")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']} | Session: {report['session_id']} | Model: {report['model']}")
    lines.append("")

    analysis = report.get("analysis") or {}
    lines.append("## 1. Conclusion")
    lines.append("")
    lines.append(analysis.get("executive_summary") or "(not generated; see structured evidence below)")
    lines.append("")

    hypotheses = analysis.get("hypotheses") or []
    if hypotheses:
        lines.append("## 2. Hypothesis Verdicts")
        lines.append("")
        lines.append("| Hypothesis | Verdict | Key Evidence |")
        lines.append("|---|---|---|")
        verdict_txt = {"supported": "Supported", "refuted": "Refuted",
                       "insufficient": "Insufficient evidence"}
        for h in hypotheses:
            if not isinstance(h, dict):
                continue
            lines.append(f"| {h.get('hypothesis', '')} | {verdict_txt.get(h.get('verdict'), h.get('verdict', ''))} "
                         f"| {h.get('key_evidence', '')} |")
        lines.append("")

    src_by_id = {s.get("source_id"): s for s in (report.get("feedback_sources") or []) if isinstance(s, dict)}
    edges = report.get("claim_evidence_edges") or []
    claims = report.get("claims") or []
    edges_by_claim: dict[str, list[dict]] = {}
    for e in edges:
        edges_by_claim.setdefault(e.get("claim_id") or "", []).append(e)

    lines.append("## 3. Core Claims & Evidence Grades")
    lines.append("")
    if not claims:
        lines.append("(no structured claims extracted in this run)")
    for i, c in enumerate(claims, 1):
        c_edges = edges_by_claim.get(c.get("claim_id") or "", [])
        grade = _grade_claim(c, c_edges, src_by_id)
        text = c.get("text") or c.get("claim_text") or ""
        lines.append(f"### Claim {i} (grade {grade})")
        lines.append("")
        lines.append(text)
        stances = {}
        for e in c_edges:
            stances[e.get("stance") or "?"] = stances.get(e.get("stance") or "?", 0) + 1
        if stances:
            lines.append("\nStance distribution: " + ", ".join(f"{k} x{v}" for k, v in stances.items()))
        lines.append("")

    lines.append("## 4. Evidence Chain")
    lines.append("")
    if edges:
        lines.append("| Claim | Source Domain | Stance | Strength | Tier | Trace | Key Snippet |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in edges:
            src = src_by_id.get(e.get("source_id") or "", {})
            snippet = (e.get("evidence_snippet") or "").replace("|", "\\|").replace("\n", " ")
            if len(snippet) > 80:
                snippet = snippet[:80] + "…"
            lines.append(f"| {e.get('claim_id', '')} | {src.get('domain', '')} | {e.get('stance', '')} "
                         f"| {e.get('support_score', '')} | {e.get('source_tier', '')} "
                         f"| {e.get('trace_depth', '')} | {snippet} |")
    else:
        lines.append("(no evidence edges)")
    lines.append("")

    lines.append("## 5. Numeric Fact Cross-Check")
    lines.append("")
    nf_rows = []
    for c in claims:
        for nf in c.get("numeric_facts") or []:
            nf_rows.append(("claim", c.get("claim_id"), nf))
    for e in edges:
        for nf in e.get("numeric_facts") or []:
            nf_rows.append(("edge", e.get("claim_id"), nf))
    if nf_rows:
        lines.append("| Layer | Subject | Metric | Value | Unit | Comparator |")
        lines.append("|---|---|---|---|---|---|")
        for layer, cid, nf in nf_rows:
            if not isinstance(nf, dict):
                continue
            lines.append(f"| {layer}({cid}) | {nf.get('subject', '')} | {nf.get('metric', '')} "
                         f"| {nf.get('value_raw', '')} | {nf.get('unit', '')} | {nf.get('comparator', 'eq')} |")
    else:
        lines.append("(no numeric facts in this run)")
    lines.append("")

    lines.append("## 6. Conflict Analysis")
    lines.append("")
    conflicts = report.get("typed_conflicts") or []
    if conflicts:
        for tc in conflicts:
            if isinstance(tc, dict):
                desc = tc.get("description") or tc.get("conflict_type") or json.dumps(tc, ensure_ascii=False)
                lines.append(f"- {desc}")
            else:
                lines.append(f"- {tc}")
    else:
        lines.append("(no cross-source conflicts detected or recorded)")
    lines.append("")

    lines.append("## 7. Causal Reasoning")
    lines.append("")
    causal = report.get("candidate_causal_edges") or []
    if causal:
        for ce in causal:
            if isinstance(ce, dict):
                cause = ce.get("cause") or ce.get("from") or ""
                effect = ce.get("effect") or ce.get("to") or ""
                conf = ce.get("confidence", "")
                lines.append(f"- {cause} -> {effect}" + (f" (confidence: {conf})" if conf != "" else ""))
            else:
                lines.append(f"- {ce}")
    else:
        lines.append("(no candidate causal chains extracted in this run)")
    gaps = report.get("causal_gaps") or []
    if gaps:
        lines.append("")
        lines.append("Causal gaps:")
        for g in gaps:
            lines.append(f"- {g.get('description') if isinstance(g, dict) else g}")
    lines.append("")

    lines.append("## 8. Source List")
    lines.append("")
    lines.append("| Domain | Title | Backend Reputation | Link Stability | URL |")
    lines.append("|---|---|---|---|---|")
    for s in report.get("sources") or []:
        stab = s.get("stability")
        stab_txt = str(stab) if stab is not None else "-"
        rep = s.get("reputation")
        rep_txt = f"{rep:.2f}" if isinstance(rep, (int, float)) else "-"
        title = (s.get("title") or "").replace("|", "\\|")
        if len(title) > 40:
            title = title[:40] + "…"
        lines.append(f"| {s['domain']} | {title} | {rep_txt} | {stab_txt} | {s['url']} |")
    lines.append("")
    lines.append("Link stability: 2 = high (immutable link / institutional domain), 1 = medium, "
                 "0 = low (social / short link / temporary params, likely to rot)")
    lines.append("")

    lines.append("## 9. Uncertainties & Limitations")
    lines.append("")
    for u in (analysis.get("uncertainties") or []) or ["(not generated)"]:
        lines.append(f"- {u}")
    lines.append("")

    lines.append("## 10. Verification")
    lines.append("")
    lines.append(f"- Session ID: {report['session_id']}")
    p = report.get("passport")
    if p:
        lines.append(f"- Citation passport: {p.get('passport_uuid')} (content hash {p.get('content_hash')})")
        lines.append("- Use POST /v1/passports/verify to verify the evidence chain cited in this report")
    else:
        lines.append("- Citation passport: not issued (backend unreachable or no verified evidence)")
    conf = report.get("session_confidence")
    if isinstance(conf, (int, float)):
        lines.append(f"- Session confidence: {conf}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report export (--report): render PDF when possible, otherwise fall back to Markdown
# ---------------------------------------------------------------------------

_pdf_mod = None


def _pdf_module():
    """Lazy-load tools/md_to_pdf.py (Markdown -> PDF renderer); None on failure."""
    global _pdf_mod
    if _pdf_mod is None:
        try:
            import importlib.util
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "tools", "md_to_pdf.py")
            spec = importlib.util.spec_from_file_location("md_to_pdf", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _pdf_mod = mod
        except Exception as exc:
            print(f"  [report] failed to load md_to_pdf: {exc}", file=sys.stderr)
            return None
    return _pdf_mod


def _save_report(md: str, sid: str) -> str:
    """Save the report as PDF when renderable, otherwise as Markdown."""
    mod = _pdf_module()
    if mod is not None:
        pdf_path = os.path.join(os.getcwd(), f"report-{sid}.pdf")
        if mod.render_pdf(md, pdf_path):
            return pdf_path
        print("  [report] Chrome/Chromium not found or render failed, falling back to Markdown")
    md_path = os.path.join(os.getcwd(), f"report-{sid}.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return md_path


# ---------------------------------------------------------------------------
# Public API: research() for library use, main() for the command line
# ---------------------------------------------------------------------------

_OVERRIDES = {
    "api_key": "LLM_API_KEY",
    "base_url": "LLM_BASE_URL",
    "model": "LLM_MODEL",
    "search_provider": "SEARCH_PROVIDER",
    "search_api_key": "SEARCH_API_KEY",
    "feedback_mode": "FEEDBACK_MODE",
    "feedback_api_url": "FEEDBACK_API_URL",
}


def research(query: str, *, report: bool = False, **overrides) -> dict:
    """Run the full research pipeline and return structured results.

    Supported overrides: api_key, base_url, model, search_provider,
    search_api_key, feedback_mode, feedback_api_url (all default to the
    env / .env configuration).

    Returns {"session_id", "normalization", "sources", "answer",
    "feedback", "passport", "report_path"}. Artifacts (report, passport,
    local feedback fallback) are written to the current working directory.
    Raises RuntimeError when no usable external source can be gathered.
    """
    for key, value in overrides.items():
        if key not in _OVERRIDES:
            raise TypeError(f"unsupported override: {key}")
        globals()[_OVERRIDES[key]] = value

    print(f"Model: {LLM_MODEL} | Search: {SEARCH_PROVIDER} | Feedback: {FEEDBACK_MODE}")
    print(f"Query: {query}\n")

    # 1) Normalize
    normalization = _build_query_normalization(query)
    if normalization:
        print(f"[1/5] Normalized: subject={normalization.get('subject')} "
              f"category={normalization.get('query_category')} "
              f"tags={normalization.get('topic_tags')}")

    # 2) Decompose intent + search + fetch
    print("[2/5] Decomposing intent...")
    angles = _decompose(query)
    queries = [a.get("query", query) for a in angles if a.get("query")]
    if not queries:
        queries = [query]
    print(f"      Angles: {[a.get('angle') for a in angles]}")

    print(f"[3/5] Searching and fetching (max {MAX_SEARCH_ROUNDS} rounds)...")
    sources: dict[str, dict] = {}
    sources = _gather_sources(queries, sources)
    for round_no in range(2, MAX_SEARCH_ROUNDS + 1):
        if len([s for s in sources.values() if s["ok"]]) >= MAX_SOURCES:
            break
        followups = _plan_next_round(query, sources)
        if not followups:
            break
        print(f"      Round {round_no} follow-ups: {followups}")
        sources = _gather_sources(followups, sources)
    usable = [s for s in sources.values() if s["ok"]]
    print(f"      Usable sources {len(usable)} / {len(sources)}")
    if not usable:
        raise RuntimeError("No usable external sources; cannot continue. Check network "
                           "connectivity (Bing/DuckDuckGo scraping is used by default; "
                           "set SEARCH_API_KEY for Tavily).")

    # Fetch per-domain reputation scores from the backend when online
    if backend_online():
        domains = []
        for src in usable:
            host = (urllib.parse.urlparse(src["url"]).hostname or "").lower()
            if host and host not in domains:
                domains.append(host)
        reputations = backend_fetch_reputations(domains)
        for src in usable:
            host = (urllib.parse.urlparse(src["url"]).hostname or "").lower()
            score = reputations.get(host)
            if score is not None:
                src["reputation"] = score

    evidence = _evidence_text(sources)

    # 4) Generate final answer
    print("[4/5] Generating final answer...")
    answer = _generate_final_answer(query, evidence)

    # 5) Generate and emit structured feedback
    print("[5/5] Generating structured feedback...")
    sid = _session_id(query)
    feedback = _generate_feedback(query, evidence, sid)
    feedback["query_normalization"] = normalization or feedback.get("query_normalization")
    feedback = _validate_feedback(feedback)
    passport = _emit_feedback(feedback, sid)

    report_path = None
    if report:
        print("[report] Generating full research report...")
        report_envelope = _build_report(query, normalization, sources, feedback, answer, passport, sid)
        report_envelope["feedback_sources"] = feedback.get("sources") or []
        report_envelope["analysis"] = _generate_report_analysis(query, evidence)
        md = _render_report_md(report_envelope)
        report_path = _save_report(md, sid)
        print(f"[report] saved {report_path}")

    return {
        "session_id": sid,
        "normalization": normalization,
        "sources": [
            {
                "url": s["url"],
                "domain": (urllib.parse.urlparse(s["url"]).hostname or "").lower(),
                "title": s.get("title") or "",
                "reputation": s.get("reputation"),
                "stability": _stability_score(s["url"]),
            }
            for s in usable
        ],
        "answer": answer,
        "feedback": feedback,
        "passport": passport,
        "report_path": report_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Net Deep Research local CLI")
    parser.add_argument("query", help="research question")
    parser.add_argument("--report", action="store_true",
                        help="generate a full research report (evidence chain / causal / grades) "
                             "as report-<session>.pdf or .md")
    args = parser.parse_args()
    query = args.query.strip()
    if not query:
        parser.error("query must not be empty")

    try:
        result = research(query, report=args.report)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("\n" + "=" * 72)
    print(result["answer"])
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
