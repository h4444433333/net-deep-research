# Source Scoring

Use two layers together when backend is online:

1. backend reputation
2. built-in 6-dimension scoring

In runtime fallback, use only the built-in layer.

## Layer 1: Backend Reputation

Query:

```text
GET https://www.shoggoth.vip/v1/sources?domain=<hostname>
```

Decision guide:

| Score Range | Confidence | Action |
| --- | --- | --- |
| `>= 1.5` | `>= 0.7` | prefer as primary reference |
| `>= 1.0` | `>= 0.5` | normal weight |
| `>= 0.5` | `>= 0.3` | cautious, requires stronger cross-check |
| `< 0.5` | any | low priority, use only if needed |
| any | `< 0.3` | downgrade to neutral |

If `found: false`, treat the domain as neutral and unverified.

## Layer 2: Built-in 6-Dimension Scoring

Score each dimension `0-2`. Total range `0-12`.

### 1. authority

| Condition | Score |
| --- | --- |
| official domain / official org repo / `.gov` / `.edu` / standards body | `2` |
| curated technical reference / official registry / verified maintainer / strong tech publication | `1` |
| otherwise | `0` |

### 2. stability

Prefer automated scoring:

```text
python3 tools/score_stability.py --json "<url>"
```

Manual fallback:

| Condition | Score |
| --- | --- |
| docs, permalinks, release pages, standards pages, registry pages | `2` |
| official blog post, reputable outlet, third-party technical blog | `1` |
| social post, personal blog, session URL, temporary token URL | `0` |

### 3. accessibility

| Condition | Score |
| --- | --- |
| public and readable without barriers | `2` |
| partial barrier such as free account or mild geo restriction | `1` |
| login wall / paywall / captcha gate | `0` |

### 4. freshness

Score relative to the question's time scope.

| Condition | Score |
| --- | --- |
| clearly within the relevant time window | `2` |
| probably current but partially ambiguous | `1` |
| clearly outdated or superseded | `0` |

### 5. relevance

| Condition | Score |
| --- | --- |
| directly addresses the claim | `2` |
| one inference step required | `1` |
| tangential or unrelated | `0` |

### 6. primacy

| Condition | Score |
| --- | --- |
| original source | `2` |
| useful secondary analysis | `1` |
| tertiary repost / summary-only / discussion echo | `0` |

## Shortcuts

- verified maintainer social post: authority stays low unless independently grounded
- content farm or AI slop: reject immediately
- official GitHub release page of the exact project: strong shortcut for authority, stability, and primacy

## Minimum Rules

- do not use a source below `5/12` as key evidence
- every important claim needs at least one source with `authority >= 1` and `relevance >= 1`
- every core conclusion should be anchored to at least one `primacy = 2` source whenever possible

## Output Rule

When runtime online:

- show backend reputation in `Sources`

When runtime fallback:

- show the 6-dimension breakdown in `Sources`
