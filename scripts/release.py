#!/usr/bin/env python3
"""Bump the kit version everywhere it is recorded, in lockstep.

Usage: python3 scripts/release.py 0.2.0

Updates KIT_VERSION in sdlc.py, both plugin manifests and the Claude marketplace,
then runs the test suite. Commit, tag (vX.Y.Z) and push afterwards.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDLC = ROOT / "plugins/sdlc/bin/sdlc.py"
MANIFESTS = [ROOT / "plugins/sdlc/.claude-plugin/plugin.json", ROOT / "plugins/sdlc/.codex-plugin/plugin.json"]
MARKET = ROOT / ".claude-plugin/marketplace.json"


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print(__doc__)
        return 2
    v = sys.argv[1]
    text = SDLC.read_text(encoding="utf-8")
    text, n = re.subn(r'^KIT_VERSION = "[^"]+"$', 'KIT_VERSION = "%s"' % v, text, flags=re.M)
    if n != 1:
        print("KIT_VERSION not found in sdlc.py")
        return 1
    SDLC.write_text(text, encoding="utf-8")
    for m in MANIFESTS:
        data = json.loads(m.read_text(encoding="utf-8"))
        data["version"] = v
        dump(m, data)
    data = json.loads(MARKET.read_text(encoding="utf-8"))
    data["metadata"]["version"] = v
    for p in data["plugins"]:
        p["version"] = v
    dump(MARKET, data)
    print("version → %s; running tests…" % v)
    rc = subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=str(ROOT))
    if rc == 0:
        print("next: add a CHANGELOG entry, commit, `git tag v%s`, push with tags" % v)
    return rc


if __name__ == "__main__":
    sys.exit(main())
