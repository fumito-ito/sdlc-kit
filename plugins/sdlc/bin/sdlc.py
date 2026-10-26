#!/usr/bin/env python3
# managed-by: sdlc-kit — vendored into each repository as .sdlc/bin/sdlc.py by `sdlc.py init`.
# Do not edit the vendored copy: change the plugin and re-run the sdlc-init skill.
"""sdlc — CLI for the sdlc-kit artifact-driven SDLC (intent → spec → plan → build → review).

One stdlib-only file (Python 3.9+) shared by
  * the Claude Code / Codex plugin hooks  (hook pre-tool-use | post-tool-use | session-start)
  * the skills                            (new-change, add, status, review, verify, ...)
  * git hooks and CI                      (check)
so that every entry point applies exactly the same rules.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

KIT_VERSION = "0.2.0"
KIT_REPO = "fumito-ito/sdlc-kit"
MARKETPLACE = "sdlc-kit"
PLUGIN = "sdlc"
PLUGIN_ID = PLUGIN + "@" + MARKETPLACE

MANAGED_MARK = "managed-by: sdlc-kit"
BLOCK_BEGIN = "<!-- sdlc-kit:begin (managed; edits between these markers are overwritten) -->"
BLOCK_END = "<!-- sdlc-kit:end -->"
BLOCK_RE = re.compile(r"<!-- sdlc-kit:begin[^>]*-->.*?<!-- sdlc-kit:end -->", re.S)

ARTIFACT_STATUSES: Dict[str, Tuple[str, ...]] = {
    "intent": ("draft", "accepted", "rejected"),
    "spec": ("draft", "accepted", "rejected"),
    "plan": ("draft", "approved", "superseded"),
    "review": ("open", "resolved"),
}
DECISION_STATUSES = ("accepted", "approved", "rejected")
APPROVE_TARGET = {"intent": "accepted", "spec": "accepted", "plan": "approved"}
SOURCES = ("idea", "bug", "alert", "scan", "review", "incident")
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
ARTIFACT_FILE_RE = re.compile(r"^(intent|spec|plan|review)(?:-(\d+))?\.md$")
LANGUAGES = {"ja": "Japanese", "en": "English"}

KIT_PROTECTED = [".sdlc/bin/**", ".sdlc/githooks/**"]

DEFAULT_CONFIG: Dict[str, object] = {
    "kit_min_version": KIT_VERSION,
    "language": "ja",
    "timezone": "UTC",
    "base_branch": "main",
    "changes_dir": "docs/changes",
    "verify": "make verify",
    "plan_required_paths": ["src/**", "Sources/**", "lib/**", "app/**"],
    "protected_paths": [
        "**/Generated/**",
        "**/__generated__/**",
        "**/*.generated.*",
        "**/package-lock.json",
        "**/pnpm-lock.yaml",
        "**/yarn.lock",
        "**/Package.resolved",
        "**/Podfile.lock",
    ],
    "test_globs": [
        "**/tests/**",
        "**/test/**",
        "**/Tests/**",
        "**/__tests__/**",
        "**/spec/**",
        "**/*_test.*",
        "**/*.test.*",
        "**/*.spec.*",
        "**/test_*.py",
        "**/*Tests.swift",
        "**/*Test.swift",
    ],
    "format_command": "",
    "reviewer": "codex",
    "human_approval_only": True,
    "tool_min_versions": {"claude": "2.1.0", "codex": "0.128.0"},
}


def _secret_patterns():
    # Patterns are split so that this file never contains a literal secret-looking string.
    raw = [
        ("AWS access key id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
        ("Anthropic API key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
        ("OpenAI API key", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"),
        ("Slack token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
        ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        ("Stripe live key", r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b"),
        ("private key", "-----" + "BEGIN " + r"(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ]
    return [(name, re.compile(rx)) for name, rx in raw]


SECRET_PATTERNS = _secret_patterns()
ALLOW_SECRET_MARK = "sdlc:allow-secret"
SECRET_SCAN_SKIP = [".sdlc/bin/**"]


class SdlcError(Exception):
    pass


# --------------------------------------------------------------------------- utils


def _run(cmd: Sequence[str], cwd=None, input=None, timeout=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        input=input,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def git(root, *args: str, check: bool = True) -> str:
    p = _run(["git", *args], cwd=root)
    if check and p.returncode != 0:
        raise SdlcError("git %s failed: %s" % (" ".join(args), p.stderr.strip()))
    return p.stdout


def find_root(start=None) -> Path:
    start_path = Path(start) if start else Path(os.getcwd())
    if not start_path.exists():
        start_path = Path(os.getcwd())
    p = _run(["git", "rev-parse", "--show-toplevel"], cwd=start_path)
    if p.returncode != 0:
        raise SdlcError("not inside a git repository (%s)" % start_path)
    return Path(p.stdout.strip())


def vtuple(v) -> Tuple[int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def say(msg: str = "") -> None:
    print(msg)


def warn(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- config & state


def config_path(root: Path) -> Path:
    return root / ".sdlc" / "config.json"


def load_config(root: Path, required: bool = True) -> Optional[dict]:
    p = config_path(root)
    if not p.is_file():
        if required:
            raise SdlcError(
                "this repository is not initialized for sdlc-kit (.sdlc/config.json missing). "
                "Run the sdlc-init skill."
            )
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SdlcError(".sdlc/config.json is not valid JSON: %s" % e)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in data.items():
        if not k.startswith("$"):
            cfg[k] = v
    cfg["changes_dir"] = str(cfg["changes_dir"]).strip("/") or "docs/changes"
    return cfg


def read_raw_config(root: Path) -> dict:
    p = config_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def write_raw_config(root: Path, data: dict) -> None:
    p = config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def state_dir(root: Path) -> Path:
    d = root / ".sdlc" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_current(root: Path) -> Optional[str]:
    p = root / ".sdlc" / "state" / "current"
    if p.is_file():
        v = p.read_text(encoding="utf-8").strip()
        return v or None
    return None


def set_current(root: Path, cid: str) -> None:
    (state_dir(root) / "current").write_text(cid + "\n", encoding="utf-8")


def test_lock_path(root: Path) -> Path:
    return root / ".sdlc" / "state" / "test-lock"


def git_user(root: Path) -> str:
    name = git(root, "config", "user.name", check=False).strip()
    return name or os.environ.get("USER", "unknown")


# --------------------------------------------------------------------------- dates


def today(cfg: Optional[dict]) -> str:
    forced = os.environ.get("SDLC_TODAY")
    if forced:
        return forced
    tzname = (cfg or {}).get("timezone") or "UTC"
    tz = _dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        tz = ZoneInfo(str(tzname))
    except Exception:
        pass
    return _dt.datetime.now(tz).date().isoformat()


def detect_local_tz() -> str:
    env_tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in env_tz:
        return env_tz
    try:
        m = re.search(r"zoneinfo/(.+)$", os.path.realpath("/etc/localtime"))
        if m:
            return m.group(1)
    except OSError:
        pass
    return "UTC"


# --------------------------------------------------------------------------- globs

_GLOB_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _glob_regex(pattern: str):
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    raw = pattern.strip()
    if raw.startswith("./"):
        raw = raw[2:]
    leading_slash = raw.startswith("/")
    p = raw.strip("/")
    anchored = leading_slash or "/" in p
    out: List[str] = []
    i = 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if p.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        c = p[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = p.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                cls = p[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    body = "".join(out)
    if not anchored:
        body = "(?:.*/)?" + body
    rx = re.compile("^" + body + "$")
    _GLOB_CACHE[pattern] = rx
    return rx


def path_matches(path: str, patterns) -> Optional[str]:
    """Return the first gitignore-style pattern matching path (or one of its parent dirs)."""
    path = path.replace("\\", "/").strip("/")
    if not path:
        return None
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    for pat in patterns or []:
        if not str(pat).strip():
            continue
        rx = _glob_regex(str(pat))
        if any(rx.match(c) for c in candidates):
            return str(pat)
    return None


# --------------------------------------------------------------------------- frontmatter & templates

FM_RE = re.compile(r"\A---\n(.*?)\n---[ \t]*\n?", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    text = text.replace("\r\n", "\n")
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fm[k.strip()] = v
    return fm, text[m.end():]


def _yaml_scalar(v) -> str:
    s = str(v)
    if s == "" or re.search(r"(^[\s\-?:,\[\]{}#&*!|>'\"%@`])|(:\s)|(\s#)|(\s$)", s):
        return json.dumps(s, ensure_ascii=False)
    return s


def render_frontmatter(fm: Dict[str, str], body: str) -> str:
    lines = ["---"] + ["%s: %s" % (k, _yaml_scalar(v)) for k, v in fm.items()] + ["---"]
    return "\n".join(lines) + "\n" + body


def render(text: str, ctx: Dict[str, object]) -> str:
    return PLACEHOLDER_RE.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), text)


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def plugin_templates() -> Path:
    return PLUGIN_ROOT / "templates"


def is_plugin_copy() -> bool:
    return (plugin_templates() / "repo").is_dir()


_INITIAL_STATUS = {"intent": "draft", "spec": "draft", "plan": "draft", "review": "open"}


def _fallback_template(kind: str) -> str:
    extra = "spec_skipped: \"\"\n" if kind == "plan" else ""
    body = "{{body}}\n" if kind == "review" else ""
    return (
        "---\nid: {{id}}\nartifact: %s\nstatus: %s\n%screated: {{date}}\nkit: sdlc@{{kit_version}}\n---\n# %s: {{title}}\n\n%s"
        % (kind, _INITIAL_STATUS[kind], extra, kind.capitalize(), body)
    )


def load_change_template(root: Path, kind: str) -> str:
    for cand in (root / ".sdlc" / "templates" / (kind + ".md"), plugin_templates() / "changes" / (kind + ".md")):
        if cand.is_file():
            return cand.read_text(encoding="utf-8")
    return _fallback_template(kind)


# --------------------------------------------------------------------------- changes


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:60].strip("-")


def changes_root(root: Path, cfg: dict) -> Path:
    return root / cfg["changes_dir"]


def change_ids(root: Path, cfg: dict) -> List[str]:
    d = changes_root(root, cfg)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


def resolve_id(root: Path, cfg: dict, ref: Optional[str]) -> str:
    ids = change_ids(root, cfg)
    if not ref or ref == "current":
        cur = get_current(root)
        if not cur:
            raise SdlcError("no current change: pass a change id or run `sdlc.py use <id>`")
        ref = cur
    if ref in ids:
        return ref
    cands = [i for i in ids if i.endswith("-" + ref)] or [i for i in ids if ref in i]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise SdlcError("unknown change '%s'. Known: %s" % (ref, ", ".join(ids) or "(none)"))
    raise SdlcError("ambiguous change '%s': %s" % (ref, ", ".join(cands)))


def review_files(d: Path) -> List[Tuple[int, Path]]:
    out = []
    if d.is_dir():
        for p in d.iterdir():
            m = ARTIFACT_FILE_RE.match(p.name)
            if m and m.group(1) == "review":
                out.append((int(m.group(2) or 1), p))
    return sorted(out)


def artifact_file(root: Path, cfg: dict, cid: str, kind: str) -> Path:
    d = changes_root(root, cfg) / cid
    if kind == "review":
        files = review_files(d)
        return files[-1][1] if files else d / "review.md"
    return d / (kind + ".md")


def read_fm(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


def change_state(root: Path, cfg: dict, cid: str) -> Dict[str, Dict[str, str]]:
    found: Dict[str, Dict[str, str]] = {}
    for kind in ("intent", "spec", "plan", "review"):
        p = artifact_file(root, cfg, cid, kind)
        if p.is_file():
            found[kind] = read_fm(p)
    return found


def intent_title(root: Path, cfg: dict, cid: str) -> str:
    p = changes_root(root, cfg) / cid / "intent.md"
    if p.is_file():
        _, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        m = re.search(r"^#\s*Intent:\s*(.+)$", body, re.M)
        if m:
            return m.group(1).strip()
    return cid


def chain_errors(loc: str, found: Dict[str, Dict[str, str]]) -> List[str]:
    errs: List[str] = []

    def st(k: str) -> Optional[str]:
        return (found.get(k) or {}).get("status")

    if st("spec") == "accepted" and st("intent") != "accepted":
        errs.append("%s: spec is accepted but intent is '%s'" % (loc, st("intent")))
    if st("plan") == "approved":
        if st("intent") != "accepted":
            errs.append("%s: plan is approved but intent is '%s'" % (loc, st("intent")))
        if "spec" in found:
            if st("spec") != "accepted":
                errs.append("%s: plan is approved but spec is '%s'" % (loc, st("spec")))
        elif not (found["plan"].get("spec_skipped") or "").strip():
            errs.append(
                "%s: plan is approved without spec.md; record why with `spec_skipped: <reason>` in plan.md" % loc
            )
    return errs


# --------------------------------------------------------------------------- commands: artifacts


def cmd_new_change(a) -> int:
    root = find_root()
    cfg = load_config(root)
    slug = slugify(a.slug)
    if not slug:
        raise SdlcError("the slug needs ASCII letters or digits (e.g. claims-status); put a Japanese title in --title")
    if a.source not in SOURCES:
        raise SdlcError("--source must be one of: %s" % ", ".join(SOURCES))
    date = today(cfg)
    base = "%s-%s" % (date, slug)
    croot = changes_root(root, cfg)
    cid, n = base, 2
    while (croot / cid).exists():
        cid = "%s-%d" % (base, n)
        n += 1
    d = croot / cid
    d.mkdir(parents=True)
    ctx = {"id": cid, "date": date, "source": a.source, "kit_version": KIT_VERSION, "title": a.title or slug}
    (d / "intent.md").write_text(render(load_change_template(root, "intent"), ctx), encoding="utf-8")
    set_current(root, cid)
    say("created %s/intent.md" % d.relative_to(root).as_posix())
    say("id: %s (now the current change)" % cid)
    return 0


def cmd_add(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cid = resolve_id(root, cfg, a.id)
    kind = a.artifact
    d = changes_root(root, cfg) / cid
    if not (d / "intent.md").is_file():
        raise SdlcError("%s has no intent.md" % cid)
    if kind == "review":
        n = len(review_files(d)) + 1
        path = d / ("review.md" if n == 1 else "review-%d.md" % n)
    else:
        path = d / (kind + ".md")
        if path.exists() and not a.force:
            raise SdlcError("%s already exists (use --force to recreate it)" % path.relative_to(root))
    body = ""
    if a.body_file:
        body = Path(a.body_file).read_text(encoding="utf-8").strip() + "\n"
    ctx = {
        "id": cid,
        "date": today(cfg),
        "kit_version": KIT_VERSION,
        "title": intent_title(root, cfg, cid),
        "body": body,
        "reviewer": "",
        "round": str(len(review_files(d)) + 1) if kind == "review" else "",
    }
    text = render(load_change_template(root, kind), ctx)
    if body and "{{body}}" not in load_change_template(root, kind):
        text = text.rstrip("\n") + "\n\n" + body
    if a.set:
        fm, rest = parse_frontmatter(text)
        for kv in a.set:
            if "=" not in kv:
                raise SdlcError("--set expects key=value, got '%s'" % kv)
            k, v = kv.split("=", 1)
            fm[k.strip()] = v.strip()
        text = render_frontmatter(fm, rest)
    path.write_text(text, encoding="utf-8")
    say("created %s" % path.relative_to(root).as_posix())
    return 0


def _set_status(root: Path, cfg: dict, cid: str, kind: str, status: str, spec_skipped: Optional[str] = None) -> Path:
    if status not in ARTIFACT_STATUSES[kind]:
        raise SdlcError("%s status must be one of: %s" % (kind, ", ".join(ARTIFACT_STATUSES[kind])))
    path = artifact_file(root, cfg, cid, kind)
    if not path.is_file():
        raise SdlcError("%s does not exist" % path.relative_to(root))
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not fm:
        raise SdlcError("%s has no frontmatter" % path.relative_to(root))
    fm["status"] = status
    if spec_skipped is not None:
        fm["spec_skipped"] = spec_skipped
    if status in DECISION_STATUSES:
        fm["decided"] = today(cfg)
        fm["decided_by"] = git_user(root)
    found = change_state(root, cfg, cid)
    found[kind] = fm
    errs = chain_errors(cid, found)
    if errs:
        raise SdlcError("; ".join(errs))
    path.write_text(render_frontmatter(fm, body), encoding="utf-8")
    return path


def cmd_set_status(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cid = resolve_id(root, cfg, a.id)
    path = _set_status(root, cfg, cid, a.artifact, a.status, a.spec_skipped)
    say("%s: %s → %s" % (path.relative_to(root).as_posix(), a.artifact, a.status))
    return 0


def cmd_approve(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cid = resolve_id(root, cfg, a.id)
    found = change_state(root, cfg, cid)
    for kind in ("intent", "spec", "plan"):
        if kind in found and found[kind].get("status") == "draft":
            target = APPROVE_TARGET[kind]
            if a.reject:
                target = "rejected" if kind != "plan" else "superseded"
            path = _set_status(root, cfg, cid, kind, target, a.spec_skipped if kind == "plan" else None)
            say("%s → %s (by %s)" % (path.relative_to(root).as_posix(), target, git_user(root)))
            return 0
    say("%s: nothing is waiting for approval" % cid)
    return 0


def cmd_use(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cid = resolve_id(root, cfg, a.id)
    set_current(root, cid)
    say("current change: %s" % cid)
    return 0


def _status_rows(root: Path, cfg: dict) -> List[Dict[str, str]]:
    rows = []
    for cid in change_ids(root, cfg):
        found = change_state(root, cfg, cid)
        row = {"id": cid}
        for kind in ("intent", "spec", "plan", "review"):
            row[kind] = (found.get(kind) or {}).get("status", "-")
        if "review" in found:
            row["verdict"] = found["review"].get("verdict", "")
        if "plan" in found and found["plan"].get("spec_skipped"):
            row["spec"] = "skipped"
        rows.append(row)
    return rows


def cmd_status(a) -> int:
    root = find_root()
    cfg = load_config(root)
    rows = _status_rows(root, cfg)
    cur = get_current(root)
    if a.json:
        say(json.dumps({"current": cur, "test_lock": test_lock_path(root).exists(), "changes": rows}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        say("no changes yet — start one with the sdlc-intent skill")
    else:
        w = max(len(r["id"]) for r in rows)
        say("  %-*s  %-9s %-9s %-10s %s" % (w, "change", "intent", "spec", "plan", "review"))
        for r in rows:
            mark = "*" if r["id"] == cur else " "
            rev = r["review"] + ((" (%s)" % r["verdict"]) if r.get("verdict") else "")
            say("%s %-*s  %-9s %-9s %-10s %s" % (mark, w, r["id"], r["intent"], r["spec"], r["plan"], rev))
    if test_lock_path(root).exists():
        say("tests: LOCKED (bug-fix mode)")
    return 0


# --------------------------------------------------------------------------- check (git hooks & CI gate)


def _list_change_files(root: Path, cfg: dict, ref: Optional[str]) -> Dict[str, List[str]]:
    cdir = cfg["changes_dir"]
    files: List[str] = []
    if ref is None:
        base = root / cdir
        if base.is_dir():
            files = [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()]
    else:
        files = git(root, "ls-tree", "-r", "--name-only", ref, "--", cdir, check=False).splitlines()
    groups: Dict[str, List[str]] = {}
    for f in files:
        rest = f[len(cdir) + 1 :]
        parts = rest.split("/")
        if len(parts) != 2:
            continue
        groups.setdefault(parts[0], []).append(parts[1])
    return groups


def _read_at(root: Path, ref: Optional[str], rel: str) -> Optional[str]:
    if ref is None:
        p = root / rel
        return p.read_text(encoding="utf-8") if p.is_file() else None
    p = _run(["git", "show", "%s:%s" % (ref, rel)], cwd=root)
    return p.stdout if p.returncode == 0 else None


def lint_changes(root: Path, cfg: dict, ref: Optional[str] = None) -> Tuple[List[str], int]:
    errs: List[str] = []
    cdir = cfg["changes_dir"]
    groups = _list_change_files(root, cfg, ref)
    for cid, names in sorted(groups.items()):
        loc = "%s/%s" % (cdir, cid)
        if not ID_RE.match(cid):
            errs.append("%s: directory name must be YYYY-MM-DD-<slug> (lowercase ascii, digits, hyphens)" % loc)
        found: Dict[str, Dict[str, str]] = {}
        for name in sorted(names, key=lambda n: (len(n), n)):
            m = ARTIFACT_FILE_RE.match(name)
            if not m:
                continue
            kind = m.group(1)
            where = "%s/%s" % (loc, name)
            fm, _ = parse_frontmatter(_read_at(root, ref, where) or "")
            if not fm:
                errs.append("%s: missing frontmatter" % where)
                continue
            if fm.get("id") != cid:
                errs.append("%s: id '%s' does not match the directory name" % (where, fm.get("id")))
            if fm.get("artifact") != kind:
                errs.append("%s: artifact should be '%s'" % (where, kind))
            if fm.get("status") not in ARTIFACT_STATUSES[kind]:
                errs.append(
                    "%s: status '%s' is not one of %s" % (where, fm.get("status"), "/".join(ARTIFACT_STATUSES[kind]))
                )
            found[kind] = fm
        if "intent" not in found:
            errs.append("%s: intent.md is missing" % loc)
        errs += chain_errors(loc, found)
    return errs, len(groups)


def scan_diff_for_secrets(diff: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    current: Optional[str] = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current and path_matches(current, SECRET_SCAN_SKIP):
                current = None
            continue
        if current and line.startswith("+") and ALLOW_SECRET_MARK not in line:
            for name, rx in SECRET_PATTERNS:
                if rx.search(line):
                    hits.append((current, name))
                    break
    return hits


def scan_lines_for_secrets(lines: Sequence[str]) -> List[str]:
    found = []
    for line in lines:
        if ALLOW_SECRET_MARK in line:
            continue
        for name, rx in SECRET_PATTERNS:
            if rx.search(line):
                found.append(name)
                break
    return found


ZERO_SHA = "0" * 40


def _ref_exists(root: Path, ref: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd=root).returncode == 0


def gate_range(root: Path, cfg: dict, base: str, head: str) -> Tuple[List[str], List[str]]:
    """Plan gate: code under plan_required_paths needs an approved plan (or an explicit exemption)."""
    errs: List[str] = []
    notes: List[str] = []
    files = git(root, "diff", "--name-only", "%s...%s" % (base, head)).splitlines()
    cdir = cfg["changes_dir"]
    code = [f for f in files if not f.startswith(cdir + "/") and path_matches(f, cfg.get("plan_required_paths"))]
    log = git(root, "log", "--format=%B", "%s..%s" % (base, head))
    for f, name in scan_diff_for_secrets(git(root, "diff", "-U0", "--no-color", "%s...%s" % (base, head))):
        errs.append("possible %s added in %s (mark the line with '%s' if it is a false positive)" % (name, f, ALLOW_SECRET_MARK))
    if not code:
        return errs, notes
    exempt = re.findall(r"^SDLC-Exempt:[ \t]*(\S.*)$", log, re.M)
    if exempt:
        notes.append("plan gate skipped by SDLC-Exempt: %s" % "; ".join(sorted(set(exempt))))
        return errs, notes
    ids = set()
    for f in files:
        if f.startswith(cdir + "/"):
            parts = f[len(cdir) + 1 :].split("/")
            if len(parts) == 2:
                ids.add(parts[0])
    ids |= set(re.findall(r"^SDLC-Change:[ \t]*(\S+)[ \t]*$", log, re.M))
    approved = []
    for cid in sorted(ids):
        fm, _ = parse_frontmatter(_read_at(root, head, "%s/%s/plan.md" % (cdir, cid)) or "")
        if fm.get("status") == "approved":
            approved.append(cid)
    if approved:
        notes.append("plan gate: covered by approved plan(s) %s" % ", ".join(approved))
        return errs, notes
    shown = ", ".join(code[:8]) + (" …" if len(code) > 8 else "")
    errs.append(
        "plan gate: %d file(s) under plan_required_paths changed (%s) but no approved plan.md is referenced. "
        "Touch the change directory in this range, add a commit trailer 'SDLC-Change: <id>' for a change whose plan is "
        "approved, or — for a trivial change — a trailer 'SDLC-Exempt: <reason>'." % (len(code), shown)
    )
    return errs, notes


def _pre_push_ranges(root: Path, cfg: dict, stdin_text: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    ranges: List[Tuple[str, str]] = []
    notes: List[str] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        if remote_sha != ZERO_SHA and _ref_exists(root, remote_sha):
            ranges.append((remote_sha, local_sha))
            continue
        base_ref = "origin/%s" % cfg.get("base_branch", "main")
        if _ref_exists(root, base_ref):
            ranges.append((base_ref, local_sha))
        else:
            notes.append("no %s to compare against (first push?): plan gate skipped, artifacts still linted" % base_ref)
    return ranges, notes


def cmd_check(a) -> int:
    root = find_root()
    cfg = load_config(root)
    errs: List[str] = []
    notes: List[str] = []
    if vtuple(KIT_VERSION) < vtuple(cfg.get("kit_min_version", "0")):
        errs.append(
            "this sdlc.py is %s but .sdlc/config.json requires kit_min_version %s — update the plugin and re-run sdlc-init"
            % (KIT_VERSION, cfg.get("kit_min_version"))
        )
    ranges: List[Tuple[str, str]] = []
    if a.pre_push:
        r, n = _pre_push_ranges(root, cfg, sys.stdin.read() if not sys.stdin.isatty() else "")
        ranges += r
        notes += n
    linted = 0
    if a.base:
        if a.base == ZERO_SHA or not _ref_exists(root, a.base):
            notes.append("base %s not available: plan gate skipped, artifacts linted at %s" % (a.base, a.head))
            lint_errs, linted = lint_changes(root, cfg, a.head)
            errs += lint_errs
        else:
            ranges.append((a.base, a.head))
    for base, head in ranges:
        lint_errs, linted = lint_changes(root, cfg, head)
        errs += lint_errs
        g_errs, g_notes = gate_range(root, cfg, base, head)
        errs += g_errs
        notes += g_notes
    if a.staged or not (a.pre_push or a.base):
        lint_errs, linted = lint_changes(root, cfg, None)
        errs += lint_errs
    if a.staged:
        for f, name in scan_diff_for_secrets(git(root, "diff", "--cached", "-U0", "--no-color")):
            errs.append("possible %s staged in %s (mark the line with '%s' if it is a false positive)" % (name, f, ALLOW_SECRET_MARK))
    errs = list(dict.fromkeys(errs))
    for n in notes:
        say("[sdlc] %s" % n)
    if errs:
        warn("[sdlc] check FAILED")
        for e in errs:
            warn("  ✗ %s" % e)
        return 1
    say("[sdlc] check OK (%d change(s) linted)" % linted)
    return 0


# --------------------------------------------------------------------------- hooks (Claude Code & Codex)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.M)
PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.M)
ENV_FILE_RE = re.compile(r"^\.env(?:\..+)?$")
ENV_OK_SUFFIXES = (".example", ".sample", ".template", ".dist")
STATUS_LINE_RE = re.compile(r"^\s*status:\s*[\"']?(accepted|approved|rejected)[\"']?\s*$")


def _patch_info(patch: str) -> Tuple[List[str], List[str]]:
    paths = PATCH_FILE_RE.findall(patch) + PATCH_MOVE_RE.findall(patch)
    added = [l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return paths, added


def normalize_tool_call(payload: dict) -> dict:
    """Map a Claude Code or Codex PreToolUse/PostToolUse payload onto one shape."""
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input")
    if isinstance(ti, str):
        ti = {"command": ti}
    if not isinstance(ti, dict):
        ti = {}
    info = {"tool": tool, "kind": "other", "paths": [], "added": [], "command": "", "cwd": payload.get("cwd") or os.getcwd()}
    if tool in EDIT_TOOLS:
        info["kind"] = "edit"
        if tool == "apply_patch":
            patch = ti.get("command") or ti.get("patch") or ti.get("input") or ""
            if isinstance(patch, list):
                patch = "\n".join(str(x) for x in patch)
            info["paths"], info["added"] = _patch_info(str(patch))
        else:
            fp = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
            if fp:
                info["paths"] = [str(fp)]
            if tool == "Write":
                info["added"] = str(ti.get("content", "")).splitlines()
            elif tool == "Edit":
                info["added"] = str(ti.get("new_string", "")).splitlines()
            elif tool == "MultiEdit":
                for e in ti.get("edits") or []:
                    if isinstance(e, dict):
                        info["added"] += str(e.get("new_string", "")).splitlines()
            elif tool == "NotebookEdit":
                info["added"] = str(ti.get("new_source", "")).splitlines()
    elif tool == "Bash":
        info["kind"] = "bash"
        cmd = ti.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        info["command"] = str(cmd)
        if "*** Begin Patch" in info["command"]:
            info["paths"], info["added"] = _patch_info(info["command"])
    elif tool == "Read":
        info["kind"] = "read"
        fp = ti.get("file_path")
        if fp:
            info["paths"] = [str(fp)]
    return info


def rel_to_root(p: str, cwd: str, root: Path) -> Optional[str]:
    pp = Path(p)
    if not pp.is_absolute():
        pp = Path(cwd) / pp
    real = os.path.realpath(str(pp))
    rr = os.path.realpath(str(root))
    rel = os.path.relpath(real, rr)
    if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
        return None
    return rel.replace(os.sep, "/")


def _is_env_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return bool(ENV_FILE_RE.match(name)) and not name.endswith(ENV_OK_SUFFIXES)


def _git_commit_bypass(cmd: str) -> bool:
    if "--no-verify" in cmd and re.search(r"\bgit\b", cmd):
        return True
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    separators = {"&&", "||", ";", "|"}
    for i, t in enumerate(tokens):
        if t != "git" or "commit" not in tokens[i + 1 : i + 4]:
            continue
        for u in tokens[i + 1 :]:
            if u in separators:
                break
            if u == "-n":
                return True
    return False


def guard(root: Path, cfg: dict, info: dict) -> List[str]:
    """Return blocking messages for a normalized tool call (empty list = allow)."""
    msgs: List[str] = []
    rels = [r for r in (rel_to_root(p, info["cwd"], root) for p in info["paths"]) if r]
    cdir = cfg["changes_dir"]
    if info["kind"] in ("edit", "bash") and rels:
        locked = test_lock_path(root).exists()
        for r in rels:
            pat = path_matches(r, KIT_PROTECTED + list(cfg.get("protected_paths") or []))
            if pat:
                msgs.append(
                    "[sdlc] %s is protected (pattern '%s'). Regenerate it with its own tool, or ask the user to change "
                    "protected_paths in .sdlc/config.json through a reviewed change." % (r, pat)
                )
            if locked and path_matches(r, cfg.get("test_globs")):
                msgs.append(
                    "[sdlc] Tests are locked for this bug fix (%s matches test_globs). Fix the code, not the test. "
                    "If the test itself is wrong, stop and explain why to the user; only the user can run "
                    "`python3 .sdlc/bin/sdlc.py test-lock off`." % r
                )
            if cfg.get("human_approval_only", True) and re.match(
                r"^%s/[^/]+/(intent|spec|plan)\.md$" % re.escape(cdir), r
            ):
                new_status = None
                for line in info["added"]:
                    m = STATUS_LINE_RE.match(line)
                    if m:
                        new_status = m.group(1)
                if new_status and read_fm(root / r).get("status") != new_status:
                    msgs.append(
                        "[sdlc] Setting %s to '%s' is the user's decision. Ask the user to run "
                        "`python3 .sdlc/bin/sdlc.py approve` in their terminal." % (r, new_status)
                    )
        hits = scan_lines_for_secrets(info["added"])
        if hits:
            msgs.append(
                "[sdlc] The edit looks like it contains a %s. Never write credentials into the repository; use an "
                "environment variable or a secret store. (False positive? add '%s' on that line.)"
                % (hits[0], ALLOW_SECRET_MARK)
            )
    if info["kind"] == "read":
        for r in rels:
            if _is_env_file(r):
                msgs.append("[sdlc] %s may contain secrets; reading it is blocked. Ask the user for the specific value you need." % r)
    cmd = info["command"]
    if cmd:
        if _git_commit_bypass(cmd):
            msgs.append("[sdlc] Git hooks are part of the SDLC gate; do not bypass them with --no-verify. Fix what the hook reports, or ask the user.")
        if re.search(r"\bcore\.hooksPath\b", cmd) and re.search(r"\bgit\b.*\bconfig\b", cmd):
            msgs.append("[sdlc] core.hooksPath wires the SDLC git hooks; do not change it. Ask the user if it needs to change.")
        if cfg.get("human_approval_only", True) and re.search(
            r"sdlc\.py[\"']?\s+(?:approve\b|set-status\b[^\n;&|]*\b(?:accepted|approved|rejected)\b)", cmd
        ):
            msgs.append(
                "[sdlc] Approvals are the user's decision. Show the user the artifact and ask them to run "
                "`python3 .sdlc/bin/sdlc.py approve` in their terminal."
            )
        if re.search(r"sdlc\.py[\"']?\s+test-lock\s+off\b", cmd) or re.search(r"\b(?:rm|mv|unlink)\b[^\n;&|]*\.sdlc/state/test-lock", cmd):
            msgs.append("[sdlc] Only the user can unlock tests. Explain why the test is wrong and ask them to run `python3 .sdlc/bin/sdlc.py test-lock off`.")
    return list(dict.fromkeys(msgs))


def _hook_context(payload: dict) -> Tuple[Optional[Path], Optional[dict]]:
    try:
        root = find_root(payload.get("cwd") or os.getcwd())
    except SdlcError:
        return None, None
    try:
        return root, load_config(root, required=False)
    except SdlcError:
        return root, None


def hook_pre_tool_use(payload: dict) -> int:
    root, cfg = _hook_context(payload)
    if root is None or cfg is None:
        return 0
    msgs = guard(root, cfg, normalize_tool_call(payload))
    if msgs:
        warn("\n".join(msgs))
        return 2
    return 0


def hook_post_tool_use(payload: dict) -> int:
    root, cfg = _hook_context(payload)
    if root is None or cfg is None:
        return 0
    fmt = str(cfg.get("format_command") or "").strip()
    info = normalize_tool_call(payload)
    if not fmt or info["kind"] not in ("edit", "bash") or not info["paths"]:
        return 0
    for p in info["paths"]:
        r = rel_to_root(p, info["cwd"], root)
        if not r or not (root / r).is_file() or path_matches(r, KIT_PROTECTED + list(cfg.get("protected_paths") or [])):
            continue
        cmd = fmt.replace("{file}", shlex.quote(str(root / r)))
        try:
            subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True, timeout=60)
        except Exception:
            pass
    return 0


def vendored_version(root: Path) -> Optional[str]:
    p = root / ".sdlc" / "bin" / "sdlc.py"
    if not p.is_file():
        return None
    m = re.search(r'^KIT_VERSION = "([^"]+)"', p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def hook_session_start(payload: dict) -> int:
    root, cfg = _hook_context(payload)
    if root is None or cfg is None:
        return 0
    lines = [
        "[sdlc-kit %s] This repository follows the sdlc-kit workflow (see the SDLC section in AGENTS.md): "
        "intent → spec → plan (approved by the user) → implement → verify → cross-agent review. "
        "Use `python3 .sdlc/bin/sdlc.py` for artifact operations." % KIT_VERSION
    ]
    vend = vendored_version(root)
    if vend and vtuple(vend) < vtuple(KIT_VERSION):
        lines.append("- Repo files are from sdlc-kit %s, the plugin is %s: suggest running the sdlc-init skill to update them." % (vend, KIT_VERSION))
    if vtuple(KIT_VERSION) < vtuple(cfg.get("kit_min_version", "0")):
        lines.append("- WARNING: the installed plugin (%s) is older than kit_min_version %s. Ask the user to update the sdlc plugin." % (KIT_VERSION, cfg.get("kit_min_version")))
    cur = get_current(root)
    if cur and (changes_root(root, cfg) / cur).is_dir():
        found = change_state(root, cfg, cur)
        states = ", ".join("%s: %s" % (k, found[k].get("status", "?")) for k in ("intent", "spec", "plan", "review") if k in found)
        lines.append("- Current change: %s (%s)" % (cur, states))
    if test_lock_path(root).exists():
        lines.append("- Tests are LOCKED (bug-fix mode): fix the code, not the tests.")
    say("\n".join(lines))
    return 0


def cmd_hook(a) -> int:
    raw = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        payload = {}
    try:
        if a.event == "pre-tool-use":
            return hook_pre_tool_use(payload)
        if a.event == "post-tool-use":
            return hook_post_tool_use(payload)
        if a.event == "session-start":
            return hook_session_start(payload)
    except Exception as e:  # guards are a convenience layer: never wedge the agent on a bug here
        warn("[sdlc] hook error ignored: %s" % e)
    return 0


# --------------------------------------------------------------------------- verify, test-lock, config


def cmd_verify(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cmd = str(cfg.get("verify") or "").strip()
    if not cmd:
        raise SdlcError("no verify command configured (sdlc.py config set verify '\"make verify\"')")
    say("[sdlc] verify: %s" % cmd)
    started = time.time()
    rc = subprocess.run(cmd, shell=True, cwd=str(root)).returncode
    elapsed = time.time() - started
    head = git(root, "rev-parse", "HEAD", check=False).strip()
    (state_dir(root) / "last-verify.json").write_text(
        json.dumps({"command": cmd, "exit_code": rc, "head": head, "seconds": round(elapsed, 1), "at": _dt.datetime.now().isoformat(timespec="seconds")}, indent=2) + "\n",
        encoding="utf-8",
    )
    say("[sdlc] verify %s (exit %d, %.1fs)" % ("passed" if rc == 0 else "FAILED", rc, elapsed))
    return rc


def cmd_test_lock(a) -> int:
    root = find_root()
    load_config(root)
    p = test_lock_path(root)
    if a.state == "on":
        state_dir(root)
        p.write_text(json.dumps({"by": git_user(root), "at": _dt.datetime.now().isoformat(timespec="seconds"), "change": get_current(root)}) + "\n", encoding="utf-8")
        say("tests locked: edits to test_globs are blocked until the user runs `sdlc.py test-lock off`")
    elif a.state == "off":
        if p.exists():
            p.unlink()
        say("tests unlocked")
    else:
        say("tests: %s" % ("LOCKED" if p.exists() else "unlocked"))
    return 0


def cmd_config(a) -> int:
    root = find_root()
    load_config(root)
    data = read_raw_config(root)
    if a.action == "get":
        cfg = load_config(root)
        val = cfg if not a.key else cfg.get(a.key)
        say(json.dumps(val, ensure_ascii=False, indent=2))
        return 0
    if not a.key or a.value is None:
        raise SdlcError("usage: config set <key> <json-or-string>")
    try:
        val = json.loads(a.value)
    except ValueError:
        val = a.value
    if a.key not in DEFAULT_CONFIG:
        warn("note: '%s' is not a known sdlc-kit key" % a.key)
    data[a.key] = val
    write_raw_config(root, data)
    say("%s = %s" % (a.key, json.dumps(val, ensure_ascii=False)))
    return 0


# --------------------------------------------------------------------------- review (cross-agent)


def default_base(root: Path, cfg: dict) -> str:
    b = str(cfg.get("base_branch") or "main")
    for cand in ("origin/" + b, b):
        if _ref_exists(root, cand):
            return cand
    raise SdlcError("cannot find base branch '%s' (pass --base)" % b)


def build_review_prompt(root: Path, cfg: dict, cid: str, base: str, diff_file: str, dirty: bool) -> str:
    cdir = cfg["changes_dir"]
    lang = LANGUAGES.get(str(cfg.get("language")), str(cfg.get("language") or "English"))
    docs = ["- %s/%s/%s" % (cdir, cid, n) for n in ("intent.md", "spec.md", "plan.md") if (changes_root(root, cfg) / cid / n).is_file()]
    policy = "- REVIEW.md — the review policy (passes, severity, what to skip)" if (root / "REVIEW.md").is_file() else "- (no REVIEW.md: use Bugs / Security / Compliance passes)"
    return "\n".join(
        [
            "You are an independent reviewer for change %s in this repository. You did not write this code." % cid,
            "Do not modify any file. Read only.",
            "",
            "Read first:",
            policy,
            "- AGENTS.md — project conventions",
            *docs,
            "",
            "Then review the change. The exact diff under review (merge-base with %s → working tree%s) is saved at:" % (base, ", includes uncommitted changes" if dirty else ""),
            "- %s" % diff_file,
            "Open any file in the repository for context.",
            "",
            "Write the review in %s as Markdown with exactly these sections and nothing else:" % lang,
            "## Summary — two or three lines: what the change does and whether it matches plan.md.",
            "## Findings — one bullet per finding: `- [Important|Nit] (Bugs|Security|Compliance) path:line — problem → suggested fix`.",
            "  Important = would break behavior, leak data or breach a policy/spec/plan. Style and naming are Nits.",
            "  Report at most 5 Nits and summarize the rest as a count. Write `- none` if there are no findings.",
            "## Plan conformance — does the diff match plan.md (files, order, proof)? List deviations that plan.md does not record.",
            "## Verdict — end with exactly one line: `Verdict: important-findings` or `Verdict: no-important-findings`.",
        ]
    )


def _pick_reviewer(cfg: dict, requested: str) -> str:
    if requested != "auto":
        return requested
    if os.environ.get("CLAUDECODE"):
        return "codex"
    if any(k.startswith("CODEX_") for k in os.environ):
        return "claude"
    return str(cfg.get("reviewer") or "codex")


def cmd_review(a) -> int:
    root = find_root()
    cfg = load_config(root)
    cid = resolve_id(root, cfg, a.id)
    base = a.base or default_base(root, cfg)
    mb = git(root, "merge-base", base, "HEAD").strip()
    head = git(root, "rev-parse", "HEAD").strip()
    diff = git(root, "diff", "--no-color", mb)
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=no").strip())
    untracked = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    if not diff.strip():
        raise SdlcError("nothing to review: no difference between %s and the working tree" % base)
    rdir = state_dir(root) / "review"
    rdir.mkdir(exist_ok=True)
    diff_path = rdir / ("%s.diff" % cid)
    diff_path.write_text(diff, encoding="utf-8")
    diff_rel = diff_path.relative_to(root).as_posix()
    prompt = build_review_prompt(root, cfg, cid, base, diff_rel, dirty)
    if a.prompt_only:
        say(prompt)
        return 0
    if untracked:
        warn("[sdlc] note: %d untracked file(s) are not part of the reviewed diff (git add them first if they belong to the change)" % len(untracked))
    reviewer = _pick_reviewer(cfg, a.reviewer)
    exe = shutil.which(reviewer)
    if not exe:
        raise SdlcError("reviewer CLI '%s' not found on PATH; install it or pass --reviewer" % reviewer)
    say("[sdlc] reviewing %s with %s (read-only)… this can take a few minutes" % (cid, reviewer))
    out_file = rdir / ("%s.out.md" % cid)
    if out_file.exists():
        out_file.unlink()
    if reviewer == "codex":
        cmd = [exe, "exec", "--sandbox", "read-only", "-o", str(out_file), prompt]
    elif reviewer == "claude":
        cmd = [exe, "-p", prompt, "--allowedTools", "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*)"]
    else:
        raise SdlcError("unsupported reviewer '%s' (codex or claude)" % reviewer)
    try:
        p = _run(cmd, cwd=root, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        raise SdlcError("%s did not finish within %ds" % (reviewer, a.timeout))
    body = out_file.read_text(encoding="utf-8") if out_file.is_file() else ""
    if not body.strip():
        body = p.stdout
    if p.returncode != 0 or not body.strip():
        raise SdlcError("%s review failed (exit %d): %s" % (reviewer, p.returncode, (p.stderr or "")[-2000:]))
    m = re.findall(r"Verdict:\s*`?(important-findings|no-important-findings)`?", body)
    verdict = m[-1] if m else "unknown"
    d = changes_root(root, cfg) / cid
    n = len(review_files(d)) + 1
    path = d / ("review.md" if n == 1 else "review-%d.md" % n)
    fm = {
        "id": cid,
        "artifact": "review",
        "status": "open",
        "reviewer": reviewer,
        "round": str(n),
        "base": mb[:12],
        "head": head[:12],
        "dirty": "true" if dirty else "false",
        "verdict": verdict,
        "created": today(cfg),
        "kit": "sdlc@" + KIT_VERSION,
    }
    title = "# Review: %s (round %d, by %s)\n\n" % (intent_title(root, cfg, cid), n, reviewer)
    path.write_text(render_frontmatter(fm, title + body.strip() + "\n"), encoding="utf-8")
    say("wrote %s — verdict: %s" % (path.relative_to(root).as_posix(), verdict))
    return 0


# --------------------------------------------------------------------------- init (run from the plugin)


class Plan:
    def __init__(self, root: Path, dry: bool):
        self.root = root
        self.dry = dry
        self.rows: List[Tuple[str, str, str]] = []

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def _write(self, p: Path, content: str, executable: bool) -> None:
        if self.dry:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if executable:
            p.chmod(0o755)

    def create(self, p: Path, content: str, executable: bool = False) -> None:
        """Create a user-owned file once; never overwrite."""
        if p.exists():
            self.rows.append(("unchanged", self.rel(p), "exists (user-owned)"))
            return
        self._write(p, content, executable)
        self.rows.append(("created", self.rel(p), ""))

    def managed(self, p: Path, content: str, executable: bool = False) -> None:
        """Kit-owned file: (re)written when it carries the managed marker."""
        if not p.exists():
            self._write(p, content, executable)
            self.rows.append(("created", self.rel(p), ""))
            return
        cur = p.read_text(encoding="utf-8")
        if MANAGED_MARK not in cur:
            self.rows.append(("skipped", self.rel(p), "exists without '%s' marker; left untouched" % MANAGED_MARK))
            return
        if cur == content and (not executable or os.access(str(p), os.X_OK)):
            self.rows.append(("unchanged", self.rel(p), ""))
            return
        self._write(p, content, executable)
        self.rows.append(("updated", self.rel(p), ""))

    def block(self, p: Path, block: str, new_file: str) -> None:
        """Upsert the sdlc-kit managed block inside a user-owned Markdown file."""
        wrapped = "%s\n%s\n%s" % (BLOCK_BEGIN, block.strip("\n"), BLOCK_END)
        if not p.exists():
            self._write(p, new_file.replace("{{sdlc_block}}", wrapped), False)
            self.rows.append(("created", self.rel(p), ""))
            return
        cur = p.read_text(encoding="utf-8")
        if BLOCK_RE.search(cur):
            new = BLOCK_RE.sub(lambda _m: wrapped, cur, count=1)
        else:
            new = cur.rstrip("\n") + "\n\n" + wrapped + "\n"
        if new == cur:
            self.rows.append(("unchanged", self.rel(p), ""))
            return
        self._write(p, new, False)
        self.rows.append(("updated", self.rel(p), "managed block"))

    def note(self, status: str, what: str, detail: str = "") -> None:
        self.rows.append((status, what, detail))


def _detect_base_branch(root: Path) -> str:
    ref = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if ref.startswith("origin/"):
        return ref[len("origin/") :]
    cur = git(root, "branch", "--show-current", check=False).strip()
    if cur in ("main", "master", "develop", "trunk"):
        return cur
    default = git(root, "config", "init.defaultBranch", check=False).strip()
    return default or cur or "main"


def _ensure_claude_import(text: str) -> str:
    if re.search(r"^@AGENTS\.md\s*$", text, re.M):
        return text
    return "@AGENTS.md\n\n" + text


def _merge_claude_settings(plan: Plan, path: Path) -> None:
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            plan.note("skipped", plan.rel(path), "not valid JSON; add the sdlc marketplace manually (see README)")
            return
    before = json.dumps(data, sort_keys=True)
    mk = data.setdefault("extraKnownMarketplaces", {})
    mk.setdefault(MARKETPLACE, {"source": {"source": "github", "repo": KIT_REPO}})
    data.setdefault("enabledPlugins", {}).setdefault(PLUGIN_ID, True)
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    for sub in ("status", "new-change", "add", "use", "verify", "check", "doctor", "review", "test-lock on", "test-lock status", "config get"):
        rule = "Bash(python3 .sdlc/bin/sdlc.py %s:*)" % sub
        if rule not in allow:
            allow.append(rule)
    deny = perms.setdefault("deny", [])
    for rule in ("Read(./.env)", "Read(./.env.*)"):
        if rule not in deny:
            deny.append(rule)
    if json.dumps(data, sort_keys=True) == before:
        plan.note("unchanged", plan.rel(path), "")
        return
    status = "updated" if path.exists() else "created"
    plan._write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", False)
    plan.note(status, plan.rel(path), "marketplace, plugin, sdlc.py permissions")


def _setup_hooks_path(plan: Plan, root: Path, no_hooks_path: bool) -> None:
    want = ".sdlc/githooks"
    cur = git(root, "config", "--local", "core.hooksPath", check=False).strip()
    if cur == want:
        plan.note("unchanged", "git config core.hooksPath", want)
        return
    if no_hooks_path:
        plan.note("skipped", "git config core.hooksPath", "--no-hooks-path given; call `python3 .sdlc/bin/sdlc.py check --staged` / `check --pre-push` from your hook manager")
        return
    if cur:
        plan.note("skipped", "git config core.hooksPath", "already set to '%s'; call sdlc.py check from those hooks" % cur)
        return
    managers = [n for n in ("lefthook.yml", "lefthook.yaml", ".lefthook.yml", ".husky", ".pre-commit-config.yaml") if (root / n).exists()]
    if managers:
        plan.note("skipped", "git config core.hooksPath", "%s found; add `python3 .sdlc/bin/sdlc.py check --staged` (pre-commit) and `check --pre-push` (pre-push) there" % ", ".join(managers))
        return
    hooks_dir = Path(git(root, "rev-parse", "--git-path", "hooks").strip())
    if not hooks_dir.is_absolute():
        hooks_dir = root / hooks_dir
    existing = [p.name for p in hooks_dir.glob("*") if p.is_file() and not p.name.endswith(".sample")] if hooks_dir.is_dir() else []
    if existing:
        plan.note("skipped", "git config core.hooksPath", "existing hooks in %s (%s) would be disabled; merge them first" % (hooks_dir, ", ".join(existing)))
        return
    if not plan.dry:
        git(root, "config", "--local", "core.hooksPath", want)
    plan.note("updated", "git config core.hooksPath", want)


def cmd_init(a) -> int:
    if not is_plugin_copy():
        raise SdlcError("init must run from the plugin copy of sdlc.py (…/plugins/sdlc/bin/sdlc.py), not the vendored one")
    root = find_root()
    t = plugin_templates()
    plan = Plan(root, a.dry_run)

    raw = read_raw_config(root)
    if not raw:
        cfg_data: Dict[str, object] = {"$comment": "sdlc-kit repository config. Change it with `python3 .sdlc/bin/sdlc.py config set <key> <json>`; keys are documented in the sdlc-kit README."}
        cfg_data.update(json.loads(json.dumps(DEFAULT_CONFIG)))
        cfg_data["timezone"] = a.timezone or detect_local_tz()
        cfg_data["base_branch"] = _detect_base_branch(root)
        if a.language:
            cfg_data["language"] = a.language
        if not a.dry_run:
            write_raw_config(root, cfg_data)
        plan.note("created", ".sdlc/config.json", "timezone=%s base_branch=%s" % (cfg_data["timezone"], cfg_data["base_branch"]))
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg.update({k: v for k, v in cfg_data.items() if not k.startswith("$")})
    else:
        cfg = load_config(root)
        assert cfg is not None
        if vtuple(raw.get("kit_min_version", "0")) < vtuple(KIT_VERSION) and not a.dry_run:
            raw["kit_min_version"] = KIT_VERSION
            write_raw_config(root, raw)
            plan.note("updated", ".sdlc/config.json", "kit_min_version → %s" % KIT_VERSION)
        else:
            plan.note("unchanged", ".sdlc/config.json", "")

    ctx = {
        "kit_version": KIT_VERSION,
        "kit_repo": KIT_REPO,
        "plugin_id": PLUGIN_ID,
        "language_name": LANGUAGES.get(str(cfg.get("language")), str(cfg.get("language"))),
        "changes_dir": cfg["changes_dir"],
        "base_branch": cfg.get("base_branch", "main"),
        "repo_name": root.name,
    }

    def tpl(name: str) -> str:
        return render((t / "repo" / name).read_text(encoding="utf-8"), ctx)

    plan.managed(root / ".sdlc" / "bin" / "sdlc.py", Path(__file__).read_text(encoding="utf-8"), executable=True)
    for kind in ("intent", "spec", "plan", "review"):
        plan.create(root / ".sdlc" / "templates" / (kind + ".md"), (t / "changes" / (kind + ".md")).read_text(encoding="utf-8"))
    plan.managed(root / ".sdlc" / ".gitignore", "# %s\nstate/\n" % MANAGED_MARK)
    plan.managed(root / ".sdlc" / "githooks" / "pre-commit", tpl("githooks/pre-commit"), executable=True)
    plan.managed(root / ".sdlc" / "githooks" / "pre-push", tpl("githooks/pre-push"), executable=True)
    _setup_hooks_path(plan, root, a.no_hooks_path)
    plan.create(root / cfg["changes_dir"] / "README.md", tpl("changes-README.md"))
    plan.block(root / "AGENTS.md", tpl("agents-block.md"), tpl("AGENTS.md"))
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        cur = claude_md.read_text(encoding="utf-8")
        with_import = _ensure_claude_import(cur)
        if with_import != cur:
            plan._write(claude_md, with_import, False)
            plan.note("updated", "CLAUDE.md", "added @AGENTS.md import")
    plan.block(claude_md, tpl("claude-block.md"), tpl("CLAUDE.md"))
    plan.create(root / "REVIEW.md", tpl("REVIEW.md"))
    plan.managed(root / ".github" / "workflows" / "sdlc-gate.yml", tpl("workflows/sdlc-gate.yml"))
    plan.create(root / ".github" / "workflows" / "sdlc-verify.yml", tpl("workflows/sdlc-verify.yml"))
    plan.create(root / ".github" / "pull_request_template.md", tpl("pull_request_template.md"))
    _merge_claude_settings(plan, root / ".claude" / "settings.json")

    say("sdlc-kit %s init%s in %s" % (KIT_VERSION, " (dry run)" if a.dry_run else "", root))
    w = max(len(r[1]) for r in plan.rows)
    for status, what, detail in plan.rows:
        say("  %-9s %-*s %s" % (status, w, what, detail))
    if not a.dry_run:
        say("")
        say("next (sdlc-init skill, phase 2): set verify / plan_required_paths / test_globs / protected_paths with")
        say("  `python3 .sdlc/bin/sdlc.py config set <key> <json>`, fill the project section of AGENTS.md,")
        say("  then run `python3 .sdlc/bin/sdlc.py doctor --run-verify`.")
    return 0


# --------------------------------------------------------------------------- doctor


def _cli_version(exe: str) -> Optional[str]:
    path = shutil.which(exe)
    if not path:
        return None
    try:
        p = _run([path, "--version"], timeout=20)
    except Exception:
        return "?"
    m = re.search(r"\d+\.\d+\.\d+", p.stdout + p.stderr)
    return m.group(0) if m else "?"


def cmd_doctor(a) -> int:
    rows: List[Tuple[str, str, str]] = []

    def add(level: str, name: str, detail: str = "") -> None:
        rows.append((level, name, detail))

    add("ok" if sys.version_info >= (3, 9) else "fail", "python", "%d.%d" % sys.version_info[:2])
    try:
        root = find_root()
    except SdlcError as e:
        add("fail", "git repository", str(e))
        return _print_doctor(rows)
    cfg = None
    try:
        cfg = load_config(root)
        add("ok", ".sdlc/config.json")
    except SdlcError as e:
        add("fail", ".sdlc/config.json", str(e))
    if cfg is None:
        return _print_doctor(rows)

    add("ok" if str(cfg.get("verify") or "").strip() else "fail", "config: verify", str(cfg.get("verify") or "(empty)"))
    add("ok" if cfg.get("plan_required_paths") else "warn", "config: plan_required_paths", ", ".join(cfg.get("plan_required_paths") or []) or "empty → plan gate disabled")
    add("ok" if cfg.get("test_globs") else "warn", "config: test_globs", "%d pattern(s)" % len(cfg.get("test_globs") or []))

    vend = vendored_version(root)
    if not vend:
        add("fail", ".sdlc/bin/sdlc.py", "missing — run sdlc-init")
    else:
        level, detail = "ok", vend
        if is_plugin_copy() and vtuple(vend) < vtuple(KIT_VERSION):
            level, detail = "warn", "%s (plugin is %s) — run sdlc-init to update" % (vend, KIT_VERSION)
        if vtuple(vend) < vtuple(cfg.get("kit_min_version", "0")):
            level, detail = "fail", "%s < kit_min_version %s" % (vend, cfg.get("kit_min_version"))
        add(level, ".sdlc/bin/sdlc.py", detail)
    missing_t = [k for k in ("intent", "spec", "plan", "review") if not (root / ".sdlc" / "templates" / (k + ".md")).is_file()]
    add("warn" if missing_t else "ok", ".sdlc/templates", "missing: " + ", ".join(missing_t) if missing_t else "")
    gi = root / ".sdlc" / ".gitignore"
    add("ok" if gi.is_file() and "state/" in gi.read_text(encoding="utf-8") else "warn", ".sdlc/.gitignore", "keeps .sdlc/state/ out of git")
    add("ok" if changes_root(root, cfg).is_dir() else "warn", cfg["changes_dir"])
    lint_errs, n = lint_changes(root, cfg, None)
    add("fail" if lint_errs else "ok", "artifacts lint", "; ".join(lint_errs[:3]) if lint_errs else "%d change(s)" % n)

    agents = root / "AGENTS.md"
    add("ok" if agents.is_file() and BLOCK_RE.search(agents.read_text(encoding="utf-8")) else "fail", "AGENTS.md sdlc block")
    claude_md = root / "CLAUDE.md"
    add("ok" if claude_md.is_file() and re.search(r"^@AGENTS\.md\s*$", claude_md.read_text(encoding="utf-8"), re.M) else "warn", "CLAUDE.md imports @AGENTS.md")
    add("ok" if (root / "REVIEW.md").is_file() else "warn", "REVIEW.md")

    hp = git(root, "config", "--local", "core.hooksPath", check=False).strip()
    if hp == ".sdlc/githooks":
        bad = [n for n in ("pre-commit", "pre-push") if not os.access(str(root / ".sdlc" / "githooks" / n), os.X_OK)]
        add("fail" if bad else "ok", "git hooks", "not executable: " + ", ".join(bad) if bad else "core.hooksPath=.sdlc/githooks")
    else:
        wired = False
        for n in ("lefthook.yml", "lefthook.yaml", ".lefthook.yml", ".pre-commit-config.yaml"):
            f = root / n
            if f.is_file() and "sdlc.py check" in f.read_text(encoding="utf-8"):
                wired = True
        husky = root / ".husky"
        if husky.is_dir() and any("sdlc.py check" in p.read_text(encoding="utf-8", errors="ignore") for p in husky.glob("*") if p.is_file()):
            wired = True
        add("ok" if wired else "fail", "git hooks", "wired through hook manager" if wired else "core.hooksPath is '%s' and no hook manager calls sdlc.py check" % (hp or "unset"))
    add("ok" if (root / ".github" / "workflows" / "sdlc-gate.yml").is_file() else "warn", "CI gate workflow", ".github/workflows/sdlc-gate.yml")

    settings = root / ".claude" / "settings.json"
    try:
        sdata = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    except ValueError:
        sdata = {}
    declared = MARKETPLACE in (sdata.get("extraKnownMarketplaces") or {}) and (sdata.get("enabledPlugins") or {}).get(PLUGIN_ID)
    add("ok" if declared else "warn", "Claude Code: plugin declared in .claude/settings.json", PLUGIN_ID)

    mins = cfg.get("tool_min_versions") or {}
    clients = 0
    for exe in ("claude", "codex"):
        v = _cli_version(exe)
        if v is None:
            add("info", "%s CLI" % exe, "not installed")
            continue
        clients += 1
        need = mins.get(exe)
        level = "ok"
        if need and v != "?" and vtuple(v) < vtuple(need):
            level = "warn"
        add(level, "%s CLI" % exe, v + (" (min %s)" % need if need else ""))
    if clients == 0:
        add("warn", "agent CLIs", "neither claude nor codex found on PATH")
    if shutil.which("codex"):
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        ctoml = home / "config.toml"
        txt = ctoml.read_text(encoding="utf-8") if ctoml.is_file() else ""
        enabled = ('[plugins."%s"]' % PLUGIN_ID) in txt
        add("ok" if enabled else "warn", "Codex: plugin installed", PLUGIN_ID if enabled else "run: codex plugin marketplace add %s && codex plugin add %s" % (KIT_REPO, PLUGIN_ID))
        add("info", "Codex: hook trust", "open /hooks once per machine (and after each plugin update) and trust the sdlc hooks")
    if shutil.which("claude") and shutil.which("codex"):
        add("ok", "cross-agent review", "claude ↔ codex both available")
    else:
        add("warn", "cross-agent review", "install both claude and codex so each can review the other's work")

    if a.run_verify:
        say("[sdlc] running verify…")
        rc = subprocess.run(str(cfg.get("verify")), shell=True, cwd=str(root)).returncode
        add("ok" if rc == 0 else "fail", "verify", "exit %d" % rc)
    return _print_doctor(rows)


def _print_doctor(rows: List[Tuple[str, str, str]]) -> int:
    icon = {"ok": "✓", "warn": "!", "fail": "✗", "info": "i"}
    w = max(len(r[1]) for r in rows)
    say("sdlc-kit %s doctor" % KIT_VERSION)
    for level, name, detail in rows:
        say("  %s %-*s %s" % (icon[level], w, name, detail))
    fails = sum(1 for r in rows if r[0] == "fail")
    warns = sum(1 for r in rows if r[0] == "warn")
    say("%d failure(s), %d warning(s)" % (fails, warns))
    return 1 if fails else 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sdlc.py", description="sdlc-kit CLI (intent → spec → plan → build → review)")
    ap.add_argument("--version", action="version", version="sdlc-kit " + KIT_VERSION)
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")
    sub.required = True

    p = sub.add_parser("init", help="scaffold or update sdlc-kit files in this repo (run from the plugin)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timezone", help="IANA time zone for change ids (default: system)")
    p.add_argument("--language", help="artifact/review language code (default: ja)")
    p.add_argument("--no-hooks-path", action="store_true", help="do not set git core.hooksPath")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-change", help="create docs/changes/<date>-<slug>/intent.md")
    p.add_argument("slug")
    p.add_argument("--title", default="")
    p.add_argument("--source", default="idea", help="|".join(SOURCES))
    p.set_defaults(func=cmd_new_change)

    p = sub.add_parser("add", help="add spec.md / plan.md / review.md to a change")
    p.add_argument("id", nargs="?", default="current")
    p.add_argument("artifact", choices=["spec", "plan", "review"])
    p.add_argument("--body-file")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="frontmatter value (repeatable)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("set-status", help="set an artifact status (decisions are blocked for agents)")
    p.add_argument("id")
    p.add_argument("artifact", choices=list(ARTIFACT_STATUSES))
    p.add_argument("status")
    p.add_argument("--spec-skipped", help="plan only: reason for skipping spec.md")
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("approve", help="approve the next draft artifact of a change (human only)")
    p.add_argument("id", nargs="?", default="current")
    p.add_argument("--reject", action="store_true")
    p.add_argument("--spec-skipped", help="when approving plan.md without spec.md: the reason")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("use", help="set the current change")
    p.add_argument("id")
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("status", help="list changes and artifact statuses")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="lint artifacts and run the gates (git hooks / CI)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="pre-commit: lint + scan staged changes for secrets")
    g.add_argument("--pre-push", action="store_true", help="pre-push: read refs from stdin and gate each range")
    g.add_argument("--base", help="CI: gate the range <base>...<head>")
    p.add_argument("--head", default="HEAD")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("review", help="independent review of a change by the other agent CLI")
    p.add_argument("id", nargs="?", default="current")
    p.add_argument("--reviewer", default="auto", choices=["auto", "codex", "claude"])
    p.add_argument("--base")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--prompt-only", action="store_true", help="print the review prompt and diff path only")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("verify", help="run the configured verify command")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("test-lock", help="lock/unlock edits to tests during a bug fix")
    p.add_argument("state", choices=["on", "off", "status"])
    p.set_defaults(func=cmd_test_lock)

    p = sub.add_parser("config", help="read or change .sdlc/config.json")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("doctor", help="check that the setup actually works")
    p.add_argument("--run-verify", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("hook", help="agent hook entry point (reads the tool-call JSON on stdin)")
    p.add_argument("event", choices=["pre-tool-use", "post-tool-use", "session-start"])
    p.set_defaults(func=cmd_hook)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SdlcError as e:
        warn("sdlc: error: %s" % e)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
