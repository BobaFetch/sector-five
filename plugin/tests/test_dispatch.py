"""Focused regression tests for tools/dispatch.py. No network access.

Run from the plugin directory:
    python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import dispatch


def route_answers(confidence=0.9, role="backend"):
    return {
        "role": {"choice": role, "confidence": confidence,
                 "probabilities": {role: confidence}},
        "tier": {"choice": "flagship", "confidence": 0.8},
        "difficulty": {"score": 1, "confidence": 0.7},
    }


class ParserTest(unittest.TestCase):
    def test_common_flags_after_route(self):
        args = dispatch.build_parser().parse_args(
            ["route", "--task", "x", "--no-log", "--provider", "jev",
             "--min-confidence", "0.9", "--format", "json", "--timeout", "5"])
        self.assertEqual(args.task, "x")
        self.assertTrue(args.no_log)
        self.assertEqual(args.provider, "jev")
        self.assertEqual(args.min_confidence, 0.9)
        self.assertEqual(args.format, "json")
        self.assertEqual(args.timeout, 5.0)

    def test_common_flags_after_gate(self):
        args = dispatch.build_parser().parse_args(
            ["gate", "--state", "s", "--statement", "q",
             "--threshold", "0.7", "--no-log"])
        self.assertEqual(args.threshold, 0.7)
        self.assertTrue(args.no_log)


class RouteTest(unittest.TestCase):
    def run_route(self, answers, extra=()):
        with mock.patch.object(dispatch, "ask", return_value=("jev", answers)), \
             mock.patch.object(dispatch, "append_log"), \
             mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dispatch.main(["route", "--task", "x", *extra])
        return code, out.getvalue()

    def test_routed_exit_zero(self):
        code, out = self.run_route(route_answers(confidence=0.9))
        self.assertEqual(code, 0)
        self.assertIn("verdict: ROUTED", out)

    def test_low_confidence_escalates(self):
        code, out = self.run_route(route_answers(confidence=0.2))
        self.assertEqual(code, 3)
        self.assertIn("verdict: ESCALATE", out)

    def test_threshold_boundary(self):
        code, _ = self.run_route(route_answers(confidence=0.6),
                                 ["--min-confidence", "0.6"])
        self.assertEqual(code, 0)

    def test_expect_match_routes(self):
        code, out = self.run_route(route_answers(), ["--expect", "backend"])
        self.assertEqual(code, 0)
        self.assertIn("verdict: ROUTED", out)

    def test_expect_mismatch_below_trust_escalates(self):
        code, out = self.run_route(route_answers(confidence=0.8),
                                   ["--expect", "qa", "--format", "json"])
        self.assertEqual(code, 3)
        decision = json.loads(out)
        self.assertEqual(decision["verdict"], "mismatch")
        self.assertEqual(decision["expected_role"], "qa")

    def test_expect_mismatch_at_trust_overrides(self):
        code, out = self.run_route(route_answers(confidence=0.9),
                                   ["--expect", "qa", "--format", "json"])
        self.assertEqual(code, 0)
        decision = json.loads(out)
        self.assertEqual(decision["verdict"], "override")
        self.assertEqual(decision["role"], "backend")
        self.assertEqual(decision["expected_role"], "qa")

    def test_low_confidence_mismatch_still_escalates_with_low_trust(self):
        code, out = self.run_route(route_answers(confidence=0.4),
                                   ["--expect", "qa", "--trust", "0.3"])
        self.assertEqual(code, 3)
        self.assertIn("verdict: MISMATCH", out)

    def test_plan_frames_step_ahead_of_context(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# Plan\nbackend backend backend")
        try:
            with mock.patch.object(dispatch, "ask",
                                   return_value=("jev", route_answers())) as ask, \
                 mock.patch.object(dispatch, "append_log"), \
                 mock.patch.object(dispatch, "load_dotenv", lambda: None), \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                dispatch.main(["route", "--task", "write tests", "--plan", fh.name])
        finally:
            os.unlink(fh.name)
        state = ask.call_args[0][0]
        self.assertTrue(state.startswith("Step to route: write tests\n"))
        self.assertIn("route only the step above", state)
        self.assertTrue(state.endswith("backend backend backend"))

    def test_json_format_parses(self):
        code, out = self.run_route(route_answers(), ["--format", "json"])
        self.assertEqual(code, 0)
        decision = json.loads(out)
        self.assertEqual(decision["verdict"], "routed")
        self.assertEqual(decision["role"], "backend")


class GateTest(unittest.TestCase):
    def run_gate(self, noul, extra=()):
        answers = {"gate": {"noul": noul}}
        with mock.patch.object(dispatch, "ask", return_value=("jev", answers)), \
             mock.patch.object(dispatch, "append_log"), \
             mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dispatch.main(["gate", "--state", "s", "--statement", "q",
                                  *extra])
        return code, out.getvalue()

    def test_high_noul_blocks(self):
        code, out = self.run_gate(0.9)
        self.assertEqual(code, 4)
        self.assertIn("verdict: BLOCKED", out)

    def test_low_noul_proceeds(self):
        code, out = self.run_gate(0.1)
        self.assertEqual(code, 0)
        self.assertIn("verdict: PROCEED", out)


class CredsTest(unittest.TestCase):
    def test_no_credentials_exit_two(self):
        with mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = dispatch.main(["route", "--task", "x"])
        self.assertEqual(code, 2)
        self.assertIn("TYPESAFE_API_KEY", err.getvalue())

    def test_explicit_clef_without_keys(self):
        with mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("sys.stderr", new_callable=io.StringIO):
            code = dispatch.main(["route", "--task", "x", "--provider", "clef"])
        self.assertEqual(code, 2)


class ProviderTest(unittest.TestCase):
    def test_auto_prefers_jev_then_clef(self):
        env = {"TYPESAFE_API_KEY": "k", "CLOUDFLARE_ACCOUNT_ID": "a",
               "CLOUDFLARE_AUTH_TOKEN": "t"}
        with mock.patch.dict(os.environ, env, clear=True):
            order = [name for name, _ in dispatch.decide_provider("auto")]
        self.assertEqual(order, ["jev", "clef"])

    def test_auto_skips_missing_jev(self):
        env = {"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_AUTH_TOKEN": "t"}
        with mock.patch.dict(os.environ, env, clear=True):
            order = [name for name, _ in dispatch.decide_provider("auto")]
        self.assertEqual(order, ["clef"])

    def test_auto_with_nothing_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                dispatch.decide_provider("auto")


class UnwrapTest(unittest.TestCase):
    def test_bare_payload(self):
        self.assertEqual(dispatch.unwrap({"answers": {"a": 1}}), {"a": 1})

    def test_envelope_payload(self):
        payload = {"result": {"answers": {"a": 1}}, "success": True}
        self.assertEqual(dispatch.unwrap(payload), {"a": 1})

    def test_bad_shape_raises(self):
        with self.assertRaises(RuntimeError):
            dispatch.unwrap({"nope": True})


class DotenvTest(unittest.TestCase):
    def test_loads_and_never_overrides(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, ".env"), "w") as fh:
                fh.write('TYPESAFE_API_KEY="quoted-key"\n')
                fh.write("# comment\nEMPTY=\n")
            with mock.patch.object(dispatch, "ROOT", tmp), \
                 mock.patch.dict(os.environ, {}, clear=True):
                dispatch.load_dotenv()
                self.assertEqual(os.environ.get("TYPESAFE_API_KEY"), "quoted-key")
            with mock.patch.object(dispatch, "ROOT", tmp), \
                 mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "env-wins"},
                                 clear=True):
                dispatch.load_dotenv()
                self.assertEqual(os.environ.get("TYPESAFE_API_KEY"), "env-wins")


SECRET = "sk-test-SECRET-material"
SENTINEL = "CONFIG-SENTINEL-CONTENT"


def make_config(status="on", provider="auto", fallback="pinned", mode="multi",
                roles=None):
    return {
        "version": 1,
        "dispatcher": {"status": status, "provider": provider,
                       "fallbackMode": fallback},
        "harness": {"mode": mode, "roles": roles if roles is not None else
                    {"backend": "codex/gpt-6.1-sol @ high"}},
    }


class WorkRepoCase(unittest.TestCase):
    """Runs commands from a temp working repo that has .sectorfive/."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.meta = os.path.join(self.repo, ".sectorfive")
        os.mkdir(self.meta)
        self._cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write_config(self, config):
        with open(os.path.join(self.meta, "config.json"), "w") as fh:
            if isinstance(config, str):
                fh.write(config)
            else:
                json.dump(config, fh)

    def log_path(self):
        return os.path.join(self.meta, "dispatch-log.jsonl")

    def run_main(self, argv, env=None, probe=None):
        """probe: answers dict, or an exception to raise, for both providers."""
        def fake_call(state, questions, timeout):
            if isinstance(probe, Exception):
                raise probe
            return probe
        calls = mock.Mock(side_effect=fake_call)
        with mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch.dict(os.environ, env or {}, clear=True), \
             mock.patch.object(dispatch, "call_jev", calls), \
             mock.patch.object(dispatch, "call_clef", calls), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = dispatch.main(argv)
        self.calls = calls
        return code, out.getvalue(), err.getvalue()


JEV_ENV = {"TYPESAFE_API_KEY": SECRET}
CLEF_ENV = {"CLOUDFLARE_ACCOUNT_ID": "acct-" + SECRET,
            "CLOUDFLARE_AUTH_TOKEN": "tok-" + SECRET}
OK_PROBE = {"check": {"noul": 0.9}}


class CheckTest(WorkRepoCase):
    def assert_no_secret(self, *texts):
        for text in texts:
            self.assertNotIn(SECRET, text)

    def test_no_credentials_exit_five(self):
        code, out, err = self.run_main(["check"])
        self.assertEqual(code, 5)
        self.assertIn("TYPESAFE_API_KEY: missing", out)
        self.assertIn("no credentials", err)
        self.calls.assert_not_called()

    def test_explicit_clef_without_clef_keys_exit_five(self):
        code, _, _ = self.run_main(["check", "--provider", "clef"], env=JEV_ENV)
        self.assertEqual(code, 5)
        self.calls.assert_not_called()

    def test_ready_exit_zero_without_config(self):
        code, out, err = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("TYPESAFE_API_KEY: present", out)
        self.assertIn("provider: jev ready", out)
        self.assertNotIn("dispatch:", out)  # no config → no banner
        self.assertEqual(self.calls.call_count, 1)
        self.assert_no_secret(out, err)

    def test_auto_uses_clef_when_only_clef_keys(self):
        code, out, _ = self.run_main(["check"], env=CLEF_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("provider: clef ready", out)

    def test_probe_error_exit_two(self):
        boom = RuntimeError("HTTP 401: bad key " + SECRET)
        code, out, err = self.run_main(["check"], env=JEV_ENV, probe=boom)
        self.assertEqual(code, 2)
        self.assertIn("jev probe failed", err)
        self.assert_no_secret(out, err)

    def test_probe_bad_answers_exit_two(self):
        for probe in ({}, {"check": {}}, {"check": {"noul": "yes"}},
                      {"check": {"noul": 1.5}}, {"check": {"noul": -0.1}}):
            with self.subTest(probe=probe):
                code, _, err = self.run_main(["check"], env=JEV_ENV, probe=probe)
                self.assertEqual(code, 2)
                self.assertIn("probe failed", err)

    def test_auto_probe_failure_does_not_fall_back(self):
        env = dict(JEV_ENV, **CLEF_ENV)
        code, _, _ = self.run_main(["check"], env=env, probe=RuntimeError("x"))
        self.assertEqual(code, 2)
        self.assertEqual(self.calls.call_count, 1)

    def test_status_off_skips_probe_exit_zero(self):
        self.write_config(make_config(status="off", fallback="block",
                                      mode="single"))
        code, out, _ = self.run_main(["check"])
        self.assertEqual(code, 0)
        self.calls.assert_not_called()
        self.assertIn("dispatch: auto off · fallback: block · harness: single",
                      out)

    def test_banner_on_ready(self):
        self.write_config(make_config(status="on", provider="jev"))
        code, out, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("dispatch: jev ✓ · fallback: pinned · harness: multi", out)

    def test_banner_on_auto_names_resolved_provider(self):
        self.write_config(make_config(status="on", provider="auto",
                                      fallback="ask"))
        code, out, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("dispatch: jev ✓ · fallback: ask · harness: multi", out)

    def test_banner_on_probe_failed(self):
        self.write_config(make_config(status="on", provider="jev"))
        code, out, _ = self.run_main(["check"], env=JEV_ENV,
                                     probe=RuntimeError("down"))
        self.assertEqual(code, 2)
        self.assertIn("dispatch: jev ✗ · fallback: pinned · harness: multi", out)

    def test_banner_on_no_credentials(self):
        self.write_config(make_config(status="on", provider="jev",
                                      fallback="block"))
        code, out, _ = self.run_main(["check"])
        self.assertEqual(code, 5)
        self.assertIn("dispatch: jev ✗ · fallback: block · harness: multi", out)

    def test_banner_pending_without_credentials(self):
        self.write_config(make_config(status="pending", fallback="ask"))
        code, out, _ = self.run_main(["check"])
        self.assertEqual(code, 5)
        self.assertIn("dispatch: auto pending · fallback: ask · harness: multi",
                      out)

    def test_banner_pending_with_credentials(self):
        self.write_config(make_config(status="pending"))
        code, out, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("dispatch: jev pending · fallback: pinned", out)

    def test_cli_provider_overrides_config(self):
        self.write_config(make_config(status="on", provider="jev"))
        code, out, _ = self.run_main(["check", "--provider", "clef"],
                                     env=CLEF_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("dispatch: clef ✓", out)

    def test_explicit_config_path(self):
        path = os.path.join(self.repo, "elsewhere.json")
        with open(path, "w") as fh:
            json.dump(make_config(status="off"), fh)
        code, out, _ = self.run_main(["check", "--config", path])
        self.assertEqual(code, 0)
        self.assertIn("dispatch: auto off", out)

    def test_missing_explicit_config_exit_six(self):
        code, _, err = self.run_main(
            ["check", "--config", os.path.join(self.repo, "nope.json")])
        self.assertEqual(code, 6)
        self.assertIn("invalid config.json", err)

    def test_invalid_configs_exit_six_without_contents(self):
        good = make_config()
        cases = {
            "bad json": '{"version": 1, "x": "%s" ' % SENTINEL,
            "not object": json.dumps([SENTINEL]),
            "bad version": dict(good, version=SENTINEL),
            "bool version": dict(good, version=True),
            "bad status": make_config(status=SENTINEL),
            "bad provider": make_config(provider=SENTINEL),
            "bad fallback": make_config(fallback=SENTINEL),
            "missing fallback": dict(good, dispatcher={"status": "on",
                                                       "provider": "auto"}),
            "bad mode": make_config(mode=SENTINEL),
            "roles not object": make_config(roles=[SENTINEL]),
            "unknown role": make_config(
                roles={SENTINEL: "codex/gpt-6.1-sol @ high"}),
            "assignment not string": make_config(roles={"qa": [SENTINEL]}),
            "assignment no effort": make_config(roles={"qa": SENTINEL + "/m"}),
            "assignment no harness": make_config(
                roles={"qa": SENTINEL + " @ high"}),
            "assignment empty model": make_config(roles={"qa": "claude/ @ high"}),
        }
        for name, config in cases.items():
            with self.subTest(name):
                self.write_config(config if isinstance(config, str)
                                  else json.dumps(config))
                code, out, err = self.run_main(["check"], env=JEV_ENV,
                                               probe=OK_PROBE)
                self.assertEqual(code, 6)
                self.assertIn("invalid config.json", err)
                self.assertNotIn(SENTINEL, out + err)
                self.assert_no_secret(out, err)
                self.calls.assert_not_called()

    def test_valid_roles_accepted(self):
        roles = {"planning": "claude/opus @ default",
                 "backend": "codex/gpt-6.1-sol @ high",
                 "frontend": "codex/gpt-6.1-sol @ high",
                 "polish": "claude/sonnet @ default",
                 "qa": "muse/muse-spark-1.3 @ high"}
        self.write_config(make_config(status="off", roles=roles))
        code, _, _ = self.run_main(["check"])
        self.assertEqual(code, 0)

    def test_check_never_logs(self):
        self.write_config(make_config(status="on"))
        for env, probe in ((JEV_ENV, OK_PROBE), (JEV_ENV, RuntimeError("x")),
                           ({}, None)):
            self.run_main(["check"], env=env, probe=probe)
        self.write_config(make_config(status="off"))
        self.run_main(["check"])
        self.write_config("{bad")
        self.run_main(["check"])
        self.assertFalse(os.path.exists(self.log_path()))


class UnroutedTest(WorkRepoCase):
    def read_log(self):
        with open(self.log_path()) as fh:
            return [json.loads(line) for line in fh]

    def test_appends_unrouted_line(self):
        code, out, _ = self.run_main(
            ["unrouted", "--task", "Add pagination", "--reason", "probe failed"])
        self.assertEqual(code, 0)
        self.assertIn("verdict: UNROUTED", out)
        [entry] = self.read_log()
        self.assertEqual(entry["verdict"], "UNROUTED")
        self.assertEqual(entry["kind"], "unrouted")
        self.assertEqual(entry["task"], "Add pagination")
        self.assertEqual(entry["reason"], "probe failed")
        self.assertIn("ts", entry)
        self.calls.assert_not_called()

    def test_appends_without_reason(self):
        self.run_main(["unrouted", "--task", "a"])
        self.run_main(["unrouted", "--task", "b"])
        entries = self.read_log()
        self.assertEqual([e["task"] for e in entries], ["a", "b"])
        self.assertNotIn("reason", entries[0])

    def test_no_log_does_not_append(self):
        code, _, _ = self.run_main(["unrouted", "--task", "x", "--no-log"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.log_path()))

    def test_json_format(self):
        code, out, _ = self.run_main(
            ["unrouted", "--task", "x", "--format", "json", "--no-log"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out),
                         {"kind": "unrouted", "verdict": "UNROUTED", "task": "x"})

    def test_long_task_truncated_in_log(self):
        self.run_main(["unrouted", "--task", "x" * 3000])
        [entry] = self.read_log()
        self.assertEqual(len(entry["task"]), 2000)

    def test_no_sectorfive_dir_no_log(self):
        os.rmdir(self.meta)
        code, _, _ = self.run_main(["unrouted", "--task", "x"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.meta))


if __name__ == "__main__":
    unittest.main()


def pr_facts(**overrides):
    facts = {
        "pr": 12, "title": "fix: thing. ENG-1", "isDraft": False,
        "headSha": "abc123def456", "headCommittedAt": "2026-10-15T10:00:00Z",
        "mergeable": "MERGEABLE", "behindBy": 0,
        "body": "## Summary\nx\n\nResolves ENG-1\n\n## Plan\np\n\n## Verification\nv",
        "checks": [{"name": "build", "status": "success"},
                   {"name": "test", "status": "success"}],
        "comments": [{"author": "greptile-apps", "body": "Confidence Score: 5/5"}],
        "threads": [{"resolved": True, "outdated": False,
                     "author": "greptile-apps", "body": "P2: nit"}],
        "files": [".plans/done/2026-eng-1-thing.md", "src/a.ts"],
        "changedFiles": ["src/a.ts (+3 -1)"],
        "diff": "diff --git a/src/a.ts b/src/a.ts\n+x\n",
    }
    facts.update(overrides)
    return facts


FULL_RULES = {
    "version": 1,
    "checks": {
        "ci": {}, "reviewThreads": {}, "qa": {}, "draft": {},
        "botScore": {"author": "greptile", "pattern": r"Confidence Score: (\d+)/5",
                     "min": 5},
        "planFile": {"glob": ".plans/done/*{issue}*.md"},
        "prBody": {"sections": ["Summary", "Resolves", "Plan", "Verification"]},
    },
}


class RulesValidationTest(unittest.TestCase):
    def test_full_rules_valid(self):
        dispatch.validate_rules(FULL_RULES)

    def test_defaults_valid(self):
        dispatch.validate_rules(dispatch.DEFAULT_RULES)

    def test_rejects_bad_rules(self):
        bad = [
            [],
            {"version": 2},
            {"version": 1, "extra": 1},
            {"version": 1, "checks": {"lint": {}}},
            {"version": 1, "checks": {"ci": {"allowPreexisting": "yes"}}},
            {"version": 1, "checks": {"ci": {"ignore": [1]}}},
            {"version": 1, "checks": {"ci": {"typo": True}}},
            {"version": 1, "checks": {"botScore": {"author": "g", "min": 5}}},
            {"version": 1, "checks": {"botScore": {"author": "g", "pattern": "x",
                                                   "min": 5}}},
            {"version": 1, "checks": {"botScore": {"author": "g", "pattern": "(",
                                                   "min": 5}}},
            {"version": 1, "checks": {"botScore": {"author": "g", "pattern": "(x)",
                                                   "min": True}}},
            {"version": 1, "checks": {"planFile": {}}},
            {"version": 1, "checks": {"prBody": {"sections": []}}},
            {"version": 1, "judgments": [{"id": "a"}]},
            {"version": 1, "judgments": [{"id": "Bad Id", "statement": "s"}]},
            {"version": 1, "judgments": [{"id": "a", "statement": "s"},
                                         {"id": "a", "statement": "t"}]},
            {"version": 1, "judgments": [{"id": "a", "statement": "s",
                                          "threshold": 0}]},
            {"version": 1, "judgments": [{"id": "a", "statement": "s",
                                          "evidence": ["logs"]}]},
            {"version": 1, "judgment": {"onUnavailable": "ignore"}},
            {"version": 1, "checks": {"botScore": {"author": "g", "pattern": "(x)",
                                                   "min": 5, "commitPattern": "x"}}},
            {"version": 1, "checks": {"mergeable": {"maxBehind": -1}}},
            {"version": 1, "checks": {"mergeable": {"maxBehind": 1.5}}},
            {"version": 1, "checks": {"ci": {"docsOnly": "*.md"}}},
            {"version": 1, "judgment": {"maxStateChars": 10}},
        ]
        for rules in bad:
            with self.subTest(rules=rules):
                with self.assertRaises(ValueError):
                    dispatch.validate_rules(rules)


class FactsTest(unittest.TestCase):
    ITEM = {"issue": "ENG-1", "qa": {"verdict": "approve", "sha": "abc123def456"},
            "ci": {"preexisting": []}}

    def statuses(self, facts, rules=FULL_RULES, item=ITEM):
        return {k: v["status"] for k, v in
                dispatch.evaluate_facts(rules, facts, item).items()}

    def test_all_pass(self):
        self.assertEqual(set(self.statuses(pr_facts()).values()), {"pass"})

    def test_ci_pending_failed_and_none(self):
        running = pr_facts(checks=[{"name": "build", "status": "pending"}])
        self.assertEqual(self.statuses(running)["ci"], "pending")
        red = pr_facts(checks=[{"name": "build", "status": "failure"},
                               {"name": "test", "status": "pending"}])
        self.assertEqual(self.statuses(red)["ci"], "fail")
        self.assertEqual(self.statuses(pr_facts(checks=[]))["ci"], "pending")

    def test_ci_ignore_required_and_preexisting(self):
        red = pr_facts(checks=[{"name": "build", "status": "success"},
                               {"name": "flaky", "status": "failure"}])
        rules = {"version": 1, "checks": {"ci": {"ignore": ["flaky"]}}}
        self.assertEqual(self.statuses(red, rules)["ci"], "pass")
        rules = {"version": 1, "checks": {"ci": {"allowPreexisting": True}}}
        item = dict(self.ITEM, ci={"preexisting": ["flaky"]})
        self.assertEqual(self.statuses(red, rules, item)["ci"], "pass")
        self.assertEqual(self.statuses(red, rules)["ci"], "fail")
        rules = {"version": 1, "checks": {"ci": {"required": ["e2e"]}}}
        self.assertEqual(self.statuses(pr_facts(), rules)["ci"], "fail")

    def test_unresolved_threads(self):
        facts = pr_facts(threads=[{"resolved": False, "outdated": True,
                                   "author": "g", "body": "b"}])
        self.assertEqual(self.statuses(facts)["reviewThreads"], "fail")
        rules = {"version": 1, "checks": {"reviewThreads": {"ignoreOutdated": True}}}
        self.assertEqual(self.statuses(facts, rules)["reviewThreads"], "pass")

    def test_bot_score_uses_latest_and_waits(self):
        facts = pr_facts(comments=[
            {"author": "greptile-apps", "body": "Confidence Score: 5/5"},
            {"author": "greptile-apps", "body": "Confidence Score: 3/5"}])
        self.assertEqual(self.statuses(facts)["botScore"], "fail")
        facts = pr_facts(comments=[{"author": "someone", "body": "Confidence Score: 5/5"}])
        self.assertEqual(self.statuses(facts)["botScore"], "pending")

    def test_bot_score_must_be_for_head(self):
        rules = {"version": 1, "checks": {"botScore": {
            "author": "greptile", "pattern": r"greptile_confidence_score:(\d+)",
            "min": 5, "commitPattern": r"/commit/([0-9a-f]{7,40})"}}}
        body = "<!-- greptile_confidence_score:5 --> Last reviewed commit: x/commit/%s"
        fresh = pr_facts(comments=[{"author": "greptile-apps[bot]",
                                    "body": body % "abc123def456"}])
        self.assertEqual(self.statuses(fresh, rules)["botScore"], "pass")
        stale = pr_facts(comments=[{"author": "greptile-apps[bot]",
                                    "body": body % "0000000aaaa"}])
        result = dispatch.evaluate_facts(rules, stale, self.ITEM)["botScore"]
        self.assertEqual(result["status"], "pending")
        self.assertIn("is for 0000000aaaa", result["detail"])
        # A review carries its commit; a comment without one falls back to time.
        review = pr_facts(comments=[{"author": "greptile", "commit": "fff",
                                     "body": "greptile_confidence_score:5"}])
        self.assertEqual(self.statuses(review, rules)["botScore"], "pending")
        early = pr_facts(comments=[{"author": "greptile", "at": "2026-10-15T09:00:00Z",
                                    "body": "greptile_confidence_score:5"}])
        self.assertEqual(self.statuses(early, rules)["botScore"], "pending")
        late = pr_facts(comments=[{"author": "greptile", "at": "2026-10-15T11:00:00Z",
                                   "body": "greptile_confidence_score:5"}])
        self.assertEqual(self.statuses(late, rules)["botScore"], "pass")

    def test_mergeable(self):
        rules = {"version": 1, "checks": {"mergeable": {}}}
        self.assertEqual(self.statuses(pr_facts(behindBy=40), rules)["mergeable"], "pass")
        self.assertEqual(self.statuses(pr_facts(mergeable="CONFLICTING"),
                                       rules)["mergeable"], "fail")
        self.assertEqual(self.statuses(pr_facts(mergeable="UNKNOWN"),
                                       rules)["mergeable"], "pending")
        strict = {"version": 1, "checks": {"mergeable": {"maxBehind": 0}}}
        self.assertEqual(self.statuses(pr_facts(behindBy=1), strict)["mergeable"], "fail")
        self.assertEqual(self.statuses(pr_facts(), strict)["mergeable"], "pass")
        self.assertEqual(self.statuses(pr_facts(behindBy=None), strict)["mergeable"],
                         "pending")
        self.assertEqual(self.statuses(pr_facts(behindBy=None), rules)["mergeable"], "pass")

    def test_ci_detail_names_docs_only_source(self):
        facts = pr_facts(checksFrom={"sha": "c0de" * 10, "docsOnlyCommits": 2})
        result = dispatch.evaluate_facts(FULL_RULES, facts, self.ITEM)["ci"]
        self.assertEqual(result["status"], "pass")
        self.assertIn("2 docs-only commit(s)", result["detail"])

    def test_plan_file_body_qa_draft(self):
        facts = pr_facts(files=["src/a.ts"], body="## Summary", isDraft=True,
                         headSha="ffff")
        result = self.statuses(facts)
        self.assertEqual(result["planFile"], "fail")
        self.assertEqual(result["prBody"], "fail")
        self.assertEqual(result["qa"], "fail")  # approved a different SHA
        self.assertEqual(result["draft"], "fail")
        self.assertEqual(self.statuses(pr_facts(), item=None)["qa"], "fail")

    def test_plan_glob_case(self):
        facts = pr_facts(files=[".plans/done/2026-ENG-1-x.md"])
        self.assertEqual(self.statuses(facts)["planFile"], "pass")
        rules = {"version": 1, "checks": {"planFile": {
            "glob": ".plans/done/*{issue}*.md", "caseInsensitive": False}}}
        facts = pr_facts(files=[".plans/done/2026-eng-1-x.md"])
        self.assertEqual(self.statuses(facts, rules)["planFile"], "fail")


class DocsOnlyFallbackTest(unittest.TestCase):
    FILES = {"code1": "src/a.ts", "doc1": ".plans/done/x.md", "doc2": "README.md"}

    def fake_gh(self, *argv):
        path = argv[-3] if argv[0] == "api" and "--jq" in argv else ""
        sha = path.split("/")[4] if path.count("/") >= 4 else ""
        if path.endswith("/check-runs"):
            return '{"name": "test", "status": "completed", "conclusion": "success"}\n'
        if path.endswith("/status"):
            return ""
        return self.FILES[sha] + "\n"

    def run_fallback(self, commits):
        facts = {"checks": []}
        with mock.patch.object(dispatch, "gh", side_effect=self.fake_gh):
            dispatch.docs_only_fallback(facts, commits, [".plans/*", "*.md"])
        return facts

    def test_uses_newest_code_commit(self):
        facts = self.run_fallback(["code1", "doc1", "doc2"])
        self.assertEqual(facts["checksFrom"], {"sha": "code1", "docsOnlyCommits": 2})
        self.assertEqual(facts["checks"], [{"name": "test", "status": "success"}])

    def test_code_head_keeps_no_checks(self):
        facts = self.run_fallback(["doc1", "code1"])
        self.assertEqual(facts, {"checks": []})


class FitDiffTest(unittest.TestCase):
    def test_every_file_keeps_a_share(self):
        big = "diff --git a/src/a b/src/a\n" + "+" * 5000 + "\n"
        tests = "diff --git a/test/a b/test/a\n+it('works')\n"
        out = dispatch.fit_diff(big + tests, 1000)
        self.assertLessEqual(len(out), 1000)
        self.assertIn("test/a", out)
        self.assertIn("it('works')", out)
        self.assertIn("[trimmed]", out)

    def test_small_diff_untouched(self):
        self.assertEqual(dispatch.fit_diff("diff --git a b\n+x\n", 100),
                         "diff --git a b\n+x\n")


class WorkTest(WorkRepoCase):
    def work(self, *argv):
        return self.run_main(["work", *argv])

    def item(self):
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            return json.load(fh)

    def test_lifecycle_with_gates(self):
        self.assertEqual(self.work("new", "--issue", "ENG-1",
                                   "--plan", ".sectorfive/plans/eng-1.md")[0], 0)
        code, out, _ = self.work("advance", "--issue", "ENG-1", "--to", "building")
        self.assertEqual(code, 4)
        self.assertIn("no owner recorded", out)
        self.work("set", "--issue", "ENG-1", "owner=frontend", "branch=fix/eng-1")
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "building")[0], 0)
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "open")[0], 4)  # skips review
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "review")[0], 0)
        code, out, _ = self.work("advance", "--issue", "ENG-1", "--to", "open")
        self.assertEqual(code, 4)
        self.assertIn("no qa approve", out)
        self.work("set", "--issue", "ENG-1", "pr=12", "qa.verdict=approve",
                  "qa.sha=abc123def456")
        self.assertEqual(self.work("advance", "--issue", "ENG-1", "--to", "open")[0], 0)
        code, out, _ = self.work("advance", "--issue", "ENG-1", "--to", "done")
        self.assertEqual(code, 4)
        self.assertIn("done check has not passed", out)
        self.assertEqual(self.item()["phase"], "open")
        self.assertEqual([h["to"] for h in self.item()["history"]],
                         ["planned", "building", "review", "open"])

    def test_rework_and_blocked(self):
        self.work("new", "--issue", "ENG-1", "--plan", "p.md")
        self.work("set", "--issue", "ENG-1", "owner=backend", "branch=b")
        self.work("advance", "--issue", "ENG-1", "--to", "building")
        self.work("advance", "--issue", "ENG-1", "--to", "review")
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "building")[0], 0)  # veto rework
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "blocked")[0], 2)  # needs --note
        self.assertEqual(self.work("advance", "--issue", "ENG-1", "--to", "blocked",
                                   "--note", "env down")[0], 0)
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "review")[0], 0)

    def test_dispatcher_on_requires_routed_issue(self):
        self.write_config(make_config(status="on"))
        self.work("new", "--issue", "ENG-1", "--plan", "p.md")
        self.work("set", "--issue", "ENG-1", "owner=qa")
        code, out, _ = self.work("advance", "--issue", "ENG-1", "--to", "building")
        self.assertEqual(code, 4)
        self.assertIn("no route/unrouted line", out)
        self.run_main(["unrouted", "--task", "t", "--issue", "ENG-1"])
        self.assertEqual(self.work("advance", "--issue", "ENG-1",
                                   "--to", "building")[0], 0)

    def test_set_validates(self):
        self.work("new", "--issue", "ENG-1")
        for pair in ("owner=planning", "pr=abc", "qa.verdict=ok", "color=red", "x"):
            with self.subTest(pair=pair):
                self.assertEqual(self.work("set", "--issue", "ENG-1", pair)[0], 2)
        self.assertEqual(self.work("set", "--issue", "ENG-1",
                                   "ci.preexisting=e2e, lint")[0], 0)
        self.assertEqual(self.item()["ci"]["preexisting"], ["e2e", "lint"])

    def test_bad_issue_key_and_duplicates(self):
        self.assertEqual(self.work("new", "--issue", "../x")[0], 2)
        self.work("new", "--issue", "ENG-1")
        self.assertEqual(self.work("new", "--issue", "ENG-1")[0], 2)

    def test_show(self):
        self.work("new", "--issue", "ENG-1")
        self.work("new", "--issue", "ENG-2")
        code, out, _ = self.work("show")
        self.assertEqual(code, 0)
        self.assertIn("ENG-1\tplanned", out)
        self.assertIn("ENG-2\tplanned", out)

    def test_lane_field(self):
        self.work("new", "--issue", "ENG-1")
        self.assertIsNone(self.item()["lane"])
        self.assertEqual(self.work("set", "--issue", "ENG-1", "lane=w3")[0], 0)
        self.assertEqual(self.item()["lane"], "w3")
        self.assertIn("lane w3", self.work("show")[1])


class DoneCmdTest(WorkRepoCase):
    def setUp(self):
        super().setUp()
        self.facts_path = os.path.join(self.repo, "facts.json")
        self.write_facts(pr_facts())
        self.rules_path = os.path.join(self.repo, "rules.json")
        self.write_rules(FULL_RULES)
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        self.run_main(["work", "set", "--issue", "ENG-1", "owner=frontend",
                       "branch=b", "pr=12", "qa.verdict=approve",
                       "qa.sha=abc123def456"])

    def write_facts(self, facts):
        with open(self.facts_path, "w") as fh:
            json.dump(facts, fh)

    def write_rules(self, rules):
        with open(self.rules_path, "w") as fh:
            json.dump(rules, fh)

    def done(self, *extra, answers=None, error=None):
        def fake_ask(state, questions, provider, timeout):
            if error:
                raise error
            self.states.append(state)
            return "jev", {k: {"noul": answers[k]} for k in questions}
        self.states = []
        with mock.patch.object(dispatch, "ask", side_effect=fake_ask):
            return self.run_main(["done", "--issue", "ENG-1", "--facts",
                                  self.facts_path, "--rules", self.rules_path,
                                  "--format", "json", *extra])

    def log(self):
        with open(self.log_path()) as fh:
            return [json.loads(line) for line in fh]

    def test_facts_only_done_and_recorded(self):
        code, out, _ = self.done()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["verdict"], "done")
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            item = json.load(fh)
        self.assertEqual(item["done"]["verdict"], "done")
        self.assertEqual(item["done"]["sha"], "abc123def456")
        self.assertEqual(self.log()[-1]["kind"], "done")
        for phase in ("building", "review", "open", "done", "merged"):
            self.assertEqual(self.run_main(["work", "advance", "--issue", "ENG-1",
                                            "--to", phase])[0], 0)
        closeout = self.log()[-1]
        self.assertEqual(closeout["kind"], "closeout")
        self.assertEqual(closeout["pr"], 12)
        self.assertEqual(closeout["threads"]["by_author"], {"greptile-apps": 1})

    def test_done_must_match_qa_sha(self):
        self.write_rules({"version": 1, "checks": {"ci": {}}})
        self.write_facts(pr_facts(headSha="newer000"))
        self.assertEqual(self.done()[0], 0)
        for phase in ("building", "review", "open"):
            self.run_main(["work", "advance", "--issue", "ENG-1", "--to", phase])
        code, out, _ = self.run_main(["work", "advance", "--issue", "ENG-1",
                                      "--to", "done"])
        self.assertEqual(code, 4)
        self.assertIn("qa approved abc123def456", out)

    def test_failing_fact_skips_judgments(self):
        self.write_facts(pr_facts(isDraft=True))
        self.write_rules(dict(FULL_RULES, judgments=[{"id": "a", "statement": "s"}]))
        code, out, _ = self.done(answers={"a": 0.0})
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(out)["verdict"], "not_done")
        self.assertEqual(self.states, [])

    def test_pending_exit_seven(self):
        self.write_facts(pr_facts(checks=[{"name": "build", "status": "pending"}]))
        code, out, _ = self.done()
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["verdict"], "pending")

    def test_judgments_block_and_pass(self):
        self.write_rules(dict(FULL_RULES, judgments=[
            {"id": "criteria", "statement": "s", "threshold": 0.5},
            {"id": "tests", "statement": "t", "threshold": 0.6, "evidence": ["diff"]}]))
        code, out, _ = self.done(answers={"criteria": 0.2, "tests": 0.7})
        self.assertEqual(code, 4)
        decision = json.loads(out)
        self.assertEqual(decision["verdict"], "blocked")
        self.assertTrue(decision["judgments"]["tests"]["blocked"])
        self.assertEqual(len(self.states), 2)  # one state per evidence set
        diff_only = [s for s in self.states if "--- plan ---" not in s
                     and "--- pull request ---" not in s]
        self.assertEqual(len(diff_only), 1)
        self.assertIn("--- diff ---", diff_only[0])
        code, out, _ = self.done(answers={"criteria": 0.2, "tests": 0.1})
        self.assertEqual(code, 0)

    def test_unavailable_escalates_or_facts_only(self):
        rules = dict(FULL_RULES, judgments=[{"id": "a", "statement": "s"}])
        self.write_rules(rules)
        code, out, _ = self.done(error=RuntimeError("down"))
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["verdict"], "escalate")
        self.write_rules(dict(rules, judgment={"onUnavailable": "facts_only"}))
        code, out, _ = self.done(error=RuntimeError("down"))
        self.assertEqual(code, 0)
        self.assertIn("skipped", json.loads(out)["judgments"])

    def test_invalid_rules_exit_six(self):
        with open(self.rules_path, "w") as fh:
            fh.write("{not json " + SENTINEL)
        code, _, err = self.done()
        self.assertEqual(code, 6)
        self.assertNotIn(SENTINEL, err)
        self.write_rules({"version": 1, "checks": {"lint": {}}})
        code, _, err = self.done()
        self.assertEqual(code, 6)
        self.assertIn("unknown check", err)

    def test_defaults_without_rules_file(self):
        code, out, _ = self.run_main(["done", "--issue", "ENG-1", "--facts",
                                      self.facts_path, "--format", "json"])
        self.assertEqual(code, 0)
        decision = json.loads(out)
        self.assertEqual(decision["rules"], "defaults")
        self.assertEqual(set(decision["facts"]),
                         {"ci", "reviewThreads", "qa", "draft", "mergeable"})

    def test_check_reports_rules(self):
        with open(os.path.join(self.meta, "done.json"), "w") as fh:
            json.dump(FULL_RULES, fh)
        code, out, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)
        self.assertIn("(7 checks, 0 judgments)", out)
        with open(os.path.join(self.meta, "done.json"), "w") as fh:
            fh.write("[]")
        code, _, err = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 6)
        self.assertIn("invalid done.json", err)


# --- jev-hardening (plan 004) step 2: tier table, escalation, outcome, report ---

def tier_table(role):
    return {"standard": "codex/%s-std @ medium" % role,
            "flagship": "codex/%s-flag @ high" % role,
            "ceiling": "codex/%s-ultra @ max" % role}


def tiered_config(roles=None):
    config = make_config(roles=roles or {r: "codex/%s-pin @ high" % r
                                         for r in dispatch.ROLES})
    config["harness"]["tiers"] = {r: tier_table(r) for r in dispatch.ROLES}
    return config


def tier_answers(role="backend", tier="standard", confidence=0.9):
    return {"role": {"choice": role, "confidence": confidence,
                     "probabilities": {role: confidence}},
            "tier": {"choice": tier, "confidence": 0.8},
            "difficulty": {"score": 1, "confidence": 0.7}}


class TierRouteTest(WorkRepoCase):
    def route(self, answers, extra=()):
        with mock.patch.object(dispatch, "ask", return_value=("jev", answers)), \
             mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = dispatch.main(["route", "--task", "x", *extra])
        return code, out.getvalue(), err.getvalue()

    def route_json(self, answers, extra=()):
        code, out, _ = self.route(answers, ["--format", "json", "--no-log", *extra])
        return code, json.loads(out)

    def outcome(self, role="backend", tier="standard", result="fail", issue="ENG-1"):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            return dispatch.main(["outcome", "--issue", issue, "--role", role,
                                  "--model", "m", "--tier", tier, "--result", result])

    # tier validation
    def test_ultra_or_max_in_standard_or_flagship_exit_six(self):
        for tier in ("standard", "flagship"):
            for bad in ("codex/gpt-ultra @ high", "codex/gpt @ max",
                        "codex/GPT-MAX @ low", "codex/m @ Ultra"):
                config = tiered_config()
                config["harness"]["tiers"]["backend"][tier] = bad
                self.write_config(config)
                code, out, err = self.route(tier_answers())
                self.assertEqual(code, 6, (tier, bad))
                self.assertIn("invalid config.json", err)
                self.assertEqual(out, "")

    def test_ultra_max_allowed_in_ceiling(self):
        self.write_config(tiered_config())
        self.assertIsNotNone(dispatch.load_config(os.path.join(self.meta, "config.json")))

    def test_bad_tier_tables_rejected(self):
        bads = [[], {"nope": tier_table("backend")},
                {"backend": {"standard": "codex/a @ b", "flagship": "codex/a @ b"}},
                {"backend": dict(tier_table("backend"), extra="codex/a @ b")},
                {"backend": dict(tier_table("backend"), standard="no-slash @ x")},
                {"backend": dict(tier_table("backend"), flagship=5)}]
        for tiers in bads:
            config = make_config()
            config["harness"]["tiers"] = tiers
            self.write_config(config)
            with self.assertRaises(ValueError, msg=repr(tiers)):
                dispatch.load_config(os.path.join(self.meta, "config.json"))

    def test_check_rejects_ultra_standard_exit_six(self):
        config = tiered_config()
        config["harness"]["tiers"]["backend"]["standard"] = "codex/x @ max"
        self.write_config(config)
        code, _, err = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 6)

    def test_missing_explicit_config_exit_six(self):
        code, _, err = self.route(tier_answers(), ["--config", "/nonexistent/c.json"])
        self.assertEqual(code, 6)

    # configs without tiers
    def test_no_tiers_prints_pin_and_keeps_exit_codes(self):
        self.write_config(make_config(roles={"backend": "codex/pin @ high"}))
        for conf, want in ((0.9, 0), (0.3, 3)):
            code, out, _ = self.route(tier_answers(tier="flagship", confidence=conf),
                                      ["--no-log"])
            self.assertEqual(code, want)
            self.assertIn("model: codex/pin @ high (tier flagship)", out)
            self.assertNotIn("escalated", out)

    def test_missing_role_entry_falls_back_to_pin(self):
        config = tiered_config()
        del config["harness"]["tiers"]["frontend"]
        self.write_config(config)
        _, data = self.route_json(tier_answers(role="frontend"))
        self.assertEqual(data["model"], "codex/frontend-pin @ high")
        self.assertFalse(data["escalated"])

    def test_no_config_model_null_and_no_model_line(self):
        code, out, _ = self.route(tier_answers(), ["--no-log"])
        self.assertEqual(code, 0)
        self.assertNotIn("model:", out)
        _, data = self.route_json(tier_answers())
        self.assertIsNone(data["model"])
        self.assertFalse(data["escalated"])
        self.assertIsNone(data["escalation_reason"])

    # confident standard enforced, every role
    def test_confident_standard_enforced_every_role(self):
        self.write_config(tiered_config())
        for role in dispatch.ROLES:
            code, data = self.route_json(tier_answers(role=role), ["--ceiling-approved"])
            self.assertEqual(code, 0)
            self.assertEqual(data["model"], "codex/%s-std @ medium" % role)
            self.assertEqual(data["resolved_tier"], "standard")
            self.assertFalse(data["escalated"])

    def test_standard_failure_for_other_issue_or_role_does_not_escalate(self):
        self.write_config(tiered_config())
        self.outcome(issue="ENG-2")
        self.outcome(role="frontend")
        self.outcome(result="pass")
        _, data = self.route_json(tier_answers(), ["--issue", "ENG-1"])
        self.assertEqual(data["resolved_tier"], "standard")
        _, data = self.route_json(tier_answers())  # no --issue: no escalation
        self.assertEqual(data["resolved_tier"], "standard")

    # flagship
    def test_flagship_via_pick(self):
        self.write_config(tiered_config())
        code, out, _ = self.route(tier_answers(tier="flagship"), ["--no-log"])
        self.assertEqual(code, 0)
        self.assertIn("model: codex/backend-flag @ high (tier flagship, "
                      "escalated: flagship pick)", out)

    def test_flagship_via_logged_standard_failure(self):
        self.write_config(tiered_config())
        self.outcome()
        _, data = self.route_json(tier_answers(), ["--issue", "ENG-1"])
        self.assertEqual(data["model"], "codex/backend-flag @ high")
        self.assertEqual(data["resolved_tier"], "flagship")
        self.assertTrue(data["escalated"])
        self.assertEqual(data["escalation_reason"], "standard failure")

    # ceiling
    def test_ceiling_requires_flag_and_flagship_failure(self):
        self.write_config(tiered_config())
        self.outcome(tier="flagship")
        _, data = self.route_json(tier_answers(tier="flagship"), ["--issue", "ENG-1"])
        self.assertEqual(data["resolved_tier"], "flagship")
        _, data = self.route_json(tier_answers(tier="flagship"),
                                  ["--issue", "ENG-1", "--ceiling-approved"])
        self.assertEqual(data["resolved_tier"], "ceiling")
        self.assertEqual(data["model"], "codex/backend-ultra @ max")

    def test_ceiling_flag_without_flagship_failure_caps_at_flagship(self):
        self.write_config(tiered_config())
        self.outcome(tier="standard")
        _, data = self.route_json(tier_answers(tier="flagship"),
                                  ["--issue", "ENG-1", "--ceiling-approved"])
        self.assertEqual(data["resolved_tier"], "flagship")

    # low confidence
    def test_low_confidence_gets_standard(self):
        self.write_config(tiered_config())
        self.outcome(tier="flagship")
        self.outcome(tier="standard")
        code, data = self.route_json(tier_answers(tier="flagship", confidence=0.3),
                                     ["--issue", "ENG-1", "--ceiling-approved"])
        self.assertEqual(code, 3)
        self.assertEqual(data["resolved_tier"], "standard")
        self.assertEqual(data["model"], "codex/backend-std @ medium")

    def test_mismatch_uses_picked_role_table(self):
        self.write_config(tiered_config())
        code, data = self.route_json(tier_answers(role="frontend", confidence=0.95),
                                     ["--expect", "backend"])
        self.assertEqual(data["model"], "codex/frontend-std @ medium")

    def test_route_log_carries_model_fields(self):
        self.write_config(tiered_config())
        self.route(tier_answers(tier="flagship"), ["--issue", "ENG-1"])
        with open(self.log_path()) as fh:
            entry = json.loads(fh.readlines()[-1])
        self.assertEqual(entry["model"], "codex/backend-flag @ high")
        self.assertEqual(entry["resolved_tier"], "flagship")
        self.assertTrue(entry["escalated"])

    # outcome and report
    def test_outcome_line_shape(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dispatch.main(["outcome", "--issue", "ENG-9", "--role", "qa",
                                  "--model", "codex/q @ high", "--tier", "flagship",
                                  "--result", "pass", "--retries", "2", "--escalated"])
        self.assertEqual(code, 0)
        self.assertIn("outcome: recorded", out.getvalue())
        with open(self.log_path()) as fh:
            entry = json.loads(fh.readlines()[-1])
        self.assertTrue(entry.pop("ts"))
        self.assertEqual(entry, {"kind": "outcome", "issue": "ENG-9", "role": "qa",
                                 "model": "codex/q @ high", "tier": "flagship",
                                 "result": "pass", "retries": 2, "escalated": True})

    def test_outcome_negative_retries_exit_two(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            code = dispatch.main(["outcome", "--issue", "E", "--role", "qa", "--model",
                                  "m", "--tier", "standard", "--result", "pass",
                                  "--retries", "-1"])
        self.assertEqual(code, 2)

    def test_report_aggregates(self):
        self.write_config(tiered_config())
        self.route(tier_answers(), ["--issue", "ENG-1"])
        self.outcome()
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            dispatch.main(["outcome", "--issue", "ENG-1", "--role", "backend",
                           "--model", "m", "--tier", "flagship", "--result", "pass",
                           "--retries", "3", "--escalated"])
        self.route(tier_answers(), ["--issue", "ENG-1"])
        self.outcome(issue="ENG-2", result="pass")
        with open(self.log_path(), "a") as fh:
            fh.write("not json\n")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(dispatch.main(["report", "--issue", "ENG-1",
                                            "--format", "json"]), 0)
        rep = json.loads(out.getvalue())
        rows = {(r["role"], r["tier"]): r for r in rep["rows"]}
        std, flag = rows[("backend", "standard")], rows[("backend", "flagship")]
        self.assertEqual((std["routes"], std["outcomes"], std["fail"]), (1, 1, 1))
        self.assertEqual((flag["routes"], flag["outcomes"], flag["pass"]), (1, 1, 1))
        self.assertEqual(flag["mean_retries"], 3)
        self.assertEqual(flag["escalations"], 2)  # route line + outcome line
        self.assertEqual(rep["tier_picks"], {"standard": 2})
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dispatch.main(["report"])
        text = out.getvalue()
        self.assertIn("backend standard 1 2 1 1 0.00 0", text)
        self.assertIn('tier picks: {"standard": 2}', text)

    def test_report_empty_log(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(dispatch.main(["report", "--format", "json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"rows": [], "tier_picks": {}})
