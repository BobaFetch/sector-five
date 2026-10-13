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


if __name__ == "__main__":
    unittest.main()
