# Changelog

## 0.1.0 — 2026-10-26

First release.

- One plugin for Claude Code and Codex: shared skills (`sdlc-init`, `sdlc-intent`, `sdlc-spec`, `sdlc-plan`, `sdlc-fix`, `sdlc-review`), shared `hooks/hooks.json`, a Claude Code `verifier` subagent.
- `sdlc.py` (Python 3.9+, stdlib only): `init`, `new-change`, `add`, `approve`, `set-status`, `status`, `check`, `review`, `verify`, `test-lock`, `config`, `doctor`, `hook`.
- Guards (agent hooks): protected paths, secrets in edits, `.env` reads, test lock, human-only approvals, no `--no-verify`.
- Gates (git hooks + CI): artifact lint, plan gate (`SDLC-Change` / `SDLC-Exempt` trailers), secret scan.
- Cross-agent review: Claude Code ↔ Codex via `codex exec --sandbox read-only` / `claude -p` with read-only tools.
