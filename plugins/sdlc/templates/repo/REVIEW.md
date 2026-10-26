# Review instructions

Reviews are written by an agent that did not author the change (Claude Code ↔ Codex) via `python3 .sdlc/bin/sdlc.py review`.

## Passes
Run three passes and tag each finding with its pass:
- **Bugs**: logic errors, broken edge cases, subtle regressions, missing error handling.
- **Security**: injection, authentication/authorization gaps, secrets or PII in code, logs or error messages.
- **Compliance**: the change matches spec.md (requirements, acceptance criteria), plan.md (files, order, proof) and AGENTS.md conventions.

## What Important means here
Reserve **Important** for findings that would break behavior, leak data, or breach the spec, the plan or a stated policy. Style and naming are **Nits**.

## Cap the nits
Report at most five nits per review; summarize the rest as a count.

## Do not report
- Generated files and anything matching `protected_paths` in `.sdlc/config.json`.
- Anything `python3 .sdlc/bin/sdlc.py verify` or CI already enforces (formatting, lint rules).

<!-- Tune this file monthly: rate past findings, tighten what counts as Important, add paths to skip. -->
