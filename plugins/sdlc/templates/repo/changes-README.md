# Changes

One directory per change: `<YYYY-MM-DD>-<slug>/` containing `intent.md`, `spec.md`, `plan.md` and `review*.md`.
Together with the commits that carry `SDLC-Change: <id>`, they are the audit trail: who asked for what, what was decided, what was built and how it was reviewed.

Create and update them with `python3 .sdlc/bin/sdlc.py` (see `python3 .sdlc/bin/sdlc.py --help`), not by hand.

| artifact | statuses | who decides |
|---|---|---|
| intent.md | draft → accepted / rejected | the user (`sdlc.py approve`) |
| spec.md | draft → accepted / rejected | the user |
| plan.md | draft → approved / superseded | the user |
| review*.md | open → resolved | the author, after addressing findings |
