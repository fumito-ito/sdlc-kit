---
name: sdlc-intent
description: Start a change in an sdlc-kit repository by capturing an idea, feature request, bug report or alert as intent.md in a dated change directory under docs/changes/. Use when the user wants to start new work, brainstorm a feature, or reports a problem that should become work ("新しい変更を始めたい", "intentを書いて", "このアイデアを整理して", "start a change").
---

# sdlc-intent — Plan stage

Goal: the originator's idea, in their own words, as a committed `intent.md` that the next stage can act on. Requires an initialized repository (`.sdlc/config.json`); otherwise suggest the `sdlc-init` skill.

1. Let the user describe the problem in their own words first. Then interview like an analyst, a few questions at a time, until these are concrete:
   - what they cannot do today and who is affected (how often, how badly);
   - what better looks like — measurable where possible;
   - constraints (technical, security, compatibility, deadlines);
   - what is out of scope;
   - open questions.
   Do not invent answers. Unknowns go to "未解決の問い".
2. Choose a short ASCII slug (kebab-case, e.g. `claims-status`) and a title in the user's language. Create the change with the CLI — never `mkdir` it yourself:
   `python3 .sdlc/bin/sdlc.py new-change <slug> --title "<title>" --source <idea|bug|alert|scan|incident>`
   It prints the change id and makes it the current change.
3. Fill every section of the created `intent.md` without touching the frontmatter. Write in the language set in AGENTS.md.
4. Show the intent to the user and ask what you misunderstood. Apply their corrections.
5. Acceptance is the user's decision: ask them to run `python3 .sdlc/bin/sdlc.py approve` in their terminal. Do not change the status yourself — the guard blocks it.
6. Propose the commit: `git add <dir>/intent.md && git commit -m "intent: <title>" -m "SDLC-Change: <id>"`. Next stage: `sdlc-spec` (or `sdlc-plan` directly for a small change, recording why spec was skipped).
