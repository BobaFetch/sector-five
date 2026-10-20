"""Manifest and role entry-point consistency checks (stdlib only)."""
import json
import os
import re
import unittest

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(PLUGIN)
ROLES = ("planning", "backend", "frontend", "polish", "qa")


def load(*parts):
    with open(os.path.join(*parts)) as fh:
        return json.load(fh)


class ManifestTest(unittest.TestCase):
    def test_versions_agree(self):
        claude = load(PLUGIN, ".claude-plugin", "plugin.json")["version"]
        self.assertEqual(load(PLUGIN, ".codex-plugin", "plugin.json")["version"], claude)
        self.assertEqual(load(PLUGIN, ".muse-plugin", "plugin.json")["version"], claude)
        self.assertEqual(load(REPO, ".claude-plugin", "marketplace.json")["plugins"][0]["version"], claude)

    def test_codex_marketplace_points_at_plugin(self):
        entry = load(REPO, ".agents", "plugins", "marketplace.json")["plugins"][0]
        self.assertEqual(entry["name"], "sector-five")
        self.assertTrue(os.path.isfile(os.path.join(REPO, entry["source"]["path"], ".codex-plugin", "plugin.json")))

    def test_muse_skills_exist(self):
        skills = load(PLUGIN, ".muse-plugin", "plugin.json")["capabilities"]["skills"]
        ids = {s["id"] for s in skills}
        self.assertEqual(ids, {"sector-five", *ROLES})
        for s in skills:
            self.assertTrue(os.path.isfile(os.path.join(PLUGIN, s["path"])), s["path"])


class RoleEntryPointTest(unittest.TestCase):
    def test_each_role_skill_loads_its_sources(self):
        with open(os.path.join(PLUGIN, "contract.md")) as fh:
            headings = set(re.findall(r"^### (.+)$", fh.read(), re.M))
        for role in ROLES:
            skill_dir = os.path.join(PLUGIN, "skills", role)
            with open(os.path.join(skill_dir, "SKILL.md")) as fh:
                body = fh.read()
            self.assertIn("name: %s\n" % role, body)
            self.assertIn("../sector-five/SKILL.md", body)
            self.assertIn("../../grok/%s.md" % role, body)
            self.assertTrue(os.path.isfile(os.path.join(PLUGIN, "grok", role + ".md")))
            heading = re.search(r"`### (.+?)` section", body).group(1)
            self.assertIn(heading, headings, role)


if __name__ == "__main__":
    unittest.main()
