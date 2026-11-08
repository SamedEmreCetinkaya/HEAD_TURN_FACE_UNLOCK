import os
import socket
import tempfile
import threading
import time
import unittest

from unittest import mock

from helpers import sample_template

from head_turn_face_unlock import protocol, service as service_module, vision
from head_turn_face_unlock.config import Settings
from head_turn_face_unlock.service import Service
from head_turn_face_unlock.template import to_json


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.credentials = os.path.join(self.directory.name, "credentials")
        os.makedirs(self.credentials)
        self.path = os.path.join(self.directory.name, "socket")
        self.logged = []
        patch = mock.patch.object(service_module, "log", self.logged.append)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.directory.cleanup()

    def start(self, uid=None, discover=lambda settings: None, load_models=lambda directory, threads: object(),
              idle=60.0):
        service = Service(Settings(IDLE_EXIT_SECONDS=idle), "/nonexistent", self.credentials,
                          allowed_uid=os.getuid() if uid is None else uid, discover=discover, load_models=load_models)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        listener.listen(4)
        thread = threading.Thread(target=service.serve, args=(listener,), daemon=True)
        thread.start()

        def stop():
            # Wakes the blocked accept() so the service ends while its log is still captured.
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()
            thread.join(2)
        self.addCleanup(stop)
        return service, thread

    def ask(self, message: bytes):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(self.path)
            connection.sendall(message)
            reader = protocol.Reader(connection)
            replies = []
            try:
                while True:
                    reply = reader.next(time.monotonic() + 5)
                    replies.append(reply)
                    if reply["type"] == "RESULT":
                        return replies
            except protocol.ProtocolError:
                return replies

    def enroll_file(self, user, text):
        with open(os.path.join(self.credentials, "TEMPLATE_" + user), "w") as handle:
            handle.write(text)

    def test_status_lists_enrolled_people(self):
        self.enroll_file("alice", to_json(sample_template()))
        self.start()
        result = self.ask(b'{"request":"STATUS"}\n')[-1]
        self.assertEqual(result["status"]["enrolled"], ["alice"])
        self.assertEqual(result["status"]["models"], "ready")
        self.assertIsNone(result["status"]["cameras"])

    def test_other_users_get_no_answer(self):
        self.start(uid=os.getuid() + 1)
        self.assertEqual(self.ask(b'{"request":"STATUS"}\n'), [])
        self.assertTrue(any(line.startswith("REFUSED connection from uid") for line in self.logged))

    def test_malformed_requests_get_no_answer(self):
        self.start()
        self.assertEqual(self.ask(b'{"request":"VERIFY","user":"../x","service":"sudo"}\n'), [])

    def test_unknown_unreadable_and_linked_templates_count_as_not_enrolled(self):
        self.enroll_file("broken", "{}")
        os.symlink(os.path.join(self.credentials, "TEMPLATE_broken"), os.path.join(self.credentials, "TEMPLATE_link"))
        self.start()
        for user in ("nobody", "broken", "link"):
            reply = self.ask(protocol.encode({"request": "VERIFY", "user": user, "service": "sudo"}))[-1]
            self.assertEqual((reply["accepted"], reply["key"]), (False, "NOT_ENROLLED"), user)

    def test_missing_cameras(self):
        self.enroll_file("alice", to_json(sample_template()))
        self.start()
        reply = self.ask(protocol.encode({"request": "VERIFY", "user": "alice", "service": "sudo"}))[-1]
        self.assertEqual(reply["key"], "CAMERA_MISSING")

    def test_broken_models_make_the_service_unavailable(self):
        self.enroll_file("alice", to_json(sample_template()))

        def fail(directory, threads):
            raise vision.ModelError("checksum mismatch")
        from head_turn_face_unlock.cameras import CameraPair
        from head_turn_face_unlock.v4l2_capture import Mode
        pair = CameraPair("/dev/null", Mode("MJPG", 1280, 720, 30.0), "/dev/null", Mode("GREY", 640, 360, 15.0))
        self.start(discover=lambda settings: pair, load_models=fail)
        reply = self.ask(protocol.encode({"request": "VERIFY", "user": "alice", "service": "sudo"}))[-1]
        self.assertEqual(reply["key"], "UNAVAILABLE")

    def test_the_service_exits_when_idle(self):
        _, thread = self.start(idle=0.2)
        self.ask(b'{"request":"STATUS"}\n')
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_messages_follow_the_language_setting(self):
        service = Service(Settings(LANGUAGE="tr"), "/nonexistent", None, load_models=lambda d, t: object())
        left, right = socket.socketpair()
        with left, right:
            from head_turn_face_unlock.service import _Replies
            replies = _Replies(left, service.settings.LANGUAGE)
            replies.say("TURN_HEAD")
            self.assertIn("sağdan sola", protocol.Reader(right).next(time.monotonic() + 1)["text"])
            self.assertFalse(replies.cancelled())
            right.close()
            self.assertTrue(replies.cancelled())


if __name__ == "__main__":
    unittest.main()
