import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import helpers  # noqa: F401

from head_turn_face_unlock import pam_config, pam_helper, protocol

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEBIAN_SUDO = """#%PAM-1.0

# Set up user limits from /etc/security/limits.conf.
session    required   pam_limits.so

@include common-auth
@include common-account
@include common-session-noninteractive
"""
DEBIAN_GDM = """#%PAM-1.0
auth    requisite       pam_nologin.so
auth\trequired\tpam_succeed_if.so user != root quiet_success
@include common-auth
auth    optional        pam_gnome_keyring.so
@include common-account
"""
FEDORA_SUDO = """#%PAM-1.0
auth       include      system-auth
account    include      system-auth
password   include      system-auth
session    include      system-auth
"""
FEDORA_GDM = """auth     [success=done ignore=ignore default=bad] pam_selinux_permit.so
auth        substack      password-auth
auth        optional      pam_gnome_keyring.so
"""
DEBIAN_COMMON_AUTH = """# here are the per-package modules (the "Primary" block)
auth\t[success=1 default=ignore]\tpam_unix.so nullok
# here's the fallback if no module succeeds
auth\trequisite\t\t\tpam_deny.so
auth\trequired\t\t\tpam_permit.so
"""
DEBIAN_POLKIT = """#%PAM-1.0

@include common-auth
@include common-account
@include common-password
"""


class PamConfigTests(unittest.TestCase):
    def test_line_goes_right_before_the_shared_stack(self):
        for text, before in ((DEBIAN_SUDO, "@include common-auth"), (DEBIAN_GDM, "@include common-auth"),
                             (FEDORA_SUDO, "auth       include      system-auth"),
                             (FEDORA_GDM, "auth        substack      password-auth")):
            lines = pam_config.add(text).splitlines()
            index = lines.index(pam_config.LINE)
            self.assertEqual(lines[index - 1], pam_config.MARKER)
            self.assertEqual(lines[index + 1], before)

    def test_checks_above_the_stack_still_run_first(self):
        lines = pam_config.add(DEBIAN_GDM).splitlines()
        self.assertLess(lines.index("auth    requisite       pam_nologin.so"), lines.index(pam_config.LINE))

    def test_adding_twice_changes_nothing_and_removing_restores(self):
        once = pam_config.add(DEBIAN_SUDO)
        self.assertEqual(pam_config.add(once), once)
        self.assertEqual(pam_config.remove(once), DEBIAN_SUDO)
        self.assertTrue(pam_config.present(once))
        self.assertFalse(pam_config.present(DEBIAN_SUDO))

    def test_a_file_without_a_shared_stack_is_left_alone(self):
        with self.assertRaises(pam_config.PamError):
            pam_config.add("auth required pam_unix.so\n")

    def test_polkit_reuses_what_was_typed(self):
        text = pam_config.polkit(DEBIAN_COMMON_AUTH, DEBIAN_POLKIT)
        self.assertIn(pam_config.POLKIT_LINE, text)
        self.assertIn("pam_unix.so nullok try_first_pass", text)
        self.assertNotIn("@include common-auth", text)
        self.assertIn("@include common-account", text)

    def test_polkit_leaves_other_stacks_alone(self):
        fingerprint = DEBIAN_COMMON_AUTH.replace("auth\t[success=1", "auth\t[success=2 default=ignore]\tpam_fprintd.so\nauth\t[success=1")
        for common in (fingerprint, "auth sufficient pam_sss.so\n" + DEBIAN_COMMON_AUTH):
            with self.assertRaises(pam_config.PamError):
                pam_config.polkit(common, DEBIAN_POLKIT)


class EditPamScriptTests(unittest.TestCase):
    def run_script(self, *arguments):
        return subprocess.run([sys.executable, "-I", "-B", os.path.join(ROOT, "SCRIPTS", "EDIT_PAM.py"), *arguments,
                               "--pam-directory", self.pam, "--backup-directory", self.backup],
                              capture_output=True, text=True)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.pam = os.path.join(self.directory.name, "pam.d")
        self.backup = os.path.join(self.directory.name, "backup")
        os.makedirs(self.pam)
        for name, text in (("sudo", DEBIAN_SUDO), ("gdm-password", DEBIAN_GDM), ("common-auth", DEBIAN_COMMON_AUTH),
                           ("polkit-1", DEBIAN_POLKIT)):
            with open(os.path.join(self.pam, name), "w") as handle:
                handle.write(text)

    def tearDown(self):
        self.directory.cleanup()

    def read(self, name):
        with open(os.path.join(self.pam, name)) as handle:
            return handle.read()

    def test_installing_again_keeps_every_file_and_the_record_of_the_original(self):
        self.assertEqual(self.run_script("add", "--polkit").returncode, 0)
        first = {name: self.read(name) for name in ("sudo", "gdm-password", "polkit-1")}
        second_run = self.run_script("add", "--polkit")
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        self.assertEqual({name: self.read(name) for name in first}, first)
        self.assertEqual(self.run_script("remove").returncode, 0)
        self.assertEqual(self.read("polkit-1"), DEBIAN_POLKIT)

    def test_add_then_remove_returns_every_file_to_its_original(self):
        self.assertEqual(self.run_script("add", "--polkit").returncode, 0)
        self.assertTrue(pam_config.present(self.read("sudo")))
        self.assertIn(pam_config.POLKIT_LINE, self.read("polkit-1"))
        self.assertFalse(os.path.exists(os.path.join(self.pam, "sudo-i")))
        self.assertEqual(self.run_script("remove").returncode, 0)
        self.assertEqual(self.read("sudo"), DEBIAN_SUDO)
        self.assertEqual(self.read("gdm-password"), DEBIAN_GDM)
        self.assertEqual(self.read("polkit-1"), DEBIAN_POLKIT)
        self.assertEqual(sorted(name for name in os.listdir(self.pam) if name.startswith(".")), [])


class SudoMarkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.uid = os.getuid()
        os.makedirs(os.path.join(self.directory.name, str(self.uid)))
        self.path = os.path.join(self.directory.name, str(self.uid), pam_helper.SUDO_MARKER)

    def tearDown(self):
        self.directory.cleanup()

    def test_a_fresh_marker_is_accepted_once(self):
        open(self.path, "w").close()
        self.assertTrue(pam_helper.take_sudo_marker(self.uid, self.directory.name))
        self.assertFalse(pam_helper.take_sudo_marker(self.uid, self.directory.name))

    def test_old_future_and_foreign_markers_are_refused_and_used_up(self):
        for offset in (-60, 60):
            open(self.path, "w").close()
            moment = time.time() + offset
            os.utime(self.path, (moment, moment))
            self.assertFalse(pam_helper.take_sudo_marker(self.uid, self.directory.name))
            self.assertFalse(os.path.exists(self.path))
        os.symlink("/etc/hostname", self.path)
        self.assertFalse(pam_helper.take_sudo_marker(self.uid, self.directory.name))
        open(self.path, "w").close()
        self.assertFalse(pam_helper.take_sudo_marker(self.uid + 1, self.directory.name))

    def test_letter_y(self):
        import io
        for typed, expected in ((b"y\0", True), (b"Y", True), (b"yes\0", False), (b"secret\0", False), (b"", False)):
            self.assertEqual(pam_helper.asked_with_letter_y(io.BytesIO(typed)), expected, typed)


class LockedSessionTests(unittest.TestCase):
    def fake(self, outputs):
        class Result:
            def __init__(self, stdout):
                self.stdout, self.returncode = stdout, 0

        def run(arguments, **_):
            return Result(outputs.get(tuple(arguments[1:3]), ""))
        return run

    def test_only_a_locked_graphical_session_counts(self):
        sessions = {("show-user", "alice"): "3 c2\n",
                    ("show-session", "3"): "Type=tty\nLockedHint=yes\n",
                    ("show-session", "c2"): "Type=wayland\nLockedHint=no\n"}
        self.assertFalse(pam_helper.locked_graphical_session("alice", self.fake(sessions)))
        sessions[("show-session", "c2")] = "Type=wayland\nLockedHint=yes\n"
        self.assertTrue(pam_helper.locked_graphical_session("alice", self.fake(sessions)))

    def test_odd_session_names_are_ignored(self):
        sessions = {("show-user", "alice"): "../x\n", ("show-session", "../x"): "Type=wayland\nLockedHint=yes\n"}
        self.assertFalse(pam_helper.locked_graphical_session("alice", self.fake(sessions)))


class AskServiceTests(unittest.TestCase):
    def serve(self, replies, delay=0.0):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "socket")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(1)
        self.addCleanup(listener.close)

        def answer():
            connection, _ = listener.accept()
            with connection:
                connection.recv(1024)
                time.sleep(delay)
                for reply in replies:
                    connection.sendall(reply)
        threading.Thread(target=answer, daemon=True).start()
        return path

    def ask(self, path, seconds=2.0):
        import io
        output = io.StringIO()
        return pam_helper.ask_service("alice", "sudo", output, time.monotonic() + seconds, path), output.getvalue()

    def test_accepted(self):
        path = self.serve([protocol.encode({"type": "MESSAGE", "text": "Turn"}),
                           protocol.encode({"type": "RESULT", "accepted": True, "key": "ACCEPTED", "text": "ok"})])
        self.assertEqual(self.ask(path), (True, "Turn\n"))

    def test_rejected_errors_and_silence_mean_the_password(self):
        rejected = self.serve([protocol.encode({"type": "RESULT", "accepted": False, "key": "REJECTED", "text": "no"})])
        self.assertEqual(self.ask(rejected), (False, "no\n"))
        self.assertFalse(self.ask(self.serve([b"garbage\n"]))[0])
        self.assertFalse(self.ask(self.serve([]))[0])
        self.assertFalse(self.ask(self.serve([b'{"type":"RESULT","accepted":"yes"}\n']))[0])
        self.assertFalse(self.ask(self.serve([], delay=1.0), seconds=0.2)[0])
        self.assertFalse(self.ask("/nonexistent/socket")[0])

    def test_policy_refuses_without_asking(self):
        for environment in ({"PAM_TYPE": "account", "PAM_SERVICE": "sudo", "PAM_USER": "root"},
                            {"PAM_TYPE": "auth", "PAM_SERVICE": "login", "PAM_USER": "root"},
                            {"PAM_TYPE": "auth", "PAM_SERVICE": "sudo", "PAM_USER": "../root"},
                            {"PAM_TYPE": "auth", "PAM_SERVICE": "sudo", "PAM_USER": "no-such-user-here"}):
            self.assertEqual(pam_helper.main(environment), 1, environment)


if __name__ == "__main__":
    unittest.main()
