"""The naming standard of README.md ("Naming"), checked on every tracked file."""

import ast
import os
import re
import unittest

from helpers import ROOT

UPPER_SNAKE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*")
LOWER_SNAKE = re.compile(r"_?[a-z][a-z0-9]*(_[a-z0-9]+)*")
LOWER_KEBAB = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
CAP_WORDS = re.compile(r"_?[A-Z][A-Za-z0-9]*")
STANDARD_FILES = {".gitignore", "CHANGELOG.md", "LICENSE", "Makefile", "README.md", "README.tr.md", "SECURITY.md",
                  "THIRD_PARTY.md", "pyproject.toml"}
HIDDEN_DIRECTORIES = {".git", ".githooks", ".mypy_cache", ".ruff_cache"}


def names(directory: str) -> list[str]:
    return sorted(name for name in os.listdir(os.path.join(ROOT, directory)) if name != "__pycache__")


class LayoutTests(unittest.TestCase):
    def test_top_level(self):
        for name in names("."):
            path = os.path.join(ROOT, name)
            if name in HIDDEN_DIRECTORIES:
                continue
            if os.path.isdir(path):
                self.assertRegex(name, UPPER_SNAKE, f"directory {name}")
            else:
                self.assertIn(name, STANDARD_FILES, f"unexpected top-level file {name}")

    def test_project_scripts_tools_and_configuration(self):
        for directory, suffixes in (("SCRIPTS", (".sh", ".py")), ("TOOLS/CI", (".py", ".txt")), ("CONFIG", (".conf",))):
            for name in names(directory):
                stem, suffix = os.path.splitext(name)
                self.assertIn(suffix, suffixes, f"{directory}/{name}")
                self.assertTrue(UPPER_SNAKE.fullmatch(stem), f"{directory}/{name} is not UPPER_SNAKE_CASE")

    def test_installed_commands_and_units(self):
        for name in names("SOURCE/ENTRY"):
            self.assertTrue(LOWER_KEBAB.fullmatch(name), f"SOURCE/ENTRY/{name} is not lower-kebab-case")
        for name in names("SYSTEMD"):
            stem, suffix = os.path.splitext(name)
            self.assertIn(suffix, (".service", ".socket"))
            self.assertTrue(LOWER_KEBAB.fullmatch(stem), f"SYSTEMD/{name}")

    def test_python_modules_and_tests(self):
        for directory in ("SOURCE/head_turn_face_unlock", "TESTS"):
            for name in names(directory):
                stem, suffix = os.path.splitext(name)
                self.assertEqual(suffix, ".py", f"{directory}/{name}")
                self.assertTrue(stem == "__init__" or LOWER_SNAKE.fullmatch(stem), f"{directory}/{name}")

    def test_decision_records(self):
        for name in names("DOCS/ADR"):
            self.assertTrue(name == "README.md" or re.fullmatch(r"\d{4}-[a-z0-9]+(-[a-z0-9]+)*\.md", name),
                            f"DOCS/ADR/{name}")


class CodeTests(unittest.TestCase):
    def test_module_level_names_are_constants_types_or_private(self):
        package = os.path.join(ROOT, "SOURCE", "head_turn_face_unlock")
        for name in names("SOURCE/head_turn_face_unlock"):
            with open(os.path.join(package, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else \
                    [node.target] if isinstance(node, ast.AnnAssign) else []
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.assertTrue(UPPER_SNAKE.fullmatch(target.id) or CAP_WORDS.fullmatch(target.id)
                                        or target.id.startswith("_"), f"{name}: module-level name {target.id}")

    def test_settings_keys(self):
        from head_turn_face_unlock.config import Settings
        for field in Settings.__dataclass_fields__:
            self.assertRegex(field, UPPER_SNAKE)


if __name__ == "__main__":
    unittest.main()
