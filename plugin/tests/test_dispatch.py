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
        open(os.path.join(self.repo, "p.md"), "w").close()  # planLocal needs it on disk
        code, out, _ = self.run_main(["done", "--issue", "ENG-1", "--facts",
                                      self.facts_path, "--format", "json"])
        self.assertEqual(code, 0)
        decision = json.loads(out)
        self.assertEqual(decision["rules"], "defaults")
        self.assertEqual(set(decision["facts"]),
                         {"ci", "reviewThreads", "qa", "draft", "mergeable", "planLocal"})

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
        self.assertEqual(flag["escalations"], 1)  # outcome lines only
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

    # --- re-check of qa fixes (9d6b44d) ---
    def test_minimax_style_names_route_fine(self):
        for ok in ("codex/minimax-m2 @ high", "codex/maximal @ medium",
                   "codex/ultrawide @ low", "codex/m @ maximum"):
            config = tiered_config()
            config["harness"]["tiers"]["backend"]["standard"] = ok
            self.write_config(config)
            code, out, _ = self.route(tier_answers(), ["--no-log"])
            self.assertEqual(code, 0, ok)
            self.assertIn("model: %s (tier standard)" % ok, out)

    def test_true_ultra_max_tokens_still_exit_six(self):
        for bad in ("codex/gpt-6-max @ high", "codex/Ultra_1 @ low",
                    "codex/m.ultra @ x", "codex/m @ MAX"):
            config = tiered_config()
            config["harness"]["tiers"]["backend"]["flagship"] = bad
            self.write_config(config)
            code, _, _ = self.route(tier_answers())
            self.assertEqual(code, 6, bad)

    def test_planning_tier_exit_six(self):
        config = tiered_config()
        config["harness"]["roles"]["planning"] = "codex/p @ high"
        config["harness"]["tiers"]["planning"] = tier_table("planning")
        self.write_config(config)
        code, _, err = self.route(tier_answers())
        self.assertEqual(code, 6)
        code, _, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 6)

    def test_tierless_logs_resolved_tier_null(self):
        self.write_config(make_config(roles={"backend": "codex/pin @ high"}))
        code, out, _ = self.route(tier_answers(tier="flagship"), ["--issue", "E"])
        self.assertIn("model: codex/pin @ high (tier flagship)", out)
        with open(self.log_path()) as fh:
            entry = json.loads(fh.readlines()[-1])
        self.assertIsNone(entry["resolved_tier"])
        self.assertEqual(entry["model"], "codex/pin @ high")
        self.assertFalse(entry["escalated"])
        _, data = self.route_json(tier_answers(tier="flagship"))
        self.assertIsNone(data["resolved_tier"])

    def test_escalations_counted_only_from_outcomes(self):
        self.write_config(tiered_config())
        self.route(tier_answers(tier="flagship"), ["--issue", "E"])  # escalated route
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dispatch.main(["report", "--format", "json"])
        row = json.loads(out.getvalue())["rows"][0]
        self.assertEqual((row["tier"], row["routes"], row["escalations"]), ("flagship", 1, 0))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            dispatch.main(["outcome", "--issue", "E", "--role", "backend", "--model", "m",
                           "--tier", "flagship", "--result", "pass", "--escalated"])
            self.outcome(issue="E", tier="flagship", result="fail")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dispatch.main(["report", "--format", "json"])
        row = json.loads(out.getvalue())["rows"][0]
        self.assertEqual((row["outcomes"], row["escalations"]), (2, 1))


# --- local-evidence (plan 006) step 2: optional evidence config key ---

class EvidenceConfigTest(WorkRepoCase):
    def cfg_path(self):
        return os.path.join(self.meta, "config.json")

    def config_with(self, value):
        config = make_config()
        config["evidence"] = value
        return config

    def route(self):
        with mock.patch.object(dispatch, "ask", return_value=("jev", tier_answers())), \
             mock.patch.object(dispatch, "load_dotenv", lambda: None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = dispatch.main(["route", "--task", "x", "--no-log"])
        return code, out.getvalue(), err.getvalue()

    def test_missing_key_loads_unchanged(self):
        config = make_config()
        self.write_config(config)
        self.assertEqual(dispatch.load_config(self.cfg_path()), config)
        self.assertNotIn("evidence", dispatch.load_config(self.cfg_path()))
        code, out, _ = self.route()
        self.assertEqual(code, 0)
        self.assertIn("model: codex/gpt-6.1-sol @ high", out)
        code, _, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)

    def test_local_loads(self):
        self.write_config(self.config_with("local"))
        self.assertEqual(dispatch.load_config(self.cfg_path())["evidence"], "local")
        self.assertEqual(self.route()[0], 0)
        code, _, _ = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
        self.assertEqual(code, 0)

    def test_bad_values_raise(self):
        for bad in ("branch", "Local", "LOCAL", " local", "", None, 0, 1, True,
                    ["local"], {"mode": "local"}):
            self.write_config(self.config_with(bad))
            with self.assertRaises(ValueError, msg=repr(bad)) as ctx:
                dispatch.load_config(self.cfg_path())
            self.assertEqual(str(ctx.exception), "invalid evidence")

    def test_bad_values_exit_six_check_and_route(self):
        for bad in ("branch", 5, None, ["local"]):
            self.write_config(self.config_with(bad))
            code, out, err = self.run_main(["check"], env=JEV_ENV, probe=OK_PROBE)
            self.assertEqual(code, 6, repr(bad))
            self.assertIn("invalid config.json", err)
            code, out, err = self.route()
            self.assertEqual(code, 6, repr(bad))
            self.assertEqual(out, "")
            self.assertIn("invalid config.json", err)

    def test_explicit_check_config_branch_exit_six(self):
        path = os.path.join(self.repo, "tmp-config.json")
        with open(path, "w") as fh:
            json.dump(self.config_with("branch"), fh)
        code, _, _ = self.run_main(["check", "--config", path], env=JEV_ENV,
                                   probe=OK_PROBE)
        self.assertEqual(code, 6)


# --- concurrency (plan 007) step 2: SECTORFIVE_STATE, locked atomic writes ---

def _concurrent_worker(state, worker, writes):
    """Runs in a child process: real dispatch.main calls against shared state."""
    os.environ["SECTORFIVE_STATE"] = state
    dispatch.load_dotenv = lambda: None
    devnull = open(os.devnull, "w")
    sys.stdout = sys.stderr = devnull
    codes = []
    for n in range(writes):
        codes.append(dispatch.main(["work", "advance", "--issue", "ENG-1", "--to",
                                    "blocked", "--note", "w%d-%d" % (worker, n)]))
        codes.append(dispatch.main(["unrouted", "--task", "w%d-%d" % (worker, n),
                                    "--issue", "ENG-1"]))
    return codes


class StateDirTest(WorkRepoCase):
    def setUp(self):
        super().setUp()
        import tempfile
        self._other = tempfile.TemporaryDirectory()  # a folder with no .sectorfive/
        self.addCleanup(self._other.cleanup)

    def env(self, value):
        return {"SECTORFIVE_STATE": value}

    def test_unset_uses_cwd(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(os.path.realpath(dispatch.state_dir()),
                             os.path.realpath(self.meta))

    def test_set_uses_env_from_any_cwd(self):
        os.chdir(self._other.name)
        with mock.patch.dict(os.environ, self.env(self.meta), clear=True):
            self.assertEqual(dispatch.state_dir(), self.meta)

    def test_relative_value_resolved_to_absolute(self):
        with mock.patch.dict(os.environ, self.env(".sectorfive"), clear=True):
            path = dispatch.state_dir()
        self.assertTrue(os.path.isabs(path))
        self.assertEqual(os.path.realpath(path), os.path.realpath(self.meta))

    def test_missing_path_exits_two(self):
        missing = os.path.join(self.repo, "nope")
        code, out, err = self.run_main(["work", "show"], env=self.env(missing))
        self.assertEqual(code, 2)
        self.assertIn("SECTORFIVE_STATE must be an existing directory: %s" % missing, err)
        # Applies to every command, checked up front in main().
        code, _, _ = self.run_main(["check"], env=dict(JEV_ENV, **self.env(missing)),
                                   probe=OK_PROBE)
        self.assertEqual(code, 2)

    def test_file_not_dir_exits_two(self):
        path = os.path.join(self.repo, "afile")
        open(path, "w").close()
        self.assertEqual(self.run_main(["work", "show"], env=self.env(path))[0], 2)

    def test_empty_value_exits_two_set_but_empty(self):
        os.chdir(self._other.name)
        code, _, err = self.run_main(["work", "show"], env=self.env(""))
        self.assertEqual(code, 2)
        self.assertIn("SECTORFIVE_STATE is set but empty", err)

    def test_new_work_item_mode_follows_umask(self):
        import stat
        path = os.path.join(self.meta, "work", "ENG-1.json")
        for mask, want in ((0o022, 0o644), (0o077, 0o600)):
            if os.path.exists(path):
                os.unlink(path)
            prev = os.umask(mask)
            try:
                code, _, _ = self.run_main(["work", "new", "--issue", "ENG-1",
                                            "--plan", "p.md"])
            finally:
                os.umask(prev)
            self.assertEqual(code, 0)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), want, oct(mask))

    def test_work_set_from_folder_without_sectorfive(self):
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        os.chdir(self._other.name)
        code, _, err = self.run_main(["work", "set", "--issue", "ENG-1", "lane=x"])
        self.assertEqual(code, 2)
        self.assertIn("no .sectorfive/", err)
        code, out, _ = self.run_main(["work", "set", "--issue", "ENG-1", "lane=x"],
                                     env=self.env(self.meta))
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self._other.name, ".sectorfive")))
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            self.assertEqual(json.load(fh)["lane"], "x")
        # The log follows the env var too.
        self.run_main(["unrouted", "--task", "t", "--issue", "ENG-1"],
                      env=self.env(self.meta))
        with open(self.log_path()) as fh:
            self.assertEqual(json.loads(fh.readlines()[-1])["kind"], "unrouted")

    def test_lock_files_ignored_by_show_and_missing_issue_stray_lock(self):
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        code, _, _ = self.run_main(["work", "set", "--issue", "ENG-9", "lane=x"])
        self.assertEqual(code, 2)  # no work item; a stray ENG-9.json.lock is fine
        self.assertFalse(os.path.exists(os.path.join(self.meta, "work", "ENG-9.json")))
        code, out, _ = self.run_main(["work", "show", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual([i["issue"] for i in json.loads(out)], ["ENG-1"])

    def test_atomic_write_failure_leaves_no_temp(self):
        d = os.path.join(self.meta, "work")
        os.makedirs(d)
        target = os.path.join(d, "ENG-1.json")
        with open(target, "w") as fh:
            fh.write("original")
        with mock.patch.object(dispatch.os, "fsync", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                dispatch.atomic_write(target, "new")
        with mock.patch.object(dispatch.os, "replace", side_effect=OSError("rename")):
            with self.assertRaises(OSError):
                dispatch.atomic_write(target, "new")
        self.assertEqual(os.listdir(d), ["ENG-1.json"])
        with open(target) as fh:
            self.assertEqual(fh.read(), "original")
        dispatch.atomic_write(target, "done")
        self.assertEqual(os.listdir(d), ["ENG-1.json"])

    def test_save_work_failure_keeps_item_and_no_temp(self):
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        with mock.patch.object(dispatch.os, "replace", side_effect=OSError("x")):
            with self.assertRaises(OSError):
                self.run_main(["work", "set", "--issue", "ENG-1", "lane=y"])
        d = os.path.join(self.meta, "work")
        self.assertEqual(sorted(n for n in os.listdir(d) if not n.endswith(".lock")),
                         ["ENG-1.json"])
        with open(os.path.join(d, "ENG-1.json")) as fh:
            self.assertIsNone(json.load(fh).get("lane"))

    def test_concurrent_writers_lose_nothing(self):
        import multiprocessing
        workers, writes = 8, 25
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        os.chdir(self._other.name)
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(workers) as pool:
            results = pool.starmap(_concurrent_worker,
                                   [(self.meta, w, writes) for w in range(workers)])
        self.assertTrue(all(code == 0 for codes in results for code in codes))
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            item = json.load(fh)
        notes = [h["note"] for h in item["history"] if h.get("note")]
        want = {"w%d-%d" % (w, n) for w in range(workers) for n in range(writes)}
        self.assertEqual(len(notes), workers * writes)
        self.assertEqual(set(notes), want)
        with open(self.log_path()) as fh:
            lines = [json.loads(line) for line in fh]  # every line valid JSON
        self.assertEqual({e["task"] for e in lines if e["kind"] == "unrouted"}, want)
        self.assertEqual(len(lines), workers * writes)
        leftovers = [n for n in os.listdir(os.path.join(self.meta, "work"))
                     if not n.endswith((".json", ".lock"))]
        self.assertEqual(leftovers, [])
        self.assertFalse(os.path.exists(os.path.join(self._other.name, ".sectorfive")))


# --- orchestrator-hooks (plan 008) step 2: signal, lanes, claim, webhook ---

def _lane_worker(state, worker):
    os.environ["SECTORFIVE_STATE"] = state
    os.environ.pop("SECTORFIVE_WEBHOOK_URL", None)
    dispatch.load_dotenv = lambda: None
    sys.stdout = sys.stderr = open(os.devnull, "w")
    won = dispatch.main(["claim", "--lane", "l%d" % worker, "--issue", "ENG-1"])
    codes = [dispatch.main(["work", "signal", "--lane", "l%d" % worker, "--issue",
                            "ENG-1", "--status", "working", "--note", str(n)])
             for n in range(10)]
    return won, codes


class LaneCase(WorkRepoCase):
    def setUp(self):
        super().setUp()
        self.env = {"SECTORFIVE_STATE": self.meta}
        for issue in ("ENG-1", "ENG-2"):
            self.run_main(["work", "new", "--issue", issue, "--plan", "p.md"],
                          env=self.env)
        self.lanes = os.path.join(self.meta, "lanes")

    def cli(self, *argv, env=None):
        return self.run_main(list(argv), env=dict(self.env, **(env or {})))

    def signal(self, lane="eng-1-qa", issue="ENG-1", status="working", *extra, env=None):
        return self.cli("work", "signal", "--lane", lane, "--issue", issue,
                        "--status", status, *extra, env=env)

    def lanes_json(self, *extra):
        code, out, _ = self.cli("lanes", *extra)
        self.assertEqual(code, 0)
        return json.loads(out)


class LaneHooksTest(LaneCase):
    def test_signal_merges_sets_ts_and_logs_each(self):
        code, out, err = self.signal("eng-1-qa", "ENG-1", "starting", "--objective", "o",
                                     "--pane", "p1")
        self.assertEqual(code, 0)
        self.assertIn("has no claim", err)
        self.assertEqual(json.loads(out)["status"], "starting")
        self.signal("eng-1-qa", "ENG-1", "review", "--sha", "abc")
        with open(os.path.join(self.lanes, "eng-1-qa.json")) as fh:
            rec = json.load(fh)
        self.assertEqual((rec["status"], rec["objective"], rec["pane"], rec["sha"]),
                         ("review", "o", "p1", "abc"))
        self.assertTrue(dispatch.datetime.datetime.fromisoformat(rec["ts"]).tzinfo)
        with open(os.path.join(self.lanes, "eng-1-qa.jsonl")) as fh:
            self.assertEqual([json.loads(l)["status"] for l in fh], ["starting", "review"])

    def test_signal_validation_exits_two(self):
        for lane in ("../x", "X", "_a", ".a", "a/b", "claims", "a.tmp", "b.tmp1", "a" * 65, ""):
            self.assertEqual(self.signal(lane)[0], 2, lane)
            self.assertEqual(self.cli("claim", "--lane", lane or "?", "--issue", "ENG-1")[0], 2)
        with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr", io.StringIO()):
            self.signal(status="bogus")                         # argparse
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.signal(issue="ENG-404")[0], 2)  # no work item
        self.assertEqual(self.cli("claim", "--lane", "a")[0], 2)  # needs --issue
        self.assertFalse(os.path.exists(os.path.join(self.repo, "x.json")))

    def test_signal_for_other_issue_than_claim_exits_two(self):
        self.cli("claim", "--lane", "a", "--issue", "ENG-1")
        self.assertEqual(self.signal("a", "ENG-2")[0], 2)
        code, _, err = self.signal("a", "ENG-1")
        self.assertEqual(code, 0)
        self.assertNotIn("no claim", err)

    def test_lanes_json_stale_and_issue_filter(self):
        self.assertEqual(self.lanes_json(), {"lanes": [], "claims": [], "errors": []})
        self.signal("b-lane", "ENG-2")
        self.signal("a-lane", "ENG-1")
        self.cli("claim", "--lane", "a-lane", "--issue", "ENG-1", "--path", "src")
        path = os.path.join(self.lanes, "b-lane.json")
        with open(path) as fh:
            rec = json.load(fh)
        rec["ts"] = "2000-01-01T00:00:00+00:00"
        with open(path, "w") as fh:
            json.dump(rec, fh)
        open(os.path.join(self.lanes, "tmpabc.json.tmp1"), "w").close()
        data = self.lanes_json()
        self.assertEqual([(l["lane"], l["stale"]) for l in data["lanes"]],
                         [("a-lane", False), ("b-lane", True)])
        self.assertEqual([c["lane"] for c in data["claims"]], ["a-lane"])
        data = self.lanes_json("--issue", "ENG-2")
        self.assertEqual(([l["lane"] for l in data["lanes"]], data["claims"]), (["b-lane"], []))
        self.assertTrue(self.lanes_json("--stale-after", "0")["lanes"][0]["stale"])
        self.assertEqual(self.cli("lanes", "--stale-after", "-1")[0], 2)

    def test_claim_overlaps_exit_three(self):
        self.assertEqual(self.cli("claim", "--lane", "a", "--issue", "ENG-1",
                                  "--path", "src/api", "docs/*.md")[0], 0)
        clashes = (["--issue", "ENG-1"],                              # same issue
                   ["--issue", "ENG-2", "--path", "src/api"],         # equal
                   ["--issue", "ENG-2", "--path", "src/api/x.py"],    # dir prefix
                   ["--issue", "ENG-2", "--path", "src"],             # parent prefix
                   ["--issue", "ENG-2", "--path", "docs/a.md"],       # glob vs literal
                   ["--issue", "ENG-2", "--path", "docs/x*"])         # glob vs glob prefix
        for extra in clashes:
            code, _, err = self.cli("claim", "--lane", "b", *extra)
            self.assertEqual(code, 3, extra)
            self.assertIn("claim conflict: lane a", err)
        for disjoint in ("src/apix", "lib", "README.md", "doc/x.md"):
            self.assertEqual(self.cli("claim", "--lane", "b", "--issue", "ENG-2",
                                      "--path", disjoint)[0], 0, disjoint)
        # re-claim replaces, release drops
        self.cli("claim", "--lane", "a", "--issue", "ENG-1", "--path", "web")
        self.assertEqual([c["paths"] for c in self.lanes_json()["claims"]
                          if c["lane"] == "a"], [["web"]])
        self.assertEqual(self.cli("claim", "--lane", "a", "--release")[0], 0)
        self.assertEqual([c["lane"] for c in self.lanes_json()["claims"]], ["b"])
        self.assertEqual(self.cli("claim", "--lane", "c", "--issue", "ENG-1")[0], 0)
        with open(os.path.join(self.lanes, "a.jsonl")) as fh:
            self.assertEqual([json.loads(l)["kind"] for l in fh],
                             ["claim", "claim", "release"])

    def test_claim_prints_record_and_release_flag(self):
        code, out, _ = self.cli("claim", "--lane", "a", "--issue", "ENG-1", "--path", "src")
        self.assertEqual(code, 0)
        self.assertEqual({k: v for k, v in json.loads(out).items() if k != "ts"},
                         {"lane": "a", "issue": "ENG-1", "paths": ["src"], "kind": "claim"})
        for want in (True, False):
            code, out, _ = self.cli("claim", "--lane", "a", "--release")
            self.assertEqual((code, json.loads(out)["kind"], json.loads(out)["released"]),
                             (0, "release", want))
        with open(os.path.join(self.lanes, "a.jsonl")) as fh:
            self.assertEqual([json.loads(l).get("released") for l in fh], [None, True, False])

    def test_lanes_errors_list_and_corrupt_claims(self):
        self.signal("good")
        for name, text in (("bad.json", "{nope"), ("nots.json", '{"lane": "nots"}'),
                           ("list.json", "[]")):
            with open(os.path.join(self.lanes, name), "w") as fh:
                fh.write(text)
        data = self.lanes_json()
        self.assertEqual([l["lane"] for l in data["lanes"]], ["good"])
        self.assertEqual(sorted(data["errors"]), ["bad.json", "list.json", "nots.json"])
        with open(os.path.join(self.lanes, "claims.json"), "w") as fh:
            fh.write("{nope")
        self.assertEqual(self.cli("lanes")[0], 2)

    def test_eight_process_race(self):
        import multiprocessing
        with multiprocessing.get_context("spawn").Pool(8) as pool:
            results = pool.starmap(_lane_worker, [(self.meta, w) for w in range(8)])
        self.assertEqual(sorted(won for won, _ in results), [0] + [3] * 7)
        self.assertTrue(all(c == 0 for _, codes in results for c in codes))
        lanes = self.lanes_json()["lanes"]
        self.assertEqual([(l["lane"], l["note"]) for l in lanes],
                         [("l%d" % w, "9") for w in range(8)])
        self.assertEqual(len(self.lanes_json()["claims"]), 1)
        for w in range(8):
            with open(os.path.join(self.lanes, "l%d.jsonl" % w)) as fh:
                notes = [json.loads(l).get("note") for l in fh]
            self.assertEqual([n for n in notes if n is not None], [str(n) for n in range(10)])


class WebhookTest(LaneCase):
    def start_server(self, status=200, delay=0):
        import http.server, threading, time
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(delay)
                try:
                    self.send_response(status)
                    self.end_headers()
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/hook?token=SECRET" % server.server_port
        return url, received

    def hook_signal(self, url, timeout="1"):
        import time
        start = time.monotonic()
        code, out, err = self.signal(env={"SECTORFIVE_WEBHOOK_URL": url,
                                          "SECTORFIVE_WEBHOOK_TIMEOUT": timeout})
        self.outputs = getattr(self, "outputs", []) + [out, err]
        return code, err, time.monotonic() - start

    def assert_no_secret(self):
        for text in self.outputs:
            self.assertNotIn("SECRET", text)
            self.assertNotIn("127.0.0.1", text)
        for dp, _, files in os.walk(self.meta):
            for name in files:
                with open(os.path.join(dp, name), errors="replace") as fh:
                    self.assertNotIn("SECRET", fh.read(), name)

    def test_success_posts_record(self):
        url, received = self.start_server()
        code, err, _ = self.hook_signal(url)
        self.assertEqual(code, 0)
        self.assertNotIn("warning: webhook", err)
        self.assertEqual(received[0]["lane"], "eng-1-qa")
        self.assert_no_secret()

    def test_http_500_warns_exit_zero(self):
        url, received = self.start_server(status=500)
        code, err, _ = self.hook_signal(url)
        self.assertEqual(code, 0)
        self.assertIn("warning: webhook HTTPError HTTP 500", err)
        self.assert_no_secret()

    def test_timeout_warns_exit_zero_within_bound(self):
        url, _ = self.start_server(delay=3)
        code, err, elapsed = self.hook_signal(url, timeout="1")
        self.assertEqual(code, 0)
        self.assertIn("warning: webhook", err)
        self.assertLess(elapsed, 2.0)
        with open(os.path.join(self.lanes, "eng-1-qa.json")) as fh:
            self.assertEqual(json.load(fh)["status"], "working")  # state written first
        self.assert_no_secret()

    def test_bad_url_and_unset(self):
        code, err, _ = self.hook_signal("ftp://x/?token=SECRET")
        self.assertEqual(code, 0)
        self.assertIn("warning: webhook ValueError", err)
        code, err, _ = self.hook_signal("")
        self.assertNotIn("webhook", err)
        self.assert_no_secret()


# --- merge-gate (plan 009) step 2: per-PR qa records ---

class QaByPrTest(WorkRepoCase):
    done = DoneCmdTest.done
    write_facts, write_rules = DoneCmdTest.write_facts, DoneCmdTest.write_rules
    LEGACY = {"issue": "ENG-1", "phase": "review", "plan": "p.md", "owner": "frontend",
              "branch": "b", "pr": 12, "history": [],
              "qa": {"verdict": "approve", "sha": "abc123def456", "report": "r.md"}}

    def path(self):
        return os.path.join(self.meta, "work", "ENG-1.json")

    def setUp(self):
        super().setUp()
        self.facts_path = os.path.join(self.repo, "facts.json")
        self.rules_path = os.path.join(self.repo, "rules.json")
        self.write_facts(pr_facts())
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])
        self.run_main(["work", "set", "--issue", "ENG-1", "owner=frontend", "branch=b", "pr=12"])

    def write_item(self, item):
        with open(self.path(), "w") as fh:
            json.dump(item, fh)

    def item(self):
        with open(self.path()) as fh:
            return json.load(fh)

    def qa(self, *pairs, pr=None):
        extra = ["--pr", str(pr)] if pr is not None else []
        return self.run_main(["work", "set", "--issue", "ENG-1", *extra, *pairs])[0]

    def test_legacy_migrates_on_first_pr_write(self):
        self.write_item(self.LEGACY)
        self.assertEqual(self.qa("qa.verdict=approve", "qa.sha=bbb", pr=13), 0)
        qa = self.item()["qa"]
        self.assertEqual(qa["byPr"]["12"], {"verdict": "approve", "sha": "abc123def456",
                                            "report": "r.md"})
        self.assertEqual((qa["byPr"]["13"]["sha"], qa["sha"]), ("bbb", "bbb"))

    def test_read_paths_leave_files_byte_identical(self):
        self.write_item(self.LEGACY)
        with open(self.path(), "rb") as fh:
            before = fh.read()
        for argv in (["work", "show"], ["work", "show", "--format", "json"],
                     ["work", "show", "--issue", "ENG-1"],
                     ["work", "advance", "--issue", "ENG-1", "--to", "done"]):  # refused
            self.run_main(argv)
        with open(self.path(), "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_two_prs_separate_and_mirror_follows_latest_write(self):
        self.qa("qa.verdict=approve", "qa.sha=aaa", "qa.report=a.md", pr=1)
        self.qa("qa.verdict=veto", "qa.sha=bbb", "qa.report=b.md", pr=2)
        qa = self.item()["qa"]
        self.assertEqual((qa["byPr"]["1"]["sha"], qa["byPr"]["2"]["sha"]), ("aaa", "bbb"))
        self.assertEqual((qa["verdict"], qa["sha"], qa["report"]), ("veto", "bbb", "b.md"))
        self.qa("qa.verdict=approve", "qa.sha=aa2", pr=1)  # re-record older PR: mirror follows
        qa = self.item()["qa"]
        self.assertEqual((qa["verdict"], qa["sha"], qa["report"]), ("approve", "aa2", "a.md"))
        self.assertEqual(qa["byPr"]["2"]["verdict"], "veto")
        self.assertTrue(qa["byPr"]["1"]["ts"])

    def test_item_without_pr_does_not_copy_legacy(self):
        self.write_item(dict(self.LEGACY, pr=None))
        self.qa("qa.verdict=approve", "qa.sha=ccc", pr=5)
        qa = self.item()["qa"]
        self.assertEqual(list(qa["byPr"]), ["5"])  # pinned: legacy record not migrated
        self.assertEqual(qa["sha"], "ccc")

    def test_no_pr_anywhere_writes_legacy_only(self):
        self.write_item(dict(self.LEGACY, pr=None, qa={}))
        self.qa("qa.verdict=approve", "qa.sha=ddd")
        self.assertEqual(self.item()["qa"], {"verdict": "approve", "sha": "ddd"})

    def test_bad_pr_exits_two(self):
        for bad in ("0", "-1", "x", "1.5"):
            with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr", io.StringIO()):
                self.qa("qa.verdict=approve", pr=bad)
            self.assertEqual(ctx.exception.code, 2, bad)
        self.assertEqual(self.qa("owner=qa", pr=3), 2)  # --pr needs a qa.* field

    def test_done_and_advance_gate_on_the_items_pr(self):
        self.write_rules({"version": 1, "checks": {"qa": {}}})
        self.qa("qa.verdict=veto", "qa.sha=abc123def456", pr=99)  # other PR, latest write
        self.qa("qa.verdict=approve", "qa.sha=abc123def456", pr=12)
        self.qa("qa.verdict=veto", "qa.sha=zzz", pr=99)            # mirror now says veto
        code, out, _ = self.done()
        self.assertEqual((code, json.loads(out)["facts"]["qa"]["status"]), (0, "pass"))
        for phase in ("building", "review", "open"):
            self.assertEqual(self.run_main(["work", "advance", "--issue", "ENG-1",
                                            "--to", phase])[0], 0)
        self.qa("qa.verdict=veto", "qa.sha=abc123def456", pr=12)
        self.qa("qa.verdict=approve", "qa.sha=abc123def456", pr=99)  # mirror approve
        code, out, _ = self.done()
        self.assertEqual((code, json.loads(out)["facts"]["qa"]["status"]), (4, "fail"))
        code, out, _ = self.run_main(["work", "advance", "--issue", "ENG-1", "--to", "done"])
        self.assertEqual(code, 4)
        self.assertIn("qa", out)
        code, out, _ = self.done("--pr", "99")
        self.assertEqual(json.loads(out)["facts"]["qa"]["status"], "pass")


# --- merge-gate (plan 009) step 4: merge-gate command, offline only ---

def gate_facts(pr=12, sha="abc123def456", **overrides):
    return dict(pr_facts(pr=pr, headSha=sha, changedFiles=["src/a.ts"],
                         comments=[{"author": "coderabbitai", "body": "Score: 4"}],
                         threads=[]), **overrides)


class MergeGateTest(WorkRepoCase):
    def setUp(self):
        super().setUp()
        self.cfg = os.path.join(self.repo, "c.json")
        self.plan = os.path.join(self.repo, "p.md")
        self.facts = os.path.join(self.repo, "f.json")
        self.write_plan("Blast radius: BR2 — new command")
        self.gate_config({"checksGreen": True, "maxThreadSeverity": "minor",
                          "forbiddenPaths": [".github/workflows/**"],
                          "minScore": {"author": "coderabbitai", "min": 4}})
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", self.plan])
        for pr, sha in ((12, "abc123def456"), (11, "base0001")):
            self.run_main(["work", "set", "--issue", "ENG-1", "--pr", str(pr),
                           "qa.verdict=approve", "qa.sha=" + sha])
        self.run_main(["work", "set", "--issue", "ENG-1", "pr=12"])
        no_gh = mock.patch.object(dispatch, "gh", side_effect=AssertionError("gh called"))
        no_run = mock.patch.object(dispatch.subprocess, "run",
                                   side_effect=AssertionError("subprocess called"))
        self.gh = no_gh.start()
        no_run.start()
        self.addCleanup(mock.patch.stopall)

    def write_plan(self, header):
        with open(self.plan, "w") as fh:
            fh.write("# Plan\n%s\n" % header)

    def gate_config(self, gate):
        config = make_config()
        if gate is not None:
            config["mergeGate"] = gate
        with open(self.cfg, "w") as fh:
            json.dump(config, fh)

    def gate(self, facts=None, *extra):
        with open(self.facts, "w") as fh:
            json.dump(facts or gate_facts(), fh)
        code, out, err = self.run_main(["merge-gate", "--issue", "ENG-1", "--facts",
                                        self.facts, "--config", self.cfg,
                                        "--format", "json", *extra])
        self.gh.assert_not_called()
        return code, (json.loads(out) if out else None), err

    def status(self, facts=None, check=None):
        code, data, _ = self.gate(facts)
        return code, data["checks"][check]["status"] if check else data["verdict"]

    def test_pass_logs_and_records(self):
        code, data, _ = self.gate()
        self.assertEqual((code, data["verdict"], data["escalate"]), (0, "pass", False))
        self.assertEqual(set(data["checks"]), {"checks", "score", "qa", "threads",
                                               "forbiddenPaths", "blastRadius"})
        with open(self.log_path()) as fh:
            self.assertEqual(json.loads(fh.readlines()[-1])["kind"], "merge-gate")
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            self.assertEqual(json.load(fh)["mergeGate"]["verdict"], "pass")

    def test_exit_six_and_two(self):
        self.gate_config(None)
        code, _, err = self.gate()
        self.assertEqual(code, 6)
        self.assertIn("no mergeGate config", err)
        for bad in ({"typo": 1}, {"qaPerPr": "yes"}, {"maxThreadSeverity": "huge"},
                    {"minBlastRadius": "BR5"}, {"minScore": {"author": "x"}}):
            self.gate_config(bad)
            self.assertEqual(self.gate()[0], 6, bad)
        self.gate_config({})
        code, _, err = self.run_main(["merge-gate", "--issue", "ENG-1", "--config", self.cfg])
        self.assertEqual(code, 2)
        self.assertIn("no inputs", err)
        self.assertEqual(self.gate(gate_facts(), "--pr", "99")[0], 2)  # facts PR mismatch
        self.assertEqual(self.gate(gate_facts(headSha=""))[0], 2)
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
            self.gate(None, "--pr", "0")

    def test_blast_radius(self):
        for header, want in (("Blast radius: BR0 — prod data", "BR0: escalate"),
                             ("no rating here", "missing"), ("Blast radius: BR5", "missing")):
            self.write_plan(header)
            code, data, _ = self.gate()
            self.assertEqual((code, data["checks"]["blastRadius"]["status"]), (4, "fail"))
            self.assertIn(want, data["checks"]["blastRadius"]["detail"])
            self.assertEqual(data["escalate"], header.startswith("Blast radius: BR0"))
        self.write_plan("Blast radius: BR1 — contract")
        self.gate_config({"minBlastRadius": "BR2"})
        self.assertEqual(self.status(check="blastRadius"), (4, "fail"))
        self.gate_config({"minBlastRadius": "BR1"})
        self.assertEqual(self.status(), (0, "pass"))

    def test_thread_severity(self):
        def thread(body, **kw):
            return gate_facts(threads=[dict({"resolved": False, "outdated": False,
                                             "author": "bot", "body": body}, **kw)])
        for body, want in (("minor: rename", "pass"), ("nit: typo", "pass"),
                           ("P2 badge", "pass"), ("P1 issue", "fail"), ("major bug", "fail"),
                           ("critical!", "fail"), ("looks odd", "fail")):
            self.assertEqual(self.status(thread(body), "threads")[1], want, body)
        self.assertIn("severity unknown", self.gate(thread("looks odd"))[1]
                      ["checks"]["threads"]["detail"])
        self.assertEqual(self.status(thread("critical", resolved=True), "threads")[1], "pass")
        self.assertEqual(self.status(thread("critical", outdated=True), "threads")[1], "pass")

    def test_qa_per_pr_default_on_and_explicit_off(self):
        self.run_main(["work", "set", "--issue", "ENG-1", "--pr", "12", "qa.verdict=veto"])
        self.assertEqual(self.status(check="qa"), (4, "fail"))
        self.gate_config({"qaPerPr": True})
        self.assertEqual(self.status(check="qa"), (4, "fail"))
        self.gate_config({"qaPerPr": False})
        code, data, _ = self.gate()
        self.assertEqual((code, "qa" in data["checks"]), (0, False))

    def test_forbidden_paths_checks_and_score(self):
        cases = ((gate_facts(changedFiles=[".github/workflows/ci.yml"]), "forbiddenPaths", 4, "fail"),
                 (gate_facts(checks=[{"name": "t", "status": "failure"}]), "checks", 4, "fail"),
                 (gate_facts(checks=[{"name": "t", "status": "IN_PROGRESS"}]), "checks", 7, "pending"),
                 (gate_facts(comments=[{"author": "coderabbitai", "body": "Confidence Score: 3"}]),
                  "score", 4, "fail"),
                 (gate_facts(comments=[{"author": "coderabbitai", "body": "Rating 5/5"}]),
                  "score", 7, "pending"))
        for facts, check, code, want in cases:
            self.assertEqual(self.status(facts, check), (code, want), check)

    def test_stack_names_failing_pr(self):
        self.gate_config({"wholeStack": True, "checksGreen": True})
        red = gate_facts(pr=11, sha="base0001", checks=[{"name": "t", "status": "failure"}])
        code, data, _ = self.gate(gate_facts(stack=[red]))
        self.assertEqual((code, data["verdict"]), (4, "fail"))
        self.assertEqual(data["checks"]["stack"]["detail"], "PRs to merge together: #12, #11")
        self.assertEqual([(s["pr"], s["verdict"]) for s in data["stack"]], [(11, "fail")])
        self.assertEqual(data["checks"]["checks"]["status"], "pass")  # top PR itself is green
        code, data, _ = self.gate(gate_facts(stack=[gate_facts(pr=11, sha="base0001")]))
        self.assertEqual(code, 0)
        self.assertEqual(self.gate(gate_facts(stack=[gate_facts(pr=12)]))[0], 2)  # duplicate

    def test_done_pr_rejects_zero_and_negative(self):
        for bad in ("0", "-3"):
            with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr", io.StringIO()):
                self.run_main(["done", "--issue", "ENG-1", "--pr", bad])
            self.assertEqual(ctx.exception.code, 2)

    # --- re-check of 3170df9 ---
    def test_relative_plan_from_worktree_via_state(self):
        import tempfile
        os.makedirs(os.path.join(self.repo, "plans"))
        with open(os.path.join(self.repo, "plans", "rel.md"), "w") as fh:
            fh.write("Blast radius: BR3 — one module\n")
        self.run_main(["work", "set", "--issue", "ENG-1", "plan=plans/rel.md"])
        worktree = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, worktree)
        os.chdir(worktree)
        state = {"SECTORFIVE_STATE": self.meta}
        for extra in ([], ["--plan", "plans/rel.md"]):
            with open(self.facts, "w") as fh:
                json.dump(gate_facts(), fh)
            code, out, _ = self.run_main(["merge-gate", "--issue", "ENG-1", "--facts", self.facts,
                                          "--config", self.cfg, "--format", "json", *extra],
                                         env=state)
            self.assertEqual((code, json.loads(out)["blastRadius"]), (0, "BR3"), extra)

    def test_plan_not_found(self):
        code, data, _ = self.gate(None, "--plan", "nope/missing.md")
        self.assertEqual(code, 4)
        self.assertEqual(data["checks"]["blastRadius"]["detail"], "plan not found: nope/missing.md")

    def test_leading_label_decides_severity(self):
        def body(text):
            return gate_facts(threads=[{"resolved": False, "outdated": False,
                                        "author": "bot", "body": text}])
        for text, want in (("nit: not critical, rename", "pass"), ("**[P3]** critical-ish", "pass"),
                           ("critical: just a nit", "fail"), ("> Major: minor style", "fail")):
            self.assertEqual(self.status(body(text), "threads")[1], want, text)

    def test_no_merge_gate_message_missing_or_absent(self):
        os.unlink(self.cfg)
        for _ in range(2):
            code, _, err = self.gate()
            self.assertEqual(code, 6)
            self.assertIn("no mergeGate config", err)
            self.gate_config(None)


# --- small-ports (plan 010) PR A: help, no stdin, plan default, planLocal ---

class SmallPortsTest(WorkRepoCase):
    def test_gh_runs_without_stdin_or_prompts(self):
        done = mock.Mock(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(dispatch.subprocess, "run", return_value=done) as run, \
             mock.patch("builtins.input", side_effect=AssertionError("input called")):
            self.assertEqual(dispatch.gh("pr", "view", "1"), "{}")
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["stdin"], dispatch.subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(kwargs["env"]["PATH"], os.environ["PATH"])  # rest of env kept
        with open(dispatch.__file__) as fh:
            source = fh.read()
        self.assertNotIn("input(", source)
        self.assertNotIn("sys.stdin", source)

    def plans(self, *names):
        d = os.path.join(self.meta, "plans")
        os.makedirs(d, exist_ok=True)
        for name in names:
            open(os.path.join(d, name), "w").close()

    def new(self, *extra):
        code, _, err = self.run_main(["work", "new", "--issue", "ENG-1", *extra])
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            return code, json.load(fh)["plan"], err

    def test_plan_autodetect_none(self):
        code, plan, err = self.new()
        self.assertEqual((code, plan), (0, None))
        self.assertIn("found 0 plans matching ENG-1", err)

    def test_plan_autodetect_one_case_insensitive(self):
        self.plans("010-eng-1-thing.md", "011-other.md", "eng-1-notes.txt")
        code, plan, err = self.new()
        self.assertEqual((code, plan), (0, os.path.join(".sectorfive", "plans", "010-eng-1-thing.md")))
        self.assertEqual(err, "")

    def test_plan_autodetect_many_and_override(self):
        self.plans("a-ENG-1.md", "b-eng-1.md")
        code, plan, err = self.new()
        self.assertEqual(plan, None)
        self.assertIn("found 2 plans", err)
        os.unlink(os.path.join(self.meta, "work", "ENG-1.json"))
        code, plan, err = self.new("--plan", "mine.md")
        self.assertEqual((code, plan, err), (0, "mine.md", ""))

    def done(self, env=None):
        facts = os.path.join(self.repo, "facts.json")
        with open(facts, "w") as fh:
            json.dump(pr_facts(), fh)
        code, out, _ = self.run_main(["done", "--issue", "ENG-1", "--facts", facts,
                                      "--format", "json", "--no-log"], env=env)
        return code, json.loads(out)["facts"].get("planLocal")

    def ready(self, plan):
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", plan])
        self.run_main(["work", "set", "--issue", "ENG-1", "owner=frontend", "branch=b",
                       "pr=12", "qa.verdict=approve", "qa.sha=abc123def456"])

    def test_plan_local_pass_fail_worktree_and_opt_out(self):
        self.ready(".sectorfive/plans/eng-1.md")
        code, result = self.done()
        self.assertEqual((code, result["status"], result["detail"]),
                         (4, "fail", "plan not found on disk"))
        self.plans("eng-1.md")
        self.assertEqual(self.done(), (0, {"status": "pass",
                                           "detail": ".sectorfive/plans/eng-1.md"}))
        import tempfile, shutil
        worktree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, worktree)
        os.chdir(worktree)  # relative plan resolves against the state checkout
        self.assertEqual(self.done({"SECTORFIVE_STATE": self.meta})[1]["status"], "pass")
        os.chdir(self.repo)
        with open(os.path.join(self.meta, "done.json"), "w") as fh:
            json.dump({"version": 1, "checks": {"qa": {}}}, fh)
        os.unlink(os.path.join(self.meta, "plans", "eng-1.md"))
        self.assertEqual(self.done(), (0, None))

    def test_plan_local_no_plan_recorded(self):
        self.run_main(["work", "new", "--issue", "ENG-1"])
        self.run_main(["work", "set", "--issue", "ENG-1", "pr=12"])
        self.assertEqual(self.done()[1], {"status": "fail", "detail": "no plan recorded"})

    def test_help_describes_every_option(self):
        parser = dispatch.build_parser()
        subs = {a.dest: a for a in parser._actions if hasattr(a, "choices") and a.choices}
        commands = subs["command"].choices
        work = {a.dest: a for a in commands["work"]._actions if getattr(a, "choices", None)}
        for name, sub in (("merge-gate", commands["merge-gate"]), ("lanes", commands["lanes"]),
                          ("claim", commands["claim"]),
                          ("work signal", work["action"].choices["signal"])):
            for action in sub._actions:
                if action.option_strings and action.dest != "help":
                    self.assertTrue(action.help, "%s %s" % (name, action.option_strings))
                    self.assertIn(action.help.split("(")[0].strip()[:20], " ".join(
                        sub.format_help().split()), name)

    # --- re-check of 38df08a ---
    def test_plan_autodetect_word_boundary_table(self):
        import shutil
        for name, hit in (("eng-1-x.md", True), ("eng-1.md", True), ("010-ENG-1-upper.md", True),
                          ("eng-12-x.md", False), ("eng-10.md", False), ("eng-1b.md", False),
                          ("xeng-1.md", False)):
            shutil.rmtree(os.path.join(self.meta, "plans"), ignore_errors=True)
            shutil.rmtree(os.path.join(self.meta, "work"), ignore_errors=True)
            self.plans(name)
            want = os.path.join(".sectorfive", "plans", name) if hit else None
            self.assertEqual(self.new()[1], want, name)

    def test_eng1_does_not_take_eng12_plan(self):
        self.plans("eng-12-x.md")
        code, plan, err = self.new()
        self.assertEqual((code, plan), (0, None))
        self.assertIn("found 0 plans", err)

    def test_gh_env_disables_git_prompt(self):
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(dispatch.subprocess, "run", return_value=done) as run:
            dispatch.gh("api", "x")
        env = run.call_args.kwargs["env"]
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GH_PROMPT_DISABLED"]), ("0", "1"))


# --- small-ports (plan 010) PR A2: pane rename, prs, merged per PR, gate stack pending ---

class PaneRenameTest(LaneCase):
    def rename(self, *extra, herdr="1", returncode=0, error=None, lane="eng-1-qa"):
        proc = mock.Mock(returncode=returncode, stdout="", stderr="")
        run = mock.Mock(side_effect=error) if error else mock.Mock(return_value=proc)
        env = {"HERDR_ENV": herdr} if herdr else {}
        with mock.patch.object(dispatch.subprocess, "run", run):
            code, _, err = self.signal(lane, "ENG-1", "working", *extra, env=env)
        return code, run, err

    def label(self, pane="p1", lane="eng-1-qa"):
        with open(os.path.join(self.meta, "lanes", lane + ".json")) as fh:
            return json.load(fh).get("paneLabels", {}).get(pane)

    def test_renames_with_issue_and_objective_capped(self):
        code, run, _ = self.rename("--pane", "p1", "--objective", "x" * 80)
        self.assertEqual(code, 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["herdr", "pane", "rename", "p1"])
        self.assertEqual(argv[4], ("ENG-1 · " + "x" * 80)[:48])
        self.assertEqual(len(argv[4]), 48)
        self.assertIs(run.call_args.kwargs["stdin"], dispatch.subprocess.DEVNULL)
        self.assertTrue(0 < run.call_args.kwargs["timeout"] <= 5)
        code, run, _ = self.rename("--pane", "p1")  # same label (objective kept): no rename
        run.assert_not_called()

    def test_label_change_renames_and_failure_not_recorded(self):
        label = self.label
        self.rename("--pane", "p1", "--objective", "a")
        self.assertEqual(label(), "ENG-1 · a")
        run = self.rename("--pane", "p1", "--objective", "b")[1]
        self.assertEqual(run.call_args.args[0][4], "ENG-1 · b")
        self.assertEqual(self.rename("--pane", "p1", "--objective", "c", returncode=1)[0], 0)
        self.assertEqual(label(), "ENG-1 · b")  # failed rename not recorded
        self.rename("--pane", "p1", "--objective", "d", error=FileNotFoundError())
        self.assertEqual(label(), "ENG-1 · b")
        run = self.rename("--pane", "p1", "--objective", "c")[1]  # retried after failure
        run.assert_called_once()

    def test_new_pane_and_lanes_rename_independently(self):
        self.rename("--pane", "p1", "--objective", "a")
        run = self.rename("--pane", "p2")[1]  # same label, new pane
        self.assertEqual(run.call_args.args[0][3:], ["p2", "ENG-1 · a"])
        self.assertEqual((self.label("p1"), self.label("p2")), ("ENG-1 · a",) * 2)
        run = self.rename("--pane", "p3", "--objective", "a", lane="eng-1-fe")[1]
        run.assert_called_once()
        self.rename("--pane", "p1")[1].assert_not_called()  # qa lane unaffected by fe lane
        self.assertEqual(self.label("p3", "eng-1-fe"), "ENG-1 · a")
        self.assertIsNone(self.label("p3"))
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            self.assertNotIn("paneLabel", json.load(fh))
        self.assertEqual(self.lanes_json()["lanes"][0]["status"], "working")

    def test_rename_errors_only_warn(self):
        for err in (FileNotFoundError(), dispatch.subprocess.TimeoutExpired("herdr", 2),
                    ValueError("x"), RuntimeError("x")):
            code, _, out = self.rename("--pane", "p1", "--objective", repr(err), error=err)
            self.assertEqual(code, 0, err)
            self.assertIn("warning: herdr pane rename: " + type(err).__name__, out)
        self.assertIsNone(self.label())

    def test_lane_name_without_objective(self):
        self.assertEqual(self.rename("--pane", "p1")[1].call_args.args[0][4], "eng-1-qa")

    def test_skipped_outside_herdr_without_pane_or_no_rename(self):
        for extra, herdr in ((["--pane", "p1"], None), (["--pane", "p1"], "0"), ([], "1"),
                             (["--pane", "p1", "--no-rename"], "1")):
            code, run, _ = self.rename(*extra, herdr=herdr)
            self.assertEqual(code, 0)
            run.assert_not_called()

    def test_failed_rename_only_warns(self):
        code, _, err = self.rename("--pane", "p1", returncode=1)
        self.assertEqual(code, 0)
        self.assertIn("warning: herdr pane rename exited 1", err)
        for error in (FileNotFoundError(), dispatch.subprocess.TimeoutExpired("herdr", 2)):
            code, _, err = self.rename("--pane", "p1", error=error)
            self.assertEqual(code, 0)
            self.assertIn("warning: herdr pane rename: %s" % type(error).__name__, err)


class PrsMergedTest(WorkRepoCase):
    def setUp(self):
        super().setUp()
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])

    def item(self):
        with open(os.path.join(self.meta, "work", "ENG-1.json")) as fh:
            return json.load(fh)

    def test_set_prs_and_bad_values(self):
        code, out, _ = self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "20, 21"])
        self.assertEqual((code, self.item()["prs"]), (0, [20, 21]))
        self.assertIn("updated prs", out)
        for bad in ("0", "-1", "a", "20,", "", "1.5"):
            with self.assertRaises(SystemExit) as ctx, mock.patch("sys.stderr", io.StringIO()):
                self.run_main(["work", "set", "--issue", "ENG-1", "--prs", bad])
            self.assertEqual(ctx.exception.code, 2, bad)
        self.assertEqual(self.run_main(["work", "set", "--issue", "ENG-1"])[0], 2)
        self.assertEqual(self.item()["prs"], [20, 21])

    def merged_problems(self, by_pr, legacy=None):
        qa = dict(legacy or {}, byPr={str(k): {"verdict": "approve", "sha": "s%d" % k}
                                      for k in by_pr})
        item = {"issue": "ENG-1", "phase": "done", "plan": "p.md", "owner": "qa",
                "branch": "b", "pr": 21, "prs": [20, 21], "qa": qa,
                "done": {"verdict": "done", "sha": "s21"}}
        return [p for p in dispatch.transition_problems(item, "merged") if "for PR" in p]

    def test_merged_reads_each_listed_pr(self):
        self.assertEqual(self.merged_problems([1, 20, 21]), [])
        problems = self.merged_problems([1, 20])  # PR 1 placeholder ignored
        self.assertEqual(len(problems), 1)
        self.assertIn("for PR 21", problems[0])
        legacy = {"verdict": "approve", "sha": "s21"}  # top-level record must not count
        self.assertIn("for PR 21", self.merged_problems([20], legacy)[0])

    def test_merged_cli_exit_four(self):
        item = self.item()
        item.update(phase="done", owner="qa", branch="b", pr=21, prs=[20, 21],
                    qa={"verdict": "approve", "sha": "s21", "byPr": {
                        "1": {"verdict": "approve", "sha": "s1"},
                        "20": {"verdict": "approve", "sha": "s20"}}},
                    done={"verdict": "done", "sha": "s21"})
        with open(os.path.join(self.meta, "work", "ENG-1.json"), "w") as fh:
            json.dump(item, fh)
        code, out, _ = self.run_main(["work", "advance", "--issue", "ENG-1", "--to", "merged"])
        self.assertEqual(code, 4)
        self.assertIn("for PR 21", out)


def _gate_pending_without_stack_facts(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "11,12"])
    for whole in (False, True):
        self.gate_config({"wholeStack": whole, "checksGreen": True})
        code, data, _ = self.gate()
        self.assertEqual((code, data["verdict"], data["checks"]["stack"]["status"]),
                         (7, "pending", "pending"), whole)
        self.assertIn("stack facts missing", data["checks"]["stack"]["detail"])
        red = gate_facts(checks=[{"name": "t", "status": "failure"}])
        self.assertEqual(self.gate(red)[0], 4, whole)  # fail beats pending
    lower = gate_facts(pr=11, sha="base0001")
    self.assertEqual(self.gate(gate_facts(stack=[lower]))[0], 0)
    red_lower = gate_facts(pr=11, sha="base0001", checks=[{"name": "t", "status": "failure"}])
    self.assertEqual(self.gate(gate_facts(stack=[red_lower]))[0], 4)


MergeGateTest.test_pending_without_stack_facts = _gate_pending_without_stack_facts


class PrsRulingsTest(WorkRepoCase):
    item = PrsMergedTest.item

    def setUp(self):
        super().setUp()
        self.run_main(["work", "new", "--issue", "ENG-1", "--plan", "p.md"])

    def test_pr_appends_without_duplicates(self):
        for pr in ("20", "21", "20"):
            self.run_main(["work", "set", "--issue", "ENG-1", "pr=" + pr])
        self.assertEqual((self.item()["pr"], self.item()["prs"]), (20, [20, 21]))
        self.run_main(["work", "set", "--issue", "ENG-1", "pr="])  # clearing doesn't append
        self.assertEqual(self.item()["prs"], [20, 21])

    def test_prs_dedupes_and_adds_items_pr(self):
        self.run_main(["work", "set", "--issue", "ENG-1", "pr=12"])
        self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "20,21,20"])
        self.assertEqual(self.item()["prs"], [20, 21, 12])

    def test_legacy_item_without_prs_unchanged(self):
        self.run_main(["work", "set", "--issue", "ENG-1", "owner=qa", "qa.verdict=approve"])
        self.assertNotIn("prs", self.item())
        item = {"issue": "ENG-1", "phase": "done", "plan": "p.md", "owner": "qa", "branch": "b",
                "pr": 21, "qa": {"verdict": "approve", "sha": "s21"},
                "done": {"verdict": "done", "sha": "s21"}}
        self.assertEqual(dispatch.transition_problems(item, "merged"), [])

    def test_done_sha_mismatch_refuses_merged(self):
        item = {"issue": "ENG-1", "phase": "done", "plan": "p.md", "owner": "qa", "branch": "b",
                "pr": 21, "prs": [20, 21], "qa": {"byPr": {
                    "20": {"verdict": "approve", "sha": "s20"},
                    "21": {"verdict": "approve", "sha": "s21"}}},
                "done": {"verdict": "done", "sha": "other"}}
        problems = dispatch.transition_problems(item, "merged")
        self.assertTrue(any("done check ran on other" in p for p in problems), problems)
        item["done"]["sha"] = "s21"
        self.assertEqual(dispatch.transition_problems(item, "merged"), [])


def _gate_multi_pr_rulings(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "11,12"])
    self.gate_config({"wholeStack": False, "checksGreen": True})
    red = gate_facts(pr=11, sha="base0001", checks=[{"name": "t", "status": "failure"}])
    code, data, _ = self.gate(gate_facts(stack=[red]))  # the original :1705 probe
    self.assertEqual((code, data["verdict"]), (4, "fail"))
    self.assertEqual([(s["pr"], s["verdict"]) for s in data["stack"]], [(11, "fail")])
    self.assertEqual(self.gate()[0], 7)  # no stack facts
    self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "10,11,12"])
    self.run_main(["work", "set", "--issue", "ENG-1", "--pr", "10", "qa.verdict=approve",
                   "qa.sha=x"])
    code, data, _ = self.gate(gate_facts(stack=[gate_facts(pr=11, sha="base0001")]))
    self.assertEqual((code, data["checks"]["stack"]["detail"]), (7, "stack missing PR 10"))


MergeGateTest.test_multi_pr_rulings = _gate_multi_pr_rulings


# --- small-ports (plan 010) PR A3: open QA findings count as review threads ---

class QaFindingsParseTest(WorkRepoCase):
    def threads(self, text, newline="\n"):
        with open("r.md", "w", newline="") as fh:
            fh.write(text.replace("\n", newline))
        item = {"pr": 12, "qa": {"verdict": "approve", "sha": "s", "report": "r.md"}}
        with mock.patch("sys.stderr", io.StringIO()) as err:
            got = dispatch.qa_finding_threads(item, 12)
        return [(t["body"].lower(), t["qa"]) for t in got], err.getvalue()

    def test_parsing_table(self):
        cases = [
            ("- [major] a.py:1 — x (open)", [("major", "qa: a.py:1")]),
            ("- [Critical] a.py:1 – x (OPEN)", [("critical", "qa: a.py:1")]),
            ("- [nit] a/b.py:22 - x - y (open)", [("nit", "qa: a/b.py:22")]),
            ("- [P0] a.py:1 — x (open)", [("p0", "qa: a.py:1")]),
            ("- [p3] a.py:1 — x (open)", [("p3", "qa: a.py:1")]),
            ("- [major] a.py:1 — x (fixed)", []),
            ("- [major] a.py:1 — x (wontfix)", []),
            ("- [major] a.py:1 — x", [("major", "qa: malformed")]),  # missing status
            ("- [major] a.py — x (open)", [("major", "qa: malformed")]),  # no line
            ("- [blocker] a.py:1 — x (open)", [("major", "qa: malformed")]),
            ("- [major] a.py:1 x (open)", [("major", "qa: malformed")]),  # no separator
            ("- [major] a.py:1 — x (closed)", [("major", "qa: malformed")]),
            ("Prose mentioning [major] a.py:1 — x (open).", []),
            ("* [major] a.py:1 — x (open)", []),
        ]
        for line, want in cases:
            self.assertEqual(self.threads("# R\n## Findings\n%s\n" % line)[0], want, line)

    def test_section_bounds_and_line_endings(self):
        open_line = "- [major] a.py:1 — x (open)"
        before = "- [major] z.py:1 — x (open)\n## Findings\n"
        self.assertEqual(self.threads(before + "- [nit] b.py:2 — y (open)\n## Next\n"
                                      + open_line + "\n")[0], [("nit", "qa: b.py:2")])
        crlf, _ = self.threads("## Findings\n" + open_line + "\n", newline="\r\n")
        self.assertEqual(crlf, [("major", "qa: a.py:1")])
        self.assertEqual(self.threads("## Findings\n\n" + open_line)[0][0][1], "qa: a.py:1")

    def test_malformed_warns_on_stderr(self):
        got, err = self.threads("## Findings\n- nope\n")
        self.assertEqual(got, [("major", "qa: malformed")])
        self.assertIn("warning: malformed QA finding in r.md:2", err)

    def test_unreadable_and_absent_report(self):
        item = {"pr": 12, "qa": {"verdict": "approve", "sha": "s", "report": "gone.md"}}
        self.assertEqual(dispatch.qa_finding_threads(item, 12),
                         [{"body": "major", "qa": "qa: report unreadable"}])
        with open("bin.md", "wb") as fh:
            fh.write(b"## Findings\n\xff\xfe\n")
        item["qa"]["report"] = "bin.md"
        self.assertEqual(dispatch.qa_finding_threads(item, 12)[0]["qa"], "qa: report unreadable")
        item["qa"]["report"] = None
        self.assertEqual(dispatch.qa_finding_threads(item, 12), [])


def _write_report(path, *lines):
    with open(path, "w") as fh:
        fh.write("# QA\n## Findings\n" + "".join(l + "\n" for l in lines))


def _gate_qa_findings_threshold(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "pr=12", "qa.verdict=approve",
                   "qa.sha=abc123def456", "qa.report=r.md"])
    for lines, code in ((["- [major] a.py:1 — x (open)"], 4),
                        (["- [minor] a.py:1 — x (open)", "- [nit] b.py:2 — y (open)"], 0),
                        (["- [critical] a.py:1 — x (fixed)", "- [P1] b.py:2 - y (wontfix)"], 0),
                        (["- [P1] b.py:2 - y (open)"], 4)):
        _write_report("r.md", *lines)
        got, data, _ = self.gate()
        self.assertEqual((got, data["checks"]["threads"]["status"]), (code, "pass" if code == 0
                                                                     else "fail"), lines)
    _write_report("r.md", "- [major] a.py:1 — x (open)")
    self.assertIn("qa: a.py:1", self.gate()[1]["checks"]["threads"]["detail"])
    _write_report("r.md", "- garbage")
    code, data, err = self.gate()
    self.assertEqual((code, data["checks"]["threads"]["detail"]), (4, "qa: malformed"))
    self.assertIn("malformed QA finding", err)
    os.remove("r.md")
    self.assertEqual(self.gate()[1]["checks"]["threads"]["detail"], "qa: report unreadable")
    self.gate_config({"checksGreen": True})  # findings only checked with maxThreadSeverity
    self.assertEqual(self.gate()[0], 0)


def _gate_qa_findings_with_gh_threads(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "pr=12", "qa.verdict=approve",
                   "qa.sha=abc123def456", "qa.report=r.md"])
    _write_report("r.md", "- [major] a.py:1 — x (open)")
    threads = [{"body": "P1: bad", "resolved": False}, {"body": "critical", "resolved": True}]
    code, data, _ = self.gate(gate_facts(threads=threads))
    self.assertEqual((code, data["checks"]["threads"]["detail"]), (4, "major, qa: a.py:1"))
    _write_report("r.md", "- [nit] a.py:1 — x (open)")
    self.assertEqual(self.gate(gate_facts(threads=threads[1:]))[0], 0)


def _gate_stack_reads_own_report_only(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "--prs", "11,12"])
    self.run_main(["work", "set", "--issue", "ENG-1", "pr=12", "qa.verdict=approve",
                   "qa.sha=abc123def456", "qa.report=top.md"])
    _write_report("top.md", "- [major] top.py:1 — x (open)")
    self.gate_config({"checksGreen": True, "maxThreadSeverity": "minor"})
    lower = gate_facts(pr=11, sha="base0001")
    code, data, _ = self.gate(gate_facts(stack=[lower]))
    self.assertEqual((code, data["checks"]["threads"]["detail"]), (4, "qa: top.py:1"))
    # PR 11 has no record of its own: it must not inherit top.md
    self.assertEqual([s["checks"]["threads"]["status"] for s in data["stack"]], ["pass"])
    _write_report("top.md")
    self.run_main(["work", "set", "--issue", "ENG-1", "--pr", "11", "qa.verdict=approve",
                   "qa.sha=base0001", "qa.report=low.md"])
    _write_report("low.md", "- [major] low.py:3 — x (open)")
    code, data, _ = self.gate(gate_facts(stack=[lower]))
    self.assertEqual((code, data["stack"][0]["checks"]["threads"]["detail"]),
                     (4, "qa: low.py:3"))
    self.assertEqual(data["checks"]["threads"]["status"], "pass")


MergeGateTest.test_qa_findings_threshold = _gate_qa_findings_threshold
MergeGateTest.test_qa_findings_with_gh_threads = _gate_qa_findings_with_gh_threads
MergeGateTest.test_stack_reads_own_report_only = _gate_stack_reads_own_report_only


class QaFindingsParseFixTest(WorkRepoCase):
    """A3 re-check: fences, heading case/level, section end, sub-bullets, ranges."""
    L = "- [major] a.py:1 — x (open)\n"
    threads = QaFindingsParseTest.threads

    def test_fixes_table(self):
        L, ex = self.L, "- [major] e.py:9 — example (open)\n"
        cases = [
            ("## Findings\n```\n## x\n" + ex + "```\n" + L, ["qa: a.py:1"]),
            ("## Findings\n```md\n" + ex + "```\n" + L, ["qa: a.py:1"]),
            ("## Findings\n~~~\n## x\n" + ex + "~~~\n" + L, ["qa: a.py:1"]),
            ("## findings\n" + L, ["qa: a.py:1"]),
            ("### FINDINGS (re-check 2)\n" + L, ["qa: a.py:1"]),
            ("#### Findings\n" + L, []),
            ("## Findings\n" + L + "# Next\n" + ex, ["qa: a.py:1"]),
            ("## Findings\n" + L + "## Next\n" + ex, ["qa: a.py:1"]),
            ("### Findings\n" + L + "## Next\n" + ex, ["qa: a.py:1"]),
            ("## Findings\n#### Sub\n" + L, ["qa: a.py:1"]),
            ("## Findings\n" + L + "  - detail\n    more text\n", ["qa: a.py:1"]),
            ("## Findings\n- [nit] a.py:1-3 — x (open)\n", ["qa: a.py:1-3"]),
        ]
        for text, want in cases:
            got, err = self.threads(text)
            self.assertEqual([q for _, q in got], want, text)
            self.assertNotIn("malformed", err, text)

    def test_no_section_only_warns(self):
        got, err = self.threads("# QA\nall good\n")
        self.assertEqual(got, [])
        self.assertIn("warning: no Findings section in QA report r.md", err)
        self.assertNotIn("no Findings", self.threads("## Findings\n")[1])

    def test_unclosed_fence_counts_once_anywhere(self):
        L = self.L
        for text, line in (("```\n## Findings\n" + L, 1),
                           ("## Findings\n" + L + "```\n" + L + "```\n```py\n", 6),
                           ("## Findings\n" + L + "## Next\n~~~\n", 4),
                           ("# QA\n\n```\nno section\n", 3)):
            got, err = self.threads(text)
            self.assertEqual([q for _, q in got].count("qa: unclosed fence"), 1, text)
            self.assertIn(("major", "qa: unclosed fence"), got)
            self.assertIn("unclosed code fence in r.md:%d" % line, err)
        self.assertEqual(self.threads("## Findings\n```\n" + L + "```\n")[0], [])


def _gate_unclosed_fence_exits_four(self):
    self.run_main(["work", "set", "--issue", "ENG-1", "pr=12", "qa.verdict=approve",
                   "qa.sha=abc123def456", "qa.report=r.md"])
    _write_report("r.md", "- [nit] a.py:1 — x (open)", "```")
    code, data, err = self.gate()
    self.assertEqual((code, data["checks"]["threads"]["detail"]), (4, "qa: unclosed fence"))
    self.assertIn("unclosed code fence", err)


MergeGateTest.test_unclosed_fence_exits_four = _gate_unclosed_fence_exits_four


class DoneRelativeReportTest(WorkRepoCase):
    def test_relative_report_resolved_against_state_checkout(self):
        os.makedirs(os.path.join(self.repo, "ev"))
        with open(os.path.join(self.repo, "ev", "r.md"), "w") as fh:
            fh.write("## Findings\nREPORT-BODY\n")
        item = {"pr": 12, "qa": {"verdict": "approve", "sha": "s", "report": "ev/r.md"}}
        other = os.path.join(self.repo, "elsewhere")
        os.mkdir(other)
        os.chdir(other)
        with mock.patch.dict(os.environ, {"SECTORFIVE_STATE": self.meta}):
            state = dispatch.judgment_state("ENG-1", {"pr": 12}, item, ["qa"], 10000)
            self.assertEqual(dispatch.qa_finding_threads(item, 12), [])
        self.assertIn("--- qa report ---\n## Findings\nREPORT-BODY", state)
