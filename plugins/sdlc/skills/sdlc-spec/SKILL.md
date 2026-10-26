---
name: sdlc-spec
description: Turn an accepted intent.md into spec.md — requirements, quantifiable acceptance criteria, design against the real codebase and flagged policy concerns — in an sdlc-kit repository. Use after an intent is accepted, or when the user asks for requirements or a design ("specを作って", "要件と設計をまとめて", "design this change").
---

# sdlc-spec — Design stage

Requirements and design in one pass, with the repository's policies applied while writing — not discovered in review weeks later.

1. `python3 .sdlc/bin/sdlc.py status` — the change's intent must be `accepted`. If it is not, stop and say so.
2. Read `intent.md`, AGENTS.md and every policy or standards skill available to you that applies (security, API design, UI, brand…). Read the code areas the change touches, enough to design against the real codebase.
3. `python3 .sdlc/bin/sdlc.py add <id> spec`, then fill it:
   - 懸念点・ポリシー間の矛盾 — first section on purpose: every place where policies conflict, a requirement cannot be met, or a human must decide.
   - 要求 — numbered and testable.
   - 受け入れ基準 — checks an agent can run without asking (commands, tests, screenshots compared to a mock).
   - 設計 — components, data, interfaces, migrations; reference real files.
   - 適用したポリシー — which skills/standards you applied, with version or commit when known.
   - Answer or carry forward each open question from intent.md.
4. Walk the user through the concerns first, then the rest. Revise until they are satisfied. Acceptance is theirs: `python3 .sdlc/bin/sdlc.py approve` in their terminal.
5. Propose the commit `spec: <title>` with the trailer `SDLC-Change: <id>`. Next: `sdlc-plan`.
