"""The documents say what the code does: every table that mirrors code is checked against it,
so a change to the code that forgets its documentation fails the tests."""

import ast
import dataclasses
import os
import re
import unittest

from helpers import ROOT

from head_turn_face_unlock import VERSION, messages
from head_turn_face_unlock.config import Settings
from head_turn_face_unlock.model_files import BUNDLE_MEMBERS, DOWNLOADS, RUNTIME_VERSION, RUNTIME_WHEELS

READMES = ("README.md", "README.tr.md")


def read(name: str) -> str:
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return match.group(1) if match else ""


class ReadmeTests(unittest.TestCase):
    def test_the_settings_tables_list_every_setting_with_its_default(self):
        defaults = Settings()
        for readme, heading in (("README.md", "Settings"), ("README.tr.md", "Ayarlar")):
            table = section(read(readme), heading)
            listed = dict(re.findall(r"^\| `([A-Z_]+)`(?:, `[A-Z_]+`)? \| `([^`]+)` \|", table, re.M))
            for line in re.findall(r"^\| `([A-Z_]+)`, `([A-Z_]+)` \| `([^`]+)` \|", table, re.M):
                listed[line[1]] = line[2]
            for field in dataclasses.fields(Settings):
                self.assertIn(field.name, listed, f"{readme} does not list {field.name}")
                value = getattr(defaults, field.name)
                expected = f"{value:.2f}" if isinstance(value, float) and value < 1 else \
                    f"{value:g}" if isinstance(value, float) else value
                self.assertEqual(listed[field.name], expected, f"{readme}: default of {field.name}")

    def test_the_test_tables_list_every_test_file(self):
        files = sorted(name for name in os.listdir(os.path.join(ROOT, "TESTS")) if name.startswith("test_"))
        for readme, heading in (("README.md", "Tests"), ("README.tr.md", "Testler")):
            table = section(read(readme), heading)
            for name in files:
                self.assertIn(f"`{name}`", table, f"{readme} does not describe {name}")

    def test_the_makefile_help_lists_every_target(self):
        makefile = read("Makefile")
        targets = re.search(r"^\.PHONY: (.*)$", makefile, re.M).group(1).split()
        documented = re.findall(r"^#   make ([a-z-]+)", makefile, re.M)
        self.assertEqual(sorted(targets), sorted(documented))


class ThirdPartyTests(unittest.TestCase):
    def test_every_downloaded_file_is_listed_with_its_size_and_checksum(self):
        text = read("THIRD_PARTY.md")
        for name, (_, size, digest) in DOWNLOADS.items():
            self.assertIn(f"`{name}` | {size} | `{digest}`", text)
        for name, (size, digest) in BUNDLE_MEMBERS.items():
            self.assertIn(f"`{name}` | {size} | `{digest}`", text)
        self.assertIn(f"LiteRT {RUNTIME_VERSION}", text)
        for (tag, machine), (_, size, digest) in RUNTIME_WHEELS.items():
            self.assertIn(f"| {tag[2]}.{tag[3:]} | {machine} | {size} | `{digest}` |", text)


class ChangelogTests(unittest.TestCase):
    def test_the_current_version_has_an_entry(self):
        self.assertRegex(read("CHANGELOG.md"), rf"(?m)^## {re.escape(VERSION)} · \d{{4}}-\d{{2}}-\d{{2}}$")


class MessageTests(unittest.TestCase):
    def test_both_languages_have_the_same_messages(self):
        self.assertEqual(set(messages._TEXT["en"]), set(messages._TEXT["tr"]))
        for language in messages._TEXT.values():
            self.assertTrue(all(text.strip() for text in language.values()))

    def test_every_key_the_code_uses_has_a_message(self):
        package = os.path.join(ROOT, "SOURCE", "head_turn_face_unlock")
        used = set()
        for name in os.listdir(package):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(read(os.path.join("SOURCE", "head_turn_face_unlock", name)))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in (
                        "Verdict", "SessionError", "say", "result"):
                    for argument in node.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str) \
                                and re.fullmatch(r"[A-Z][A-Z_]+", argument.value):
                            used.add(argument.value)
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str) and node.value.value.endswith("_MISSING"):
                    used.add(node.value.value)
        self.assertGreater(len(used), 20)
        self.assertEqual(sorted(used - messages.keys() - {"STATUS"}), [])


class DecisionRecordTests(unittest.TestCase):
    def test_the_index_lists_every_record_in_order(self):
        records = sorted(name for name in os.listdir(os.path.join(ROOT, "DOCS", "ADR")) if re.match(r"\d{4}-", name))
        index = read(os.path.join("DOCS", "ADR", "README.md"))
        numbers = [int(name[:4]) for name in records]
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + len(numbers))), "numbers have gaps")
        for name in records:
            if not name.startswith("0000"):
                self.assertIn(f"({name})", index, f"the index does not link {name}")

    def test_every_record_has_the_template_sections(self):
        for name in os.listdir(os.path.join(ROOT, "DOCS", "ADR")):
            if re.match(r"\d{4}-", name):
                text = read(os.path.join("DOCS", "ADR", name))
                for heading in ("## Context", "## Decision", "## Consequences", "## Options considered"):
                    self.assertIn(heading, text, f"{name} lacks {heading}")
                self.assertRegex(text, r"\*\*Status\*\* \| (Proposed|Accepted|Superseded)", name)


if __name__ == "__main__":
    unittest.main()
