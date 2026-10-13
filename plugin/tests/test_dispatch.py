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

    def test_expect_mismatch_escalates_even_when_confident(self):
        code, out = self.run_route(route_answers(confidence=0.95),
                                   ["--expect", "qa", "--format", "json"])
        self.assertEqual(code, 3)
        decision = json.loads(out)
        self.assertEqual(decision["verdict"], "mismatch")
        self.assertEqual(decision["expected_role"], "qa")

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
