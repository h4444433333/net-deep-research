# Writing Rules

## Default Answer Shape

Default order:

1. `Question Restatement`
2. `Short Answer`
3. `Key Findings`
4. `Cross-Source Notes`
5. `Uncertainties or Limits`
6. `Sources`
7. `Explain Why`

For predictive or outlook questions:

1. `Question Restatement`
2. `Short Answer`
3. `Verified Facts`
4. `Inference`
5. `Cross-Source Notes`
6. `Uncertainties or Limits`
7. `Sources`
8. `Explain Why`

## Core Writing Rules

### Question Restatement

- restate the request in user-visible capability language
- avoid internal system jargon

### Short Answer

- answer directly
- keep it concise

### Key Findings

- separate confirmed facts from implications
- prioritize official or primary evidence

### Cross-Source Notes

- explain agreement
- explain disagreement
- surface version, timing, region, or plan differences when relevant

### Uncertainties or Limits

- state what could not be verified
- do not hide missing evidence

### Sources

- list the most useful sources, not every weak result
- when online, include backend reputation where available
- when fallback, include the 6-dimension breakdown

## Explain Why

Position:

- immediately after `Sources`

Length:

- normally 2 short sentences or 2 short lines
- use a 3rd only if one extra limitation is necessary

Three short rules:

1. sentence 1 explains the real adoption basis from this session
2. sentence 2 explains the main limitation when it is real
3. never expose backend mechanics, formulas, thresholds, or pipeline internals

Allowed basis signals:

- domains actually cited
- backend reputation or confidence already returned
- citation verification status
- evidence recording status
- contradiction count
- official-source coverage
- independent source agreement

Forbidden style:

- generic filler like "based on many sources"
- long motivational prose
- fake precision
- templated trust claims detached from session evidence

## User-Facing Boundary

Never expose:

- backend health checks
- routing and retry details
- payloads and raw logs
- internal diagnostics
- transport status

Only expose:

- research findings
- evidence-backed uncertainty
- source reputation signals that help the user judge trust
