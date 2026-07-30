#!/usr/bin/env python3
"""URL stability scorer for net-deep-research skill.

Scores a URL's link-rot probability (0-2) purely from its structure.
Requires Python 3.7+. No dependencies beyond stdlib.

Usage:
    python3 score_stability.py <url>
    python3 score_stability.py --json <url>
    python3 score_stability.py --batch urls.txt
"""

import sys
import re
import json
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Score-2 matchers (stable, likely permanent)
# ---------------------------------------------------------------------------

# GitHub / GitLab permalinks
_RE_GIT_PERMALINK = re.compile(
    r"/(releases/tag/|blob/[0-9a-f]{7,40}/|commit/[0-9a-f]{7,40})",
    re.IGNORECASE,
)

# Session / temporary tokens
_RE_TEMPORARY = re.compile(
    r"[?&](session|token|temp|tmp|nonce|access_token|refresh_token|jwt)=",
    re.IGNORECASE,
)


def _is_git_permalink(parsed) -> tuple[bool, str]:
    """GitHub or GitLab release-tag, blob-hash, or commit-hash URL."""
    if _RE_GIT_PERMALINK.search(parsed.path):
        return True, "git_permalink"
    # bare repo root: github.com/owner/repo
    if parsed.netloc.lower() in ("github.com", "gitlab.com"):
        if re.match(r"^/[^/]+/[^/]+/?$", parsed.path) and not parsed.path.rstrip("/").endswith(".git"):
            return True, "repo_root"
    return False, ""


def _is_docs_hosting(parsed) -> tuple[bool, str]:
    """docs.* subdomain, readthedocs, github.io, or /docs/ path."""
    host = parsed.netloc.lower()
    if host.startswith("docs.") or ".readthedocs.io" in host or ".github.io" in host:
        return True, "docs_hosting"
    if "/docs/" in parsed.path or parsed.path.endswith("/docs"):
        return True, "docs_path"
    return False, ""


def _is_institutional(parsed) -> tuple[bool, str]:
    """.gov, .edu, or standards-body domain."""
    host = parsed.netloc.lower().rstrip(".")
    if host.endswith(".gov") or host.endswith(".edu"):
        return True, "institutional_domain"
    standards = ("rfc-editor.org", "w3.org", "ietf.org", "iso.org", "ieee.org", "acm.org")
    if any(host == s or host.endswith("." + s) for s in standards):
        return True, "standards_body"
    return False, ""


def _is_package_registry(parsed) -> tuple[bool, str]:
    """Package-registry permalink."""
    patterns = (
        ("/package/", "www.npmjs.com", "npmjs.com"),
        ("/project/", "pypi.org",),
        ("/crates/", "crates.io",),
        ("/artifact/", "search.maven.org",),
        ("/packages/", "packagist.org",),
        ("/gems/", "rubygems.org",),
    )
    host = parsed.netloc.lower()
    for path_seg, *hosts in patterns:
        if path_seg in parsed.path and any(h in host for h in hosts):
            return True, "package_registry"
    return False, ""


# ---------------------------------------------------------------------------
# Score-1 matchers (moderately stable)
# ---------------------------------------------------------------------------

_KNOWN_STABLE_NEWS = (
    "arstechnica.com", "lwn.net", "theverge.com", "techcrunch.com",
    "wired.com", "zdnet.com", "infoq.com", "phoronix.com",
    "theregister.com", "heise.de", "golem.de",
)

_THIRD_PARTY_BLOGS = (
    "medium.com", "dev.to", "hashnode.dev", "substack.com",
    "blog.csdn.net", "cnblogs.com",
)

_MIRROR_SITES = (
    "web.archive.org", "archive.is", "archive.ph",
)


def _is_known_stable_news(parsed) -> tuple[bool, str]:
    """Well-known tech publication with editorial archiving."""
    host = parsed.netloc.lower()
    if any(h in host for h in _KNOWN_STABLE_NEWS):
        return True, "known_stable_publication"
    return False, ""


def _is_third_party_blog(parsed) -> tuple[bool, str]:
    """Blog platform where URLs may change or paywalls appear."""
    host = parsed.netloc.lower()
    if any(h in host for h in _THIRD_PARTY_BLOGS):
        return True, "third_party_blog_platform"
    return False, ""


def _is_official_blog(parsed) -> tuple[bool, str]:
    """Blog path on a project's own domain."""
    if "/blog/" in parsed.path or "/news/" in parsed.path:
        return True, "official_blog_path"
    return False, ""


def _is_mirror(parsed) -> tuple[bool, str]:
    """Archive or mirror site."""
    host = parsed.netloc.lower()
    if any(h in host for h in _MIRROR_SITES):
        return True, "mirror_archive"
    return False, ""


# ---------------------------------------------------------------------------
# Score-0 matchers (ephemeral, likely to rot)
# ---------------------------------------------------------------------------

_SOCIAL_DOMAINS = (
    "twitter.com", "x.com", "reddit.com", "news.ycombinator.com",
    "facebook.com", "fb.com", "instagram.com", "tiktok.com",
    "linkedin.com", "discord.com", "discord.gg",
    "slack.com", "telegram.org", "t.me",
    "weibo.com", "zhihu.com", "douban.com",
    "youtube.com", "youtu.be", "bilibili.com",
    "whatsapp.com", "snapchat.com", "threads.net",
)

_LINK_SHORTENERS = (
    "t.co", "bit.ly", "tinyurl.com", "ow.ly", "buff.ly",
    "shorturl.at", "goo.gl", "is.gd", "rebrand.ly",
    "cutt.ly", "short.link", "rb.gy", "tiny.cc",
    "s.id", "soo.gd", "url.cn",
)


def _is_social_media(parsed) -> tuple[bool, str]:
    """Social media — posts can be deleted at any time."""
    host = parsed.netloc.lower()
    if any(h in host for h in _SOCIAL_DOMAINS):
        return True, "social_media"
    return False, ""


def _is_link_shortener(parsed) -> tuple[bool, str]:
    """URL shortener — extra layer of indirection, may stop resolving."""
    host = parsed.netloc.lower()
    if any(h in host for h in _LINK_SHORTENERS):
        return True, "link_shortener"
    return False, ""


def _has_temporary_params(parsed) -> tuple[bool, str]:
    """URL carries session, token, or temporary parameters."""
    if _RE_TEMPORARY.search(parsed.query) or _RE_TEMPORARY.search(parsed.path):
        return True, "temporary_params"
    return False, ""


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

# Ordered by priority: stop at first match. Each tuple is (score, matcher_fn).
_RULES = [
    # Score 2
    (2, _is_git_permalink),
    (2, _is_docs_hosting),
    (2, _is_institutional),
    (2, _is_package_registry),
    # Score 1
    (1, _is_mirror),
    (1, _is_known_stable_news),
    (1, _is_official_blog),
    (1, _is_third_party_blog),
    # Score 0
    (0, _is_social_media),
    (0, _is_link_shortener),
    (0, _has_temporary_params),
]

_RULE_EXPLANATIONS = {
    "git_permalink":            "GitHub/GitLab release tag, commit, or blob URL — immutable",
    "repo_root":                "GitHub/GitLab repository root — stable as long as repo exists",
    "docs_hosting":             "Hosted on docs.* subdomain, readthedocs, or github.io — static site",
    "docs_path":                "Path contains /docs/ — likely rendered documentation",
    "institutional_domain":     ".gov or .edu domain — legally required to preserve information",
    "standards_body":           "Standards body (W3C, IETF, ISO, IEEE, RFC Editor) — permanent archive",
    "package_registry":         "Package registry permalink (npm, PyPI, crates.io, Maven) — stable",
    "mirror_archive":           "Web Archive or mirror — already preserved",
    "known_stable_publication": "Established tech publication with editorial archiving",
    "official_blog_path":       "Blog or news path on project's own domain",
    "third_party_blog_platform":"Third-party blog platform (Medium, dev.to, Substack) — may paywall or reorganize",
    "social_media":             "Social media post — can be deleted at any time",
    "link_shortener":           "URL shortener — adds resolution indirection, may stop working",
    "temporary_params":         "URL contains session, token, or temporary parameters",
    "default":                  "No specific risk signal — assume moderate stability",
}


def score(url: str) -> dict:
    """Score a single URL. Returns dict with score, rule, explanation."""
    if not url or not url.strip():
        return {
            "url": url or "",
            "score": 0,
            "rule": "empty_url",
            "explanation": "Empty or missing URL",
            "error": "empty_url",
        }

    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception:
        return {
            "url": url,
            "score": 0,
            "rule": "parse_error",
            "explanation": "URL could not be parsed",
            "error": "parse_error",
        }

    # If no scheme, default to https
    if not parsed.scheme:
        url = "https://" + url
        try:
            parsed = urlparse(url)
        except Exception:
            pass

    for score_val, matcher in _RULES:
        matched, rule = matcher(parsed)
        if matched:
            return {
                "url": url,
                "score": score_val,
                "rule": rule,
                "explanation": _RULE_EXPLANATIONS.get(rule, rule),
            }

    return {
        "url": url,
        "score": 1,
        "rule": "default",
        "explanation": _RULE_EXPLANATIONS["default"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    json_mode = "--json" in flags
    batch_mode = "--batch" in flags

    if batch_mode and not args:
        print("Error: --batch requires a file path", file=sys.stderr)
        sys.exit(2)

    if batch_mode:
        filepath = args[0]
        urls = []
        try:
            with open(filepath) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        urls.append(stripped)
        except FileNotFoundError:
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            sys.exit(1)
    elif args:
        urls = args
    else:
        # Read from stdin
        urls = [line.strip() for line in sys.stdin if line.strip()]

    if not urls:
        print("Usage: score_stability.py [--json] [--batch FILE] <url...>", file=sys.stderr)
        print("       echo 'https://...' | score_stability.py [--json]", file=sys.stderr)
        sys.exit(2)

    results = [score(u) for u in urls]

    if json_mode:
        if len(results) == 1:
            print(json.dumps(results[0], ensure_ascii=False))
        else:
            print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            # Compact single-line output: SCORE:<n>  RULE:<name>  URL
            print(f"SCORE:{r['score']}  RULE:{r['rule']}  URL:{r['url']}")


if __name__ == "__main__":
    main()
