import argparse
import os
import pwd
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401

from head_turn_face_unlock import cli

ME = pwd.getpwuid(os.getuid()).pw_name


class WhoseFaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.loginuid = os.path.join(self.directory.name, "loginuid")

    def write_loginuid(self, value):
        with open(self.loginuid, "w") as handle:
            handle.write(value)

    def user(self, name=None, sudo_user=None):
        environment = {} if sudo_user is None else {"SUDO_USER": sudo_user}
        with mock.patch.dict(os.environ, environment, clear=True):
            return cli._user(argparse.Namespace(user=name), self.loginuid)

    def test_an_explicit_name_wins(self):
        self.assertEqual(self.user("alice", sudo_user=ME), "alice")

    def test_the_person_who_ran_sudo(self):
        self.assertEqual(self.user(sudo_user="alice"), "alice")

    def test_a_root_shell_from_sudo_uses_the_login_account(self):
        self.write_loginuid(str(os.getuid()))
        self.assertEqual(self.user(sudo_user="root"), ME)
        self.assertEqual(self.user(), ME)

    def test_root_itself_or_an_unknown_login_must_name_the_person(self):
        for value in ("0", "4294967295", "not a number"):
            self.write_loginuid(value)
            with self.assertRaises(SystemExit):
                self.user(sudo_user="root")
        with self.assertRaises(SystemExit):
            self.user("../root")


if __name__ == "__main__":
    unittest.main()
