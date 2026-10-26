## SDLC workflow (sdlc-kit {{kit_version}})

Every non-trivial change lives in `{{changes_dir}}/<YYYY-MM-DD>-<slug>/` and moves through committed artifacts. Each stage starts by reading the previous artifact and ends by committing its own.

1. `intent.md` — problem, outcome, constraints in the originator's words (skill: `sdlc-intent`). The user accepts it.
2. `spec.md` — requirements, acceptance criteria, design, concerns (skill: `sdlc-spec`). The user accepts it.
3. `plan.md` — files, order, risks, proof (skill: `sdlc-plan`). The user approves it. **No implementation before an approved plan.**
4. Implement, verify, then get `review.md` from the *other* agent (skill: `sdlc-review`).

Bugs: skill `sdlc-fix` (failing test first; tests are locked while fixing).

Rules for agents:
- Use `python3 .sdlc/bin/sdlc.py` for every artifact operation (`new-change`, `add`, `status`, `review`). Never create change directories or edit artifact frontmatter by hand.
- Approvals are the user's. Never mark anything accepted/approved; ask the user to run `python3 .sdlc/bin/sdlc.py approve` in their terminal.
- Before saying a task is done, run `python3 .sdlc/bin/sdlc.py verify` and show its output. If a test fails, fix the code, not the test.
- If the implementation departs from plan.md, update plan.md in the same commit.
- Commit messages for a change carry the trailer `SDLC-Change: <id>`. A trivial change outside the plan gate may use `SDLC-Exempt: <reason>` instead.
- Do not bypass git hooks (`--no-verify`) and do not edit `.sdlc/bin/` or `.sdlc/githooks/` (kit-managed).
- Write artifacts and reviews in {{language_name}}.
- When a review flags the same mistake a second time, add the correction to "Things agents get wrong" below.
