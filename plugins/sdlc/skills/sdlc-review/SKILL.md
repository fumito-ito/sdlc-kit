---
name: sdlc-review
description: Get an independent review of the current sdlc-kit change from the other agent (Claude Code reviews Codex's work, Codex reviews Claude Code's) against REVIEW.md, spec.md and plan.md, saved as review.md, then address the findings. Use when implementation is done, before opening or merging a PR, or when the user asks for a review ("レビューして", "クロスレビュー", "review this change").
---

# sdlc-review — Deploy stage, review loop

The agent that wrote the code does not review or approve it. The review runs in the other agent's CLI, read-only, from a deterministic prompt.

1. Make sure the work is committed and `python3 .sdlc/bin/sdlc.py verify` passes. (Uncommitted tracked changes are included in the reviewed diff and flagged as `dirty`; untracked files are not.)
2. Run the review with the other agent:
   - If you are Claude Code: `python3 .sdlc/bin/sdlc.py review <id> --reviewer codex`
   - If you are Codex: `python3 .sdlc/bin/sdlc.py review <id> --reviewer claude`
   The CLI snapshots the diff to `.sdlc/state/review/<id>.diff`, runs the reviewer read-only and writes `review.md` (`review-2.md`, … for later rounds) with the verdict in its frontmatter. It can take several minutes; use a long tool timeout.
3. If the other CLI is unavailable, tell the user. Only with their consent, fall back to a fresh-context self-review: get the prompt with `python3 .sdlc/bin/sdlc.py review <id> --prompt-only`, run it in a fresh context that only reads (Claude Code: a subagent limited to Read/Grep/Glob), save the output to a file and record it with `python3 .sdlc/bin/sdlc.py add <id> review --body-file <file> --set reviewer=self`.
4. Summarize the Important findings for the user. Fix them in new commits (same `SDLC-Change` trailer); record plan changes in plan.md. Fix cheap Nits, list the rest.
5. If Important findings were fixed, run another round. When the latest review has no unresolved Important findings, mark it: `python3 .sdlc/bin/sdlc.py set-status <id> review resolved`.
6. When a review flags a mistake that was flagged before, add the correction to "Things agents get wrong" in AGENTS.md as part of this change.
7. Merging is a human decision through the PR and branch protection.
