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
