import socket
import time
import unittest

import helpers  # noqa: F401

from head_turn_face_unlock import protocol
from head_turn_face_unlock.protocol import ProtocolError, Request, parse_request


class RequestTests(unittest.TestCase):
    def test_valid_requests(self):
        self.assertEqual(parse_request(b'{"request":"VERIFY","user":"alice","service":"sudo"}'),
                         Request("VERIFY", "alice", "sudo"))
        self.assertEqual(parse_request(b'{"request":"ENROLL","user":"a.b-c_d","mode":"ADD"}').mode, "ADD")
        self.assertEqual(parse_request(b'{"request":"STATUS"}').kind, "STATUS")

    def test_invalid_requests(self):
        for line in (b"", b"[]", b"not json", b'{"request":"VERIFY","user":"x"}',
                     b'{"request":"VERIFY","user":"x","service":"sudo","extra":"1"}',
                     b'{"request":"VERIFY","user":"../root","service":"sudo"}',
                     b'{"request":"VERIFY","user":"-x","service":"sudo"}',
                     b'{"request":"VERIFY","user":"' + b"a" * 40 + b'","service":"sudo"}',
                     b'{"request":"VERIFY","user":"x","service":"su do"}',
                     b'{"request":"ENROLL","user":"x","mode":"REPLACE"}',
                     b'{"request":"VERIFY","user":1,"service":"sudo"}',
                     b'{"request":"DELETE","user":"x"}', b"\xff\xfe"):
            with self.subTest(line=line), self.assertRaises(ProtocolError):
                parse_request(line)

    def test_dot_names_are_not_users(self):
        self.assertFalse(protocol.valid_user("."))
        self.assertFalse(protocol.valid_user(".."))


class LineTests(unittest.TestCase):
    def setUp(self):
        self.left, self.right = socket.socketpair()

    def tearDown(self):
        self.left.close()
        self.right.close()

    def test_a_line_is_read_whole(self):
        self.left.sendall(b'{"a":')
        self.left.sendall(b'"b"}\n')
        self.assertEqual(protocol.read_line(self.right, 100, time.monotonic() + 1), b'{"a":"b"}')

    def test_limits_and_timeouts(self):
        self.left.sendall(b"x" * 200)
        with self.assertRaises(ProtocolError):
            protocol.read_line(self.right, 100, time.monotonic() + 1)
        with self.assertRaises(ProtocolError):
            protocol.read_line(self.right, 100, time.monotonic() + 0.05)

    def test_data_after_the_request_is_refused(self):
        self.left.sendall(b"{}\nmore")
        with self.assertRaises(ProtocolError):
            protocol.read_line(self.right, 100, time.monotonic() + 1)

    def test_reader_splits_lines_that_arrive_together(self):
        self.left.sendall(protocol.encode({"type": "MESSAGE", "text": "one"}) + protocol.encode({"type": "RESULT"}))
        reader = protocol.Reader(self.right)
        self.assertEqual(reader.next(time.monotonic() + 1)["text"], "one")
        self.assertEqual(reader.next(time.monotonic() + 1)["type"], "RESULT")

    def test_reader_refuses_unknown_replies_and_closed_connections(self):
        self.left.sendall(b'{"type":"OTHER"}\n')
        reader = protocol.Reader(self.right)
        with self.assertRaises(ProtocolError):
            reader.next(time.monotonic() + 1)
        self.left.close()
        with self.assertRaises(ProtocolError):
            reader.next(time.monotonic() + 1)

    def test_printable_removes_control_characters(self):
        self.assertEqual(protocol.printable("ok\x1b[2J\nnext‮"), "ok[2Jnext")


if __name__ == "__main__":
    unittest.main()
