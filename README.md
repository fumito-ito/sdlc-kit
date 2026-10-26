# sdlc-kit

English | [日本語](docs/README-ja.md)

A plugin for running Anthropic's [AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook) reproducibly in local development. This single repository ships the same plugin to both Claude Code and Codex.

- For each change, `intent.md` → `spec.md` → `plan.md` → implementation → `review.md` are committed under `docs/changes/<date>-<slug>/`, and each stage starts by reading the previous stage's artifact.
- Skills (instructions) form the layer that makes rules *easy to follow*; hooks, git hooks, and CI form the layer that makes them *hard to break*.
- Reviews are performed by a different agent from the one that wrote the code (Codex reviews Claude Code's work, and Claude Code reviews Codex's work).

## Layout

```
sdlc-kit/
├── .claude-plugin/marketplace.json     # Catalog for Claude Code
├── .agents/plugins/marketplace.json    # Catalog for Codex
└── plugins/sdlc/
    ├── .claude-plugin/plugin.json      # Manifest for Claude Code
    ├── .codex-plugin/plugin.json       # Manifest for Codex (same name/version)
    ├── skills/                         # Shared by both tools (SKILL.md)
    │   ├── sdlc-init/   sdlc-intent/  sdlc-spec/
    │   └── sdlc-plan/   sdlc-fix/     sdlc-review/
    ├── hooks/hooks.json                # Hook definitions shared by both tools
    ├── agents/verifier.md              # Subagent for Claude Code
    ├── bin/sdlc.py                     # CLI, guards, and gates (Python 3.9+ stdlib only, single file)
    └── templates/                      # Artifact templates and scaffolds deployed into each repository
```

## Installation

**Claude Code**

```
/plugin marketplace add fumito-ito/sdlc-kit
/plugin install sdlc@sdlc-kit
```

**Codex**

```sh
codex plugin marketplace add fumito-ito/sdlc-kit
codex plugin add sdlc@sdlc-kit
```

Codex does not run hooks bundled with a plugin until you trust them. After installing (and after every plugin update), open `/hooks` in Codex and trust the sdlc hooks.

Requires `python3` (3.9 or later; macOS's built-in `/usr/bin/python3` works) and `git`. Cross-review uses both the `claude` and `codex` CLIs.

## Setting up a repository

Open Claude Code or Codex in the target repository and ask it to "set up sdlc-kit in this repository", or invoke the `sdlc-init` skill (`/sdlc:sdlc-init` in Claude Code). The skill proceeds in three steps:

1. **Scaffold (deterministic)** — the bundled `sdlc.py init` lays down the scaffolding. It is idempotent and never overwrites existing files.
2. **Tailor (judgment)** — the agent inspects the stack, sets the `verify` command and protected paths via `sdlc.py config set`, and writes the project section of AGENTS.md.
3. **Confirm** — `sdlc.py doctor --run-verify` checks that the machinery actually works.

What gets deployed:

| Path | Owner | Contents |
|---|---|---|
| `.sdlc/config.json` | repository | Single source of repo-specific values (see below) |
| `.sdlc/bin/sdlc.py` | kit (overwritten on update) | Copy of the plugin's CLI. CI and git hooks use this without the plugin |
| `.sdlc/templates/*.md` | repository | Artifact templates (editable) |
| `.sdlc/githooks/pre-commit`, `pre-push` | kit | Enabled via `core.hooksPath` |
| `docs/changes/` | repository | Where artifacts live |
| `AGENTS.md` | repository (kit owns only the marked section) | Shared instructions. Codex reads it directly; Claude Code reads it via `@AGENTS.md` in CLAUDE.md |
| `CLAUDE.md` | repository (kit owns only the marked section) | `@AGENTS.md` plus Claude Code–specific notes |
| `REVIEW.md` | repository | Review policy |
| `.github/workflows/sdlc-gate.yml` | kit | CI gate |
| `.github/workflows/sdlc-verify.yml` | repository | Runs `verify` in CI (switch to `macos-latest` for Xcode) |
| `.claude/settings.json` | merged | Marketplace and plugin declarations, permission for sdlc.py, deny reading `.env` |

Kit-owned files carry a `managed-by: sdlc-kit` marker; rerunning `sdlc-init` after a plugin update brings them up to date. If a file with the same name exists without the marker, it is left alone and reported as `skipped`. In repositories using lefthook / husky / pre-commit, `core.hooksPath` is not changed; call `python3 .sdlc/bin/sdlc.py check --staged` (pre-commit) and `check --pre-push` (pre-push) from their respective configs.

## Day-to-day flow

| Stage | Skill | Artifact | Decided by |
|---|---|---|---|
| Plan | `sdlc-intent` | `intent.md` | User (`approve` → accepted) |
| Design | `sdlc-spec` | `spec.md` (concerns up front) | User (accepted) |
| Build | `sdlc-plan` | `plan.md` → implementation → `verify` | User (approved). No implementation before approval |
| Test | `sdlc-fix` (for bugs) | Commit a failing test first, then lock tests and fix | Only the user can unlock |
| Deploy | `sdlc-review` | `review.md` (written read-only by the other agent) | User merges (PR) |

Approvals are made by the user in the terminal:

```sh
python3 .sdlc/bin/sdlc.py approve            # approve the next draft artifact of the current change
python3 .sdlc/bin/sdlc.py approve --spec-skipped "one-line fix"   # skip spec and approve plan
```

If an agent runs `approve` or rewrites a frontmatter status to accepted/approved, a hook blocks it.

Commits carry an `SDLC-Change: <id>` trailer. Changes touching `plan_required_paths` fail at pre-push and in CI unless they reference an approved plan.md. Truly small changes such as typo fixes can pass with `SDLC-Exempt: <reason>` (the reason stays in history).

## CLI

`python3 .sdlc/bin/sdlc.py <command>` (see `--help` for details)

| Command | Purpose |
|---|---|
| `new-change <slug> --title "…" --source idea\|bug\|alert\|scan\|incident` | Create a change directory and intent.md (date in the configured timezone; `-2` suffix on collision) |
| `add [id] spec\|plan\|review` | Add an artifact from its template |
| `approve [id] [--reject] [--spec-skipped "…"]` | Approve the next draft (for humans) |
| `set-status <id> <artifact> <status>` | Change status (checked for consistency with the previous stage) |
| `status [--json]` / `use <id>` | List changes / switch the current change |
| `verify` | Run the configured verify command and record the result |
| `review [id] --reviewer codex\|claude [--prompt-only]` | Pin the diff and have the other agent review it read-only |
| `test-lock on\|off\|status` | Lock test edits during a bug fix |
| `check [--staged \| --pre-push \| --base REF]` | Artifact lint, plan gate, and secret scan (for git hooks / CI) |
| `config get\|set <key> <json>` | Read/write `.sdlc/config.json` |
| `doctor [--run-verify]` | Check that the setup really works |

## Guards and gates

| Rule | Agent hook (early) | git hook | CI |
|---|---|---|---|
| No production code changes without an approved plan | — | pre-push | `sdlc-gate` |
| Artifact format and status consistency | — | pre-commit / pre-push | `sdlc-gate` |
| No secrets written | Inspect edits | pre-commit (staged) | `sdlc-gate` (diff) |
| No hand-editing generated files, lockfiles, or kit-managed files | Block | — | — |
| Only humans approve | Block `approve` / status rewrites | — | — |
| No rewriting tests during a bug fix | Test lock | — | — |
| No bypassing git hooks via `--no-verify` or changing `core.hooksPath` | Block | — | — |
| No reading `.env` | Block (also denied in settings for Claude Code) | — | — |

Agent hooks are convenient early feedback, not a complete boundary (writes via the shell, for example, slip through). The final guarantee lives in git hooks and CI, which apply no matter which tool—or human—wrote the code. If a hook itself hits malformed input or an internal error, it fails open so work is not blocked.

A single `hooks/hooks.json` is shared by both tools: both pass tool calls as JSON on stdin, treat exit code 2 as a block, and Codex also sets `CLAUDE_PLUGIN_ROOT` for compatibility. The only difference is how file edits are represented (Claude Code uses `file_path` on `Edit`/`Write`; Codex uses the patch body of `apply_patch`), which `sdlc.py` normalizes into the same shape. Sample inputs from both tools are in `tests/fixtures/`.

## Configuration (`.sdlc/config.json`)

| Key | Default | Meaning |
|---|---|---|
| `verify` | `make verify` | build + test + lint in one command; exits non-zero on failure |
| `plan_required_paths` | `src/**` `Sources/**` `lib/**` `app/**` | Production code that requires an approved plan |
| `protected_paths` | generated files, lockfiles | Paths agents must not hand-edit |
| `test_globs` | common test locations | Targets of the test lock |
| `format_command` | `""` | Run per file after edits (`{file}`) |
| `timezone` | system setting at setup | Date used in change IDs |
| `base_branch` | auto-detected | Comparison base for the plan gate |
| `language` | `ja` | Language for artifacts and reviews |
| `reviewer` | `codex` | Default when using `review --reviewer auto` from the terminal |
| `human_approval_only` | `true` | Disallow approval operations by agents |
| `kit_min_version` | version at setup | `check` fails with an older sdlc.py |
| `tool_min_versions` | claude 2.1.0 / codex 0.128.0 | Minimum versions doctor warns about |

Path patterns are gitignore-style (patterns containing `/` are anchored at the root, others match at any depth, `**` is supported, and a match on a parent directory counts).

## Development

```sh
python3 -m unittest discover -s tests -v   # CI runs on Python 3.9 / 3.13, Linux / macOS
claude plugin validate . && claude plugin validate plugins/sdlc
python3 scripts/release.py 0.2.0           # bump the version in sdlc.py, both manifests, and the marketplaces at once
```

Write the CHANGELOG, tag (`v0.2.0`), and push. Users pick up the update by refreshing the marketplace in Claude Code or running `codex plugin marketplace upgrade` in Codex, then rerun `sdlc-init` in each repository to update repo-side files (artifact frontmatter keeps the `kit: sdlc@<version>` it was created with).
