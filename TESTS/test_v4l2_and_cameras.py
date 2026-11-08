import os
import unittest

import helpers  # noqa: F401

from head_turn_face_unlock import cameras
from head_turn_face_unlock import v4l2_capture as v4l2
from head_turn_face_unlock.config import Settings

Mode = v4l2.Mode


class LayoutTests(unittest.TestCase):
    def test_request_numbers_equal_the_kernel_header(self):
        # Values of the VIDIOC_* macros from <linux/videodev2.h> on 64-bit Linux.
        expected = {"QUERY_CAPABILITY": 0x80685600, "ENUMERATE_FORMATS": 0xC0405602, "SET_FORMAT": 0xC0D05605,
                    "REQUEST_BUFFERS": 0xC0145608, "QUERY_BUFFER": 0xC0585609, "QUEUE_BUFFER": 0xC058560F,
                    "DEQUEUE_BUFFER": 0xC0585611, "STREAM_ON": 0x40045612, "STREAM_OFF": 0x40045613,
                    "SET_PARAMETERS": 0xC0CC5616, "ENUMERATE_FRAME_SIZES": 0xC02C564A,
                    "ENUMERATE_FRAME_INTERVALS": 0xC034564B}
        for name, value in expected.items():
            self.assertEqual(getattr(v4l2, name), value, name)

    def test_pixel_format_codes(self):
        self.assertEqual(v4l2.fourcc("MJPG"), 0x47504A4D)
        self.assertEqual(v4l2.fourcc_name(v4l2.fourcc("GREY")), "GREY")
        with self.assertRaises(ValueError):
            v4l2.fourcc("MJPEG")

    def test_grey_frames_respect_the_row_stride(self):
        mode = Mode("GREY", 4, 2, 15.0)
        frame = v4l2.Frame(1, 0.0, bytes(range(12)), mode, stride=6)
        self.assertEqual(frame.grey().tolist(), [[0, 1, 2, 3], [6, 7, 8, 9]])

    def test_one_poll_drains_every_ready_camera(self):
        class Fake:
            def __init__(self, count):
                self.read_end, self.write_end = os.pipe()
                os.write(self.write_end, b"x")
                self.left = count

            def fileno(self):
                return self.read_end

            def read(self):
                if not self.left:
                    return None
                self.left -= 1
                return v4l2.Frame(self.left, 0.0, b"", Mode("MJPG", 1, 1, 30.0), 1)

        first, second = Fake(3), Fake(0)
        try:
            frames = v4l2.wait_for_frames([first, second], 0.1)
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(camera is first for camera, _ in frames))
        finally:
            for fake in (first, second):
                os.close(fake.read_end)
                os.close(fake.write_end)


HELLO_COLOR = [Mode("MJPG", w, h, 30.0) for w, h in ((1920, 1080), (1280, 720), (640, 480), (320, 240))] \
    + [Mode("YUYV", 640, 480, 30.0), Mode("YUYV", 1280, 720, 5.0)]
HELLO_INFRARED = [Mode("GREY", 640, 360, 15.0)]


def device(path, bus, modes):
    return v4l2.DeviceInfo(path, "camera", bus, tuple(modes))


class DiscoveryTests(unittest.TestCase):
    def test_a_windows_hello_style_pair(self):
        pair = cameras.find_pair([device("/dev/video0", "usb-1", HELLO_COLOR),
                                  device("/dev/video2", "usb-1", HELLO_INFRARED)], Settings())
        self.assertEqual((pair.color_path, pair.color_mode), ("/dev/video0", Mode("MJPG", 1280, 720, 30.0)))
        self.assertEqual((pair.infrared_path, pair.infrared_mode), ("/dev/video2", HELLO_INFRARED[0]))

    def test_cameras_on_different_devices_are_not_paired(self):
        found = cameras.find_pair([device("/dev/video0", "usb-1", HELLO_COLOR),
                                   device("/dev/video4", "usb-2", HELLO_INFRARED)], Settings())
        self.assertIsNone(found)

    def test_named_devices_are_paired_even_across_buses(self):
        settings = Settings(COLOR_CAMERA="/dev/video0", INFRARED_CAMERA="/dev/video4")
        found = cameras.find_pair([device("/dev/video0", "usb-1", HELLO_COLOR),
                                   device("/dev/video4", "usb-2", HELLO_INFRARED)], settings)
        self.assertEqual(found.infrared_path, "/dev/video4")

    def test_without_infrared_there_is_no_pair(self):
        self.assertIsNone(cameras.find_pair([device("/dev/video0", "usb-1", HELLO_COLOR)], Settings()))

    def test_color_mode_choice(self):
        self.assertEqual(cameras.choose_color_mode([Mode("MJPG", 640, 480, 30.0), Mode("MJPG", 320, 240, 30.0)]),
                         Mode("MJPG", 640, 480, 30.0))
        self.assertEqual(cameras.choose_color_mode([Mode("MJPG", 1920, 1080, 15.0), Mode("MJPG", 1280, 720, 30.0)]),
                         Mode("MJPG", 1280, 720, 30.0))
        self.assertIsNone(cameras.choose_color_mode([Mode("YUYV", 640, 480, 30.0)]))

    def test_unreadable_nodes_are_skipped(self):
        def describe(path):
            if path.endswith("1"):
                raise PermissionError(13, "denied")
            return None if path.endswith("3") else device(path, "usb-1", HELLO_INFRARED)
        self.assertEqual([d.path for d in cameras.describe_all(["/dev/video1", "/dev/video2", "/dev/video3"], describe)],
                         ["/dev/video2"])


if __name__ == "__main__":
    unittest.main()
