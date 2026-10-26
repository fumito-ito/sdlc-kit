---
name: sdlc-init
description: Set up or update the sdlc-kit SDLC workflow in the current git repository — scaffolds .sdlc/, docs/changes/, the AGENTS.md block, git hooks and the CI gate, then tailors the config to the project's stack. Use when the user asks to initialize, install or update sdlc-kit ("SDLCを導入", "sdlc-initして", "このリポジトリにsdlc-kitを入れて"), or when session notes say the repository files are older than the plugin.
---

# sdlc-init

Three phases. Phase 1 is deterministic: never hand-write the files it creates. Phase 2 needs your judgment about this repository. Phase 3 proves it works.

## Phase 1 — scaffold (deterministic)

1. The plugin copy of the CLI is `../../bin/sdlc.py` relative to this SKILL.md. Resolve it to an absolute path; call it `KIT` below.
2. Run `python3 KIT init --dry-run` and show the user the planned rows. Then run `python3 KIT init`.
   - It is idempotent: it creates missing files, refreshes kit-managed files (they carry a `managed-by: sdlc-kit` marker), upserts the marked block in AGENTS.md / CLAUDE.md, and never overwrites user-owned files.
   - Explain every `skipped` row to the user (for example an existing hook manager such as lefthook or husky — then add `python3 .sdlc/bin/sdlc.py check --staged` to its pre-commit and `check --pre-push` to its pre-push).
3. From here on, use the vendored copy: `python3 .sdlc/bin/sdlc.py`.

When updating an already initialized repository, Phase 1 is usually all that is needed; run Phase 3 step 1 afterwards.

## Phase 2 — tailor to this repository (judgment)

Inspect build files, languages, test layout and existing CI. Change config only through the CLI — `python3 .sdlc/bin/sdlc.py config set <key> '<json>'` — never by editing `.sdlc/config.json`.

- `verify`: one command that builds, tests and lints and exits non-zero on failure. Prefer `make verify`; if there is no Makefile, create one whose `verify` target wraps the real commands. Examples: Swift package `swift build && swift test`; Xcode app `xcodebuild test -scheme <Scheme> -destination '<destination>'` plus `swiftlint --strict` when SwiftLint is configured; Node `npm run lint && npm test`; Python `ruff check . && pytest`. In an empty repository create a `verify` target that prints `no checks configured yet` and exits 0, and tell the user to replace it once there is code.
- `plan_required_paths`: globs for production code that must not change without an approved plan.
- `test_globs`: where tests live (used by the test lock during bug fixes).
- `protected_paths`: generated code, vendored code and lockfiles that agents must not hand-edit.
- `format_command` (optional): formatter for one file, with a `{file}` placeholder, run after each agent edit.
- If the project is not on ubuntu (e.g. Xcode), adjust `.github/workflows/sdlc-verify.yml` (repository-owned).

Then fill the project section of AGENTS.md **outside** the `sdlc-kit:begin/end` markers: Commands (include an example of healthy `verify` output), Conventions, Architecture (one line per top-level directory), Things agents get wrong. Keep the whole file under about one page — it is read at the start of every session.

## Phase 3 — prove it

1. Run `python3 .sdlc/bin/sdlc.py doctor --run-verify`. Fix every ✗. Report the remaining ! and i rows to the user, including the manual ones (installing the plugin in the other agent, trusting the hooks in Codex with `/hooks`).
2. Record the setup as the first change so the workflow is exercised once:
   `python3 .sdlc/bin/sdlc.py new-change sdlc-bootstrap --title "sdlc-kit 導入"` and fill intent.md briefly (problem: no shared, reproducible workflow; outcome: artifacts, guards and gates in place). Ask the user to review it and approve with `python3 .sdlc/bin/sdlc.py approve` in their terminal.
3. Propose a commit containing the scaffold and the intent, with the trailer `SDLC-Change: <id>`. Do not commit unless the user asks.
