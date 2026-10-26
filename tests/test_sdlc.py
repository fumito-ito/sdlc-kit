"""Tests for plugins/sdlc/bin/sdlc.py (stdlib unittest; Python 3.9+).

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "sdlc"
KIT = PLUGIN / "bin" / "sdlc.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("sdlc", KIT)
sdlc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(sdlc)

# Secret-looking strings are assembled at runtime so this file never contains one.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GH = "gh" + "p_" + "a1B2" * 9


def base_env(home: Path) -> dict:
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("GIT_", "CLAUDE", "CODEX_")):
            env.pop(k)
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com",
            "SDLC_TODAY": "2026-10-26",
        }
    )
    return env


class GlobTests(unittest.TestCase):
    def test_patterns(self):
        m = sdlc.path_matches
        self.assertTrue(m("src/app.py", ["src/**"]))
        self.assertFalse(m("lib/src/app.py", ["src/**"]))  # pattern with a slash is anchored
        self.assertTrue(m("a/b/Generated/X.swift", ["**/Generated/**"]))
        self.assertTrue(m("Generated/X.swift", ["**/Generated/**"]))
        self.assertTrue(m("pkg/foo.generated.ts", ["**/*.generated.*"]))
        self.assertTrue(m("deep/dir/Package.resolved", ["**/Package.resolved"]))
        self.assertTrue(m("Package.resolved", ["Package.resolved"]))
        self.assertTrue(m("x/vendor/y.c", ["vendor"]))  # no slash → any level, parent dirs count
        self.assertTrue(m("Tests/FooTests.swift", ["**/*Tests.swift"]))
        self.assertTrue(m("app/tests/test_a.py", ["**/tests/**"]))
        self.assertFalse(m("app/testsuite.py", ["**/tests/**"]))
        self.assertTrue(m("src/a.py", ["/src/**"]))
        self.assertTrue(m("docs/a1.md", ["docs/a[0-9].md"]))
        self.assertIsNone(m("README.md", ["src/**", "**/Generated/**"]))


class NormalizeTests(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_claude_edit(self):
        info = sdlc.normalize_tool_call(self.load("claude_edit.json"))
        self.assertEqual(info["kind"], "edit")
        self.assertEqual(info["paths"], ["/repo/src/app.py"])
        self.assertIn("return 42", info["added"])

    def test_claude_write_and_multiedit(self):
        w = sdlc.normalize_tool_call(self.load("claude_write.json"))
        self.assertEqual(w["paths"], ["/repo/README.md"])
        self.assertEqual(w["added"], ["# hello", "world"])
        me = sdlc.normalize_tool_call(self.load("claude_multiedit.json"))
        self.assertEqual(me["added"], ["one", "two"])

    def test_claude_bash_and_read(self):
        b = sdlc.normalize_tool_call(self.load("claude_bash.json"))
        self.assertEqual((b["kind"], b["command"]), ("bash", "git status"))
        r = sdlc.normalize_tool_call(self.load("claude_read.json"))
        self.assertEqual((r["kind"], r["paths"]), ("read", ["/repo/.env"]))

    def test_codex_apply_patch(self):
        info = sdlc.normalize_tool_call(self.load("codex_apply_patch.json"))
        self.assertEqual(info["kind"], "edit")
        self.assertEqual(info["paths"], ["src/app.py", "src/new_file.py", "src/old.py", "src/renamed.py"])
        self.assertIn("print('new')", info["added"])
        self.assertIn("    return 42", info["added"])

    def test_codex_bash(self):
        info = sdlc.normalize_tool_call(self.load("codex_bash.json"))
        self.assertEqual((info["kind"], info["command"]), ("bash", "ls -la"))

    def test_bad_payloads_do_not_crash(self):
        for payload in ({}, {"tool_name": "Edit", "tool_input": None}, {"tool_name": "apply_patch", "tool_input": "*** Begin Patch\n*** End Patch"}):
            sdlc.normalize_tool_call(payload)


class FrontmatterTests(unittest.TestCase):
    def test_roundtrip(self):
        text = "---\nid: x\nstatus: draft\nspec_skipped: \"\"\n---\n# T\n\nbody\n"
        fm, body = sdlc.parse_frontmatter(text)
        self.assertEqual(fm, {"id": "x", "status": "draft", "spec_skipped": ""})
        self.assertEqual(sdlc.render_frontmatter(fm, body), text)

    def test_quotes_when_needed(self):
        out = sdlc.render_frontmatter({"reason": "too small: typo"}, "")
        self.assertIn('reason: "too small: typo"', out)


class RepoTestCase(unittest.TestCase):
    """A fresh git repository (with a bare origin) initialized with sdlc-kit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sdlc-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = base_env(self.home)
        self.origin = self.tmp / "origin.git"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Tester")
        self.git("config", "user.email", "tester@example.com")
        self.git("remote", "add", "origin", str(self.origin))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers -------------------------------------------------------------
    def run_cmd(self, args, cwd=None, input=None, env=None):
        return subprocess.run(args, cwd=str(cwd or self.repo), input=input, text=True, capture_output=True, env=env or self.env)

    def git(self, *args, cwd=None, check=True):
        p = self.run_cmd(["git", *args], cwd=cwd)
        if check and p.returncode != 0:
            raise AssertionError("git %s failed: %s%s" % (args, p.stdout, p.stderr))
        return p

    def kit(self, *args, input=None):
        return self.run_cmd([sys.executable, str(KIT), *args], input=input)

    def sdlc(self, *args, input=None, env=None):
        return self.run_cmd([sys.executable, str(self.repo / ".sdlc" / "bin" / "sdlc.py"), *args], input=input, env=env)

    def ok(self, p):
        self.assertEqual(p.returncode, 0, "exit %d\nstdout:\n%s\nstderr:\n%s" % (p.returncode, p.stdout, p.stderr))
        return p

    def write(self, rel, content):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def init_kit(self, *extra):
        p = self.ok(self.kit("init", "--timezone", "Asia/Tokyo", *extra))
        self.ok(self.sdlc("config", "set", "verify", '"true"'))
        return p

    def commit_all(self, msg, *trailers):
        self.git("add", "-A")
        args = ["commit", "-q", "-m", msg]
        for t in trailers:
            args += ["-m", t]
        return self.git(*args)

    def bootstrap(self):
        """init + initial commit pushed to origin/main."""
        self.init_kit()
        self.commit_all("chore: sdlc-kit scaffold", "SDLC-Exempt: bootstrap")
        self.ok(self.git("push", "-q", "-u", "origin", "main"))

    def hook(self, event, payload):
        return self.run_cmd([sys.executable, str(KIT), "hook", event], input=json.dumps(payload))

    def change_with_approved_plan(self, slug="feature-x", spec_skipped="small change"):
        self.ok(self.sdlc("new-change", slug, "--title", "機能X"))
        cid = "2026-10-26-" + slug
        self.ok(self.sdlc("approve", cid))
        self.ok(self.sdlc("add", cid, "plan"))
        self.ok(self.sdlc("approve", cid, "--spec-skipped", spec_skipped))
        return cid


class InitTests(RepoTestCase):
    def test_creates_scaffold_and_is_idempotent(self):
        self.init_kit()
        for rel in (
            ".sdlc/config.json",
            ".sdlc/bin/sdlc.py",
            ".sdlc/templates/plan.md",
            ".sdlc/githooks/pre-push",
            "docs/changes/README.md",
            "AGENTS.md",
            "CLAUDE.md",
            "REVIEW.md",
            ".github/workflows/sdlc-gate.yml",
            ".github/workflows/sdlc-verify.yml",
            ".claude/settings.json",
        ):
            self.assertTrue((self.repo / rel).exists(), rel)
        self.assertTrue(os.access(str(self.repo / ".sdlc/githooks/pre-push"), os.X_OK))
        self.assertEqual(self.git("config", "core.hooksPath").stdout.strip(), ".sdlc/githooks")
        cfg = json.loads((self.repo / ".sdlc/config.json").read_text())
        self.assertEqual(cfg["timezone"], "Asia/Tokyo")
        self.assertEqual(cfg["base_branch"], "main")
        settings = json.loads((self.repo / ".claude/settings.json").read_text())
        self.assertEqual(settings["extraKnownMarketplaces"]["sdlc-kit"]["source"], {"source": "github", "repo": sdlc.KIT_REPO})
        self.assertTrue(settings["enabledPlugins"]["sdlc@sdlc-kit"])

        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts}
        second = self.ok(self.kit("init"))
        self.assertNotIn("created", second.stdout)
        self.assertNotIn("updated", second.stdout)
        after = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.assertEqual(before, after)

    def test_dry_run_writes_nothing(self):
        p = self.ok(self.kit("init", "--dry-run"))
        self.assertIn("created", p.stdout)
        self.assertEqual([x for x in self.repo.iterdir() if x.name != ".git"], [])

    def test_preserves_user_content_and_refreshes_block(self):
        self.write("AGENTS.md", "# My project\n\nKeep this line.\n")
        self.write("CLAUDE.md", "Claude-only note.\n")
        self.init_kit()
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Keep this line.", agents)
        self.assertIn("sdlc-kit:begin", agents)
        claude = (self.repo / "CLAUDE.md").read_text()
        self.assertTrue(claude.startswith("@AGENTS.md"))
        self.assertIn("Claude-only note.", claude)
        # Edits inside the managed block are restored, edits outside survive.
        tampered = agents.replace("No implementation before an approved plan.", "anything goes").replace("Keep this line.", "Keep this line, edited.")
        (self.repo / "AGENTS.md").write_text(tampered)
        self.ok(self.kit("init"))
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("No implementation before an approved plan.", agents)
        self.assertIn("Keep this line, edited.", agents)
        self.assertEqual(agents.count("sdlc-kit:begin"), 1)

    def test_leaves_user_owned_files_and_hook_managers_alone(self):
        self.write(".github/workflows/sdlc-gate.yml", "name: mine\n")
        self.write("lefthook.yml", "pre-commit:\n  commands: {}\n")
        p = self.init_kit()
        self.assertEqual((self.repo / ".github/workflows/sdlc-gate.yml").read_text(), "name: mine\n")
        self.assertIn("skipped", p.stdout)
        self.assertEqual(self.git("config", "core.hooksPath", check=False).stdout.strip(), "")

    def test_merges_existing_claude_settings(self):
        self.write(".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(make test)"]}, "model": "opus"}))
        self.init_kit()
        s = json.loads((self.repo / ".claude/settings.json").read_text())
        self.assertEqual(s["model"], "opus")
        self.assertIn("Bash(make test)", s["permissions"]["allow"])
        self.assertIn("sdlc@sdlc-kit", s["enabledPlugins"])

    def test_vendored_copy_refuses_init(self):
        self.init_kit()
        p = self.sdlc("init")
        self.assertEqual(p.returncode, 1)
        self.assertIn("plugin copy", p.stderr)


class ArtifactTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.init_kit()

    def test_new_change_naming(self):
        p = self.ok(self.sdlc("new-change", "Claims Status!", "--title", "請求ステータス"))
        self.assertIn("2026-10-26-claims-status", p.stdout)
        intent = (self.repo / "docs/changes/2026-10-26-claims-status/intent.md").read_text()
        fm, body = sdlc.parse_frontmatter(intent)
        self.assertEqual(fm["status"], "draft")
        self.assertEqual(fm["kit"], "sdlc@" + sdlc.KIT_VERSION)
        self.assertIn("# Intent: 請求ステータス", body)
        self.ok(self.sdlc("new-change", "claims-status"))
        self.assertTrue((self.repo / "docs/changes/2026-10-26-claims-status-2/intent.md").is_file())
        bad = self.sdlc("new-change", "請求")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("ASCII", bad.stderr)

    def test_approval_chain(self):
        self.ok(self.sdlc("new-change", "feature-x"))
        cid = "2026-10-26-feature-x"
        self.ok(self.sdlc("add", cid, "plan"))
        # plan cannot be approved before the intent
        p = self.sdlc("set-status", cid, "plan", "approved")
        self.assertEqual(p.returncode, 1)
        self.ok(self.sdlc("approve"))  # current change → intent accepted
        fm = sdlc.parse_frontmatter((self.repo / "docs/changes" / cid / "intent.md").read_text())[0]
        self.assertEqual((fm["status"], fm["decided_by"]), ("accepted", "Tester"))
        # without spec, a reason is required
        p = self.sdlc("approve")
        self.assertEqual(p.returncode, 1)
        self.assertIn("spec_skipped", p.stderr)
        self.ok(self.sdlc("approve", "--spec-skipped", "one-line fix"))
        status = json.loads(self.ok(self.sdlc("status", "--json")).stdout)
        row = status["changes"][0]
        self.assertEqual((row["intent"], row["spec"], row["plan"]), ("accepted", "skipped", "approved"))
        self.ok(self.sdlc("check"))

    def test_spec_flow_and_short_ids(self):
        self.ok(self.sdlc("new-change", "with-spec"))
        self.ok(self.sdlc("approve", "with-spec"))
        self.ok(self.sdlc("add", "with-spec", "spec"))
        self.ok(self.sdlc("add", "with-spec", "plan"))
        self.ok(self.sdlc("approve", "with-spec"))  # spec
        self.ok(self.sdlc("approve", "with-spec"))  # plan
        self.assertIn("approved", self.ok(self.sdlc("status")).stdout)
        dup = self.sdlc("add", "with-spec", "plan")
        self.assertEqual(dup.returncode, 1)

    def test_lint_catches_hand_edits(self):
        self.ok(self.sdlc("new-change", "lint-me"))
        path = self.repo / "docs/changes/2026-10-26-lint-me/intent.md"
        path.write_text(path.read_text().replace("status: draft", "status: done"))
        p = self.sdlc("check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("status 'done'", p.stderr)
        self.write("docs/changes/Bad_Dir/intent.md", "no frontmatter")
        p = self.sdlc("check")
        self.assertIn("directory name", p.stderr)
        self.assertIn("missing frontmatter", p.stderr)

    def test_config_set_and_test_lock(self):
        self.ok(self.sdlc("config", "set", "plan_required_paths", '["Sources/**"]'))
        self.assertEqual(json.loads(self.ok(self.sdlc("config", "get", "plan_required_paths")).stdout), ["Sources/**"])
        self.ok(self.sdlc("test-lock", "on"))
        self.assertIn("LOCKED", self.ok(self.sdlc("test-lock", "status")).stdout)
        self.ok(self.sdlc("test-lock", "off"))
        self.assertIn("unlocked", self.ok(self.sdlc("test-lock", "status")).stdout)

    def test_verify_records_result(self):
        self.ok(self.sdlc("verify"))
        last = json.loads((self.repo / ".sdlc/state/last-verify.json").read_text())
        self.assertEqual(last["exit_code"], 0)
        self.ok(self.sdlc("config", "set", "verify", '"exit 3"'))
        self.assertEqual(self.sdlc("verify").returncode, 3)

    def test_doctor(self):
        p = self.ok(self.sdlc("doctor", "--run-verify"))
        self.assertIn("0 failure(s)", p.stdout)
        (self.repo / ".sdlc/githooks/pre-push").chmod(0o644)
        p = self.sdlc("doctor")
        self.assertEqual(p.returncode, 1)
        self.assertIn("not executable", p.stdout)


class HookTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.init_kit()

    def payload(self, tool, tool_input, cwd=None):
        return {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(cwd or self.repo), "tool_name": tool, "tool_input": tool_input}

    def assertBlocked(self, p, text):
        self.assertEqual(p.returncode, 2, "expected block; stderr=%s" % p.stderr)
        self.assertIn(text, p.stderr)

    def assertAllowed(self, p):
        self.assertEqual(p.returncode, 0, "unexpected block: %s" % p.stderr)

    def test_normal_edits_are_allowed(self):
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(self.repo / "src/app.py"), "old_string": "a", "new_string": "b"})))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Bash", {"command": "git commit -m 'fix -n flag'"})))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Read", {"file_path": str(self.repo / ".env.example")})))

    def test_protected_paths_claude_and_codex(self):
        p = self.hook("pre-tool-use", self.payload("Write", {"file_path": str(self.repo / "App/Generated/Api.swift"), "content": "x"}))
        self.assertBlocked(p, "protected")
        patch = "*** Begin Patch\n*** Update File: App/Generated/Api.swift\n@@\n-a\n+b\n*** End Patch\n"
        self.assertBlocked(self.hook("pre-tool-use", self.payload("apply_patch", {"command": patch})), "protected")
        self.assertBlocked(self.hook("pre-tool-use", self.payload("Edit", {"file_path": ".sdlc/bin/sdlc.py", "old_string": "a", "new_string": "b"})), "protected")
        # relative paths resolve against the session cwd (Codex may start in a subdirectory)
        (self.repo / "App").mkdir()
        patch2 = "*** Begin Patch\n*** Add File: Generated/X.swift\n+x\n*** End Patch\n"
        self.assertBlocked(self.hook("pre-tool-use", self.payload("apply_patch", {"command": patch2}, cwd=self.repo / "App")), "App/Generated/X.swift")

    def test_secrets_and_env(self):
        p = self.hook("pre-tool-use", self.payload("Write", {"file_path": str(self.repo / "src/config.py"), "content": "KEY = '%s'\n" % FAKE_AWS}))
        self.assertBlocked(p, "AWS access key id")
        self.assertNotIn(FAKE_AWS, p.stderr)
        patch = "*** Begin Patch\n*** Add File: src/t.py\n+TOKEN = '%s'\n*** End Patch\n" % FAKE_GH
        self.assertBlocked(self.hook("pre-tool-use", self.payload("apply_patch", {"command": patch})), "GitHub token")
        ok = "KEY = '%s'  # sdlc:allow-secret (documented test fixture)\n" % FAKE_AWS
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Write", {"file_path": str(self.repo / "src/c.py"), "content": ok})))
        self.assertBlocked(self.hook("pre-tool-use", self.payload("Read", {"file_path": str(self.repo / ".env")})), "secrets")
        self.assertBlocked(self.hook("pre-tool-use", self.payload("Read", {"file_path": str(self.repo / "app/.env.production")})), "secrets")

    def test_git_hook_bypass(self):
        for cmd in ("git commit --no-verify -m x", "git push --no-verify", "git commit -n -m x", "git config core.hooksPath /dev/null"):
            self.assertEqual(self.hook("pre-tool-use", self.payload("Bash", {"command": cmd})).returncode, 2, cmd)

    def test_approvals_are_human_only(self):
        for cmd in (
            "python3 .sdlc/bin/sdlc.py approve",
            "python3 .sdlc/bin/sdlc.py approve 2026-10-26-x --spec-skipped tiny",
            "python3 .sdlc/bin/sdlc.py set-status x plan approved",
            "python3 .sdlc/bin/sdlc.py test-lock off",
            "rm -f .sdlc/state/test-lock",
        ):
            self.assertEqual(self.hook("pre-tool-use", self.payload("Bash", {"command": cmd})).returncode, 2, cmd)
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Bash", {"command": "python3 .sdlc/bin/sdlc.py set-status x review resolved"})))
        self.ok(self.sdlc("new-change", "x"))
        intent = self.repo / "docs/changes/2026-10-26-x/intent.md"
        self.assertBlocked(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(intent), "old_string": "status: draft", "new_string": "status: accepted"})), "user's decision")
        patch = "*** Begin Patch\n*** Update File: docs/changes/2026-10-26-x/intent.md\n@@\n-status: draft\n+status: accepted\n*** End Patch\n"
        self.assertBlocked(self.hook("pre-tool-use", self.payload("apply_patch", {"command": patch})), "user's decision")
        # editing the body of an already accepted intent is fine
        self.ok(self.sdlc("approve"))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(intent), "old_string": "status: accepted\nsource", "new_string": "status: accepted\nsource"})))

    def test_test_lock(self):
        self.ok(self.sdlc("test-lock", "on"))
        self.assertBlocked(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(self.repo / "tests/test_app.py"), "old_string": "a", "new_string": "b"})), "locked")
        patch = "*** Begin Patch\n*** Update File: Tests/AppTests/LoginTests.swift\n@@\n-a\n+b\n*** End Patch\n"
        self.assertBlocked(self.hook("pre-tool-use", self.payload("apply_patch", {"command": patch})), "locked")
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(self.repo / "src/app.py"), "old_string": "a", "new_string": "b"})))
        self.ok(self.sdlc("test-lock", "off"))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Edit", {"file_path": str(self.repo / "tests/test_app.py"), "old_string": "a", "new_string": "b"})))

    def test_uninitialized_or_outside_repo_is_allowed(self):
        other = self.tmp / "plain"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Write", {"file_path": str(other / "Generated/x.swift"), "content": FAKE_AWS}, cwd=other)))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Write", {"file_path": "/etc/hosts", "content": "x"})))
        self.assertAllowed(self.hook("pre-tool-use", self.payload("Write", {"file_path": str(self.tmp / "x.txt"), "content": "x"}, cwd=self.tmp)))

    def test_garbage_input_never_blocks(self):
        p = self.run_cmd([sys.executable, str(KIT), "hook", "pre-tool-use"], input="not json")
        self.assertEqual(p.returncode, 0)

    def test_session_start_and_format(self):
        self.ok(self.sdlc("new-change", "ctx"))
        self.ok(self.sdlc("test-lock", "on"))
        p = self.ok(self.hook("session-start", {"cwd": str(self.repo), "source": "startup"}))
        self.assertIn("sdlc-kit", p.stdout)
        self.assertIn("2026-10-26-ctx (intent: draft)", p.stdout)
        self.assertIn("LOCKED", p.stdout)
        other = self.tmp / "plain2"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        self.assertEqual(self.hook("session-start", {"cwd": str(other)}).stdout, "")
        # post-tool-use formatter receives the edited file
        self.ok(self.sdlc("config", "set", "format_command", '"echo formatted >> {file}"'))
        f = self.write("src/a.txt", "x\n")
        self.ok(self.hook("post-tool-use", self.payload("Edit", {"file_path": str(f), "old_string": "", "new_string": "x"})))
        self.assertIn("formatted", f.read_text())
        g = self.write("App/Generated/b.txt", "x\n")
        self.ok(self.hook("post-tool-use", self.payload("Write", {"file_path": str(g), "content": "x"})))
        self.assertNotIn("formatted", g.read_text())


class GateTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.bootstrap()
        self.git("checkout", "-q", "-b", "feature")

    def test_code_without_plan_is_rejected_locally_and_in_ci(self):
        self.write("src/app.py", "print('hi')\n")
        self.commit_all("feat: app")
        ci = self.sdlc("check", "--base", "origin/main")
        self.assertEqual(ci.returncode, 1)
        self.assertIn("plan gate", ci.stderr)
        push = self.git("push", "-q", "origin", "feature", check=False)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("plan gate", push.stderr)

    def test_change_with_approved_plan_passes(self):
        cid = self.change_with_approved_plan()
        self.write("src/app.py", "print('hi')\n")
        self.commit_all("feat: app", "SDLC-Change: " + cid)
        self.ok(self.sdlc("check", "--base", "origin/main"))
        self.ok(self.git("push", "-q", "origin", "feature"))

    def test_trailer_reference_and_exemption(self):
        cid = self.change_with_approved_plan("earlier")
        self.commit_all("plan: earlier", "SDLC-Change: " + cid)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--ff-only", "feature")
        self.ok(self.git("push", "-q", "origin", "main"))
        self.git("checkout", "-q", "-b", "impl")
        self.write("src/app.py", "print('impl')\n")
        self.commit_all("feat: implement", "SDLC-Change: " + cid)
        self.ok(self.sdlc("check", "--base", "origin/main"))
        self.git("checkout", "-q", "-b", "typo", "origin/main")
        self.write("src/typo.py", "x = 1\n")
        self.commit_all("fix: typo")
        self.assertEqual(self.sdlc("check", "--base", "origin/main").returncode, 1)
        self.git("commit", "-q", "--amend", "-m", "fix: typo", "-m", "SDLC-Exempt: comment typo only")
        p = self.ok(self.sdlc("check", "--base", "origin/main"))
        self.assertIn("SDLC-Exempt", p.stdout)

    def test_unapproved_plan_is_not_enough(self):
        self.ok(self.sdlc("new-change", "draft-only"))
        self.ok(self.sdlc("add", "draft-only", "plan"))
        self.write("src/app.py", "print('hi')\n")
        self.commit_all("feat: app")
        self.assertEqual(self.sdlc("check", "--base", "origin/main").returncode, 1)

    def test_docs_only_changes_pass(self):
        self.write("README.md", "# readme\n")
        self.commit_all("docs: readme")
        self.ok(self.sdlc("check", "--base", "origin/main"))

    def test_pre_commit_blocks_staged_secret(self):
        self.write("notes.txt", "token %s\n" % FAKE_GH)
        self.git("add", "notes.txt")
        p = self.git("commit", "-q", "-m", "notes", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("GitHub token", p.stderr)
        self.write("notes.txt", "token %s sdlc:allow-secret\n" % FAKE_GH)
        self.git("add", "notes.txt")
        self.ok(self.git("commit", "-q", "-m", "notes"))

    def test_ci_secret_scan_and_zero_base(self):
        self.write("cfg.txt", "key=%s\n" % FAKE_AWS)
        self.git("add", "cfg.txt")
        self.git("commit", "-q", "--no-verify", "-m", "oops")
        p = self.sdlc("check", "--base", "origin/main")
        self.assertEqual(p.returncode, 1)
        self.assertIn("AWS access key id", p.stderr)
        p = self.ok(self.sdlc("check", "--base", "0" * 40))
        self.assertIn("gate skipped", p.stdout)


class ReviewTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.bootstrap()
        self.git("checkout", "-q", "-b", "feature")
        self.cid = self.change_with_approved_plan("reviewed")
        self.write("src/app.py", "print('hi')\n")
        self.commit_all("feat: app", "SDLC-Change: " + self.cid)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.json"

    def fake_cli(self, name, script):
        path = self.bin / name
        path.write_text("#!%s\n%s" % (sys.executable, textwrap.dedent(script)))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        env = dict(self.env)
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        env["CALLS"] = str(self.calls)
        return env

    def test_prompt_only(self):
        p = self.ok(self.sdlc("review", self.cid, "--prompt-only"))
        self.assertIn(".sdlc/state/review/%s.diff" % self.cid, p.stdout)
        self.assertIn("docs/changes/%s/plan.md" % self.cid, p.stdout)
        self.assertIn("Verdict:", p.stdout)
        self.assertIn("Japanese", p.stdout)
        diff = (self.repo / (".sdlc/state/review/%s.diff" % self.cid)).read_text()
        self.assertIn("print('hi')", diff)

    def test_codex_reviewer(self):
        env = self.fake_cli(
            "codex",
            """
            import json, os, sys
            args = sys.argv[1:]
            json.dump(args, open(os.environ["CALLS"], "w"))
            out = args[args.index("-o") + 1]
            open(out, "w").write("## Summary\\nok\\n## Findings\\n- none\\n## Verdict\\nVerdict: no-important-findings\\n")
            """,
        )
        self.ok(self.sdlc("review", self.cid, "--reviewer", "codex", env=env))
        args = json.loads(self.calls.read_text())
        self.assertEqual(args[:3], ["exec", "--sandbox", "read-only"])
        fm, body = sdlc.parse_frontmatter((self.repo / "docs/changes" / self.cid / "review.md").read_text())
        self.assertEqual((fm["reviewer"], fm["verdict"], fm["status"], fm["dirty"]), ("codex", "no-important-findings", "open", "false"))
        self.assertIn("## Findings", body)
        self.ok(self.sdlc("check", "--base", "origin/main"))

    def test_claude_reviewer_second_round_and_auto(self):
        env = self.fake_cli(
            "claude",
            """
            import json, os, sys
            json.dump(sys.argv[1:], open(os.environ["CALLS"], "w"))
            print("## Summary\\nx\\n## Findings\\n- [Important] (Bugs) src/app.py:1 — boom → fix\\n## Verdict\\nVerdict: important-findings")
            """,
        )
        env["CODEX_THREAD_ID"] = "t"  # running inside Codex → auto picks claude
        self.ok(self.sdlc("review", self.cid, env=env))
        args = json.loads(self.calls.read_text())
        self.assertEqual(args[0], "-p")
        self.assertIn("--allowedTools", args)
        self.ok(self.sdlc("review", self.cid, "--reviewer", "claude", env=env))
        second = self.repo / "docs/changes" / self.cid / "review-2.md"
        fm = sdlc.parse_frontmatter(second.read_text())[0]
        self.assertEqual((fm["round"], fm["verdict"]), ("2", "important-findings"))
        self.ok(self.sdlc("set-status", self.cid, "review", "resolved"))
        self.assertEqual(sdlc.parse_frontmatter(second.read_text())[0]["status"], "resolved")

    def test_failed_reviewer_writes_nothing(self):
        env = self.fake_cli("codex", "import sys\nsys.stderr.write('auth error')\nsys.exit(1)\n")
        p = self.sdlc("review", self.cid, "--reviewer", "codex", env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("auth error", p.stderr)
        self.assertFalse((self.repo / "docs/changes" / self.cid / "review.md").exists())

    def test_self_review_fallback(self):
        body = self.tmp / "self.md"
        body.write_text("## Summary\nself\n## Verdict\nVerdict: no-important-findings\n")
        self.ok(self.sdlc("add", self.cid, "review", "--body-file", str(body), "--set", "reviewer=self"))
        fm, text = sdlc.parse_frontmatter((self.repo / "docs/changes" / self.cid / "review.md").read_text())
        self.assertEqual(fm["reviewer"], "self")
        self.assertIn("## Summary", text)


class PackagingTests(unittest.TestCase):
    def test_versions_and_names_are_in_lockstep(self):
        claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
        cm = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        xm = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        self.assertEqual(claude["version"], sdlc.KIT_VERSION)
        self.assertEqual(codex["version"], sdlc.KIT_VERSION)
        self.assertEqual(cm["metadata"]["version"], sdlc.KIT_VERSION)
        self.assertEqual(cm["plugins"][0]["version"], sdlc.KIT_VERSION)
        self.assertEqual(claude["name"], codex["name"])
        self.assertEqual(claude["name"], sdlc.PLUGIN)
        self.assertEqual(cm["name"], sdlc.MARKETPLACE)
        self.assertEqual(xm["name"], sdlc.MARKETPLACE)
        self.assertEqual(cm["plugins"][0]["source"], "./plugins/sdlc")
        self.assertEqual(xm["plugins"][0]["source"]["path"], "./plugins/sdlc")
        self.assertTrue(claude["repository"].endswith(sdlc.KIT_REPO))

    def test_hooks_json(self):
        hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "PreToolUse", "PostToolUse"})
        for groups in hooks.values():
            for g in groups:
                for h in g["hooks"]:
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}/bin/sdlc.py", h["command"])
        self.assertIn("apply_patch", hooks["PreToolUse"][0]["matcher"])

    def test_skills_are_valid(self):
        skills = sorted((PLUGIN / "skills").iterdir())
        self.assertEqual([s.name for s in skills], ["sdlc-fix", "sdlc-init", "sdlc-intent", "sdlc-plan", "sdlc-review", "sdlc-spec"])
        for s in skills:
            fm, body = sdlc.parse_frontmatter((s / "SKILL.md").read_text())
            self.assertEqual(fm.get("name"), s.name)
            self.assertTrue(20 < len(fm.get("description", "")) <= 1024, s.name)
            self.assertNotIn("<", fm["description"])
            self.assertTrue(body.strip())

    def test_templates_exist(self):
        for name in ("intent", "spec", "plan", "review"):
            fm, _ = sdlc.parse_frontmatter((PLUGIN / "templates/changes" / (name + ".md")).read_text())
            self.assertEqual(fm["artifact"], name)


if __name__ == "__main__":
    unittest.main()
