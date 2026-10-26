## Claude Code specifics (sdlc-kit)
- Write plan.md in plan mode (read-only) and implement only after the user approved it.
- Before reporting work as done, use the `verifier` subagent from the sdlc plugin for an independent final check.
- Independent tasks from the plan can run in parallel worktrees (`claude --worktree <name>`), one task per worktree.
- For the review stage you are the author, so the reviewer is Codex: `python3 .sdlc/bin/sdlc.py review --reviewer codex`.
