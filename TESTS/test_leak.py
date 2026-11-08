"""The long-lived parts must not grow: open files, memory and threads stay flat over many
requests to the service and many verification sessions."""

import gc
import os
import socket
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest import mock

import helpers
from test_session import PAIR, FakeModels, FakeStreams, Script, SyncWorker, owner_template

from head_turn_face_unlock import protocol, session, vision
from head_turn_face_unlock import service as service_module
from head_turn_face_unlock.config import Settings
from head_turn_face_unlock.service import Service
from head_turn_face_unlock.template import to_json


def open_files() -> int:
    return len(os.listdir("/proc/self/fd"))


class ServiceLeakTests(unittest.TestCase):
    def test_hundreds_of_requests_leave_no_trace(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(service_module, "log", lambda line: None):
            credentials = os.path.join(directory, "credentials")
            os.makedirs(credentials)
            with open(os.path.join(credentials, "TEMPLATE_alice"), "w") as handle:
                handle.write(to_json(helpers.sample_template()))
            path = os.path.join(directory, "socket")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(path)
            listener.listen(8)
            service = Service(Settings(IDLE_EXIT_SECONDS=60), "/nonexistent", credentials, allowed_uid=os.getuid(),
                              discover=lambda settings: None, load_models=lambda d, t: object())
            thread = threading.Thread(target=service.serve, args=(listener,), daemon=True)
            thread.start()
            requests = [b'{"request":"STATUS"}\n', protocol.encode({"request": "VERIFY", "user": "alice", "service": "sudo"}),
                        protocol.encode({"request": "VERIFY", "user": "bob", "service": "sudo"}), b"garbage\n"]

            def burst(count):
                for index in range(count):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.connect(path)
                        connection.sendall(requests[index % len(requests)])
                        reader = protocol.Reader(connection)
                        try:
                            while reader.next(time.monotonic() + 5)["type"] != "RESULT":
                                pass
                        except protocol.ProtocolError:
                            pass
            burst(50)
            gc.collect()
            tracemalloc.start()
            files_before, memory_before = open_files(), tracemalloc.get_traced_memory()[0]
            burst(400)
            gc.collect()
            files_after, memory_after = open_files(), tracemalloc.get_traced_memory()[0]
            tracemalloc.stop()
            listener.shutdown(socket.SHUT_RDWR)
            listener.close()
            thread.join(2)
        self.assertLessEqual(files_after, files_before)
        self.assertLess(memory_after - memory_before, 256 * 1024)


class SessionLeakTests(unittest.TestCase):
    def test_repeated_sessions_end_their_threads_and_files(self):
        marker = (lambda data: helpers.np.full((1, 1, 3), int.from_bytes(data, "little"), helpers.np.float32))
        with mock.patch.object(session, "_Streams", FakeStreams), mock.patch.object(session, "_Worker", SyncWorker), \
                mock.patch.object(vision, "decode_half", marker), \
                mock.patch.object(vision, "decode_full", lambda data: helpers.np.ones((2, 2, 3), helpers.np.uint8)):
            def once():
                yaws = helpers.turn_yaws() + [0.0] * 10
                FakeStreams.script = Script(yaws)
                verdict = session.Verification(FakeModels(yaws), PAIR, owner_template(), Settings(), lambda key: None,
                                               clock=FakeStreams.script.clock).run(lambda result: None)
                self.assertTrue(verdict.accepted)
            once()
            gc.collect()
            threads, files = threading.active_count(), open_files()
            for _ in range(30):
                once()
            gc.collect()
        self.assertEqual(threading.active_count(), threads)
        self.assertEqual(open_files(), files)


if __name__ == "__main__":
    unittest.main()
