## Description: <br>
Performs structured, multi-source internet research integrated with the remote backend API for source reputation scoring, security gating, and a minimal structured evidence feedback loop. Targets `https://www.shoggoth.vip`. Capabilities: public web research, external backend access, URL safety checks, default minimal structured feedback submission, and explicit high-sensitivity diagnostics only when separately requested. <br>

This skill is ready for commercial/non-commercial use. <br>

## Publisher: <br>
[h4444433333](https://clawhub.ai/user/h4444433333) <br>

### License/Terms of Use: <br>
MIT-0 <br>


## Use Case: <br>
Developers, researchers, and agent users who need backend-assisted deep research with source reputation queries, URL security checks, explicit `/net-deep-research` triggering, minimal structured feedback submission, and clean user-facing answers. <br>

User notice: During the default feedback workflow, this skill may transmit cited source metadata, structured evidence links, query classification, and usefulness signals to an external backend for source auditing and quality analysis. Raw query text, full answer text, offnet answer audits, and trust/untrust votes are only sent when the user explicitly requests a high-sensitivity diagnostic or explicit vote action. <br>

### Deployment Geography for Use: <br>
Global — backend at `https://www.shoggoth.vip` <br>

## Known Risks and Mitigations: <br>
Risk: Web research output can still contain incorrect, incomplete, or outdated conclusions, especially for medical, legal, financial, security, or other high-stakes decisions. <br>
Mitigation: Verify cited sources independently and treat the skill's answer as research support rather than final professional advice. <br>
Risk: The workflow encourages internet searching and may optionally run a local URL stability scorer. <br>
Mitigation: Review searched sources and any suggested shell commands before relying on them or running them in a sensitive environment. <br>
Risk: Backend API may be unavailable or partially degraded during runtime. <br>
Mitigation: The skill degrades gracefully — falls back to built-in source scoring when the backend is unreachable. <br>


## Reference(s): <br>
- [ClawHub skill page](https://clawhub.ai/h4444433333/net-deep-research) <br>
- Backend: https://www.shoggoth.vip <br>


## Skill Output: <br>
**Output Type(s):** [text, markdown, guidance, shell commands] <br>
**Output Format:** [Markdown research answer with source notes, backend reputation scores, and optional explicit diagnostic actions] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Encourages primary-source evidence, multi-angle decomposition, source scoring (backend + built-in), security pre-checks, conflict notes, explicit uncertainty, default minimal structured feedback, and explicit audit/vote boundaries.] <br>

## Skill Version(s): <br>
1.1.4 <br>

## Ethical Considerations: <br>
Users should evaluate whether this skill is appropriate for their environment, review any generated or modified files before relying on them, and apply their organization's safety, security, and compliance requirements before deployment. <br>
