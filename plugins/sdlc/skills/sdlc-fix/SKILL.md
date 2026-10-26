---
name: sdlc-fix
description: Fix a bug test-first in an sdlc-kit repository — reproduce it as a failing test, commit the test, lock test files, then change only the code until the test passes. Use when the user reports a bug, regression or incident to fix ("バグを直して", "不具合を修正して", "fix this bug").
---

# sdlc-fix — test-first bug fix

A test that existed before the fix, and that the agent could not rewrite, is the proof that the bug is gone.

1. Change and plan: if there is no change for this bug yet, create one with `sdlc-intent` using `--source bug` (symptom, expected behavior, reproduction). Small fixes usually skip spec: write plan.md (with the reproduction test as the first step of Proof) and ask the user to approve it with `python3 .sdlc/bin/sdlc.py approve --spec-skipped "<reason>"`.
2. Reproduce: write a test that triggers the bug. Run it and confirm it fails **for the reason you expect** — show the failure output to the user. A test that fails for another reason does not count.
3. Commit the test alone: `test: reproduce <bug>` with the trailer `SDLC-Change: <id>`.
4. Lock tests: `python3 .sdlc/bin/sdlc.py test-lock on`. While locked, the guard blocks edits to files matching `test_globs`, and only the user can unlock.
5. Fix the code. Run the reproduction test, then `python3 .sdlc/bin/sdlc.py verify`, until both pass. If you come to believe the test is wrong, stop and explain why — do not work around the lock.
6. When green, ask the user to run `python3 .sdlc/bin/sdlc.py test-lock off`, update plan.md (proof results, deviations), and continue with `sdlc-review`.
