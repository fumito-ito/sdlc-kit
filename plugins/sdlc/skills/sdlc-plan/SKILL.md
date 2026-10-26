---
name: sdlc-plan
description: Write plan.md (files to change, order of work, risks, rejected alternatives, proof) for an sdlc-kit change before any implementation, get it approved, then implement against it with verification. Use when the user asks to plan or implement a change that has no approved plan yet ("計画を立てて", "実装して", "plan this", "build this").
---

# sdlc-plan — Build stage

Nothing is implemented without an approved plan. The plan is what the review later checks the diff against.

## Plan
1. `python3 .sdlc/bin/sdlc.py status`: spec must be `accepted`. For a small change the user may skip spec — then intent must be `accepted` and the reason goes into `spec_skipped` (the user passes it when approving: `sdlc.py approve --spec-skipped "<reason>"`).
2. In Claude Code, work in plan mode (read-only) until the plan is approved.
3. Read intent/spec, then the code. `python3 .sdlc/bin/sdlc.py add <id> plan` and fill:
   - 変更するファイル — paths, new/modified/deleted.
   - 作業順 — small steps, each verifiable on its own.
   - リスク — what could break, the riskiest step, external limits.
   - 検討して採らなかった案 — and why.
   - 証明 — exact commands/tests/targets that prove it works; always include `python3 .sdlc/bin/sdlc.py verify`.
4. Interrogate the plan before showing it: What could this break? Which step is riskiest? What did you choose not to do? Could an engineer who never saw this conversation implement it from plan.md alone? Fix the gaps.
5. Iterate with the user. Approval is theirs (`python3 .sdlc/bin/sdlc.py approve` in their terminal). Propose the commit `plan: <title>` with `SDLC-Change: <id>`.

## Implement
6. Follow the order. Commit in small steps, every commit with the trailer `SDLC-Change: <id>`.
7. When reality departs from the plan, record it in "計画からの逸脱" in the same commit.
8. Run `python3 .sdlc/bin/sdlc.py verify` and iterate until it passes. Never skip, weaken or delete a test to get green.
9. Claude Code: before reporting done, have the `verifier` subagent run the Proof items. Independent tasks from the plan can run in parallel worktrees.
10. Continue with `sdlc-review`.
