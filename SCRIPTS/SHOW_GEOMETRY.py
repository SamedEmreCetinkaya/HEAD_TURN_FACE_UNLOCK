#!/usr/bin/python3 -IB
"""Opens a window that draws, live, what a verification measures from both cameras.

Usage: python3 -IB SHOW_GEOMETRY.py [--seconds N] [--library DIRECTORY]

Run it as yourself (members of the video group may open the cameras), not as root.
While it runs the cameras are busy, so face unlock falls back to the password.
Nothing is saved. Press q or Esc, or close the window, to stop.

Left: the color camera, mirrored. The 478 mesh points, the outline, brows, eyes, lips and
nose, the five points the recognizer aligns by (yellow rings), the head pose in degrees,
the parallax against the last frame that looked straight at the camera (a flat picture
stays near 0%, a real head passes 15% within a short turn) and the face's proportions.
Right: the lit infrared frame with its own mesh and the reflection inside the outline.
Bottom: yaw over the last seconds, the eight angle bins and the 20 degree turn limits.
"""

import argparse
import collections
import os
import sys
import time

LIBRARY = "/usr/local/lib/head-turn-face-unlock"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "SOURCE"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from head_turn_face_unlock import cameras, decision, geometry, vision  # noqa: E402
from head_turn_face_unlock import v4l2_capture as v4l2  # noqa: E402
from head_turn_face_unlock.config import ConfigError, Settings, load  # noqa: E402

WINDOW = "HEAD_TURN_FACE_UNLOCK geometry"
WIDTH, HEIGHT, STRIP = 640, 360, 170
TRAIL_SECONDS = 4.0
REGIONS = (
    (geometry.FACE_OVAL, True, (255, 255, 255)),
    ((46, 53, 52, 65, 55, 107, 66, 105, 63, 70), True, (0, 200, 255)),
    ((276, 283, 282, 295, 285, 336, 296, 334, 293, 300), True, (0, 200, 255)),
    ((33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246), True, (255, 200, 0)),
    ((263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466), True, (255, 200, 0)),
    ((61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185), True, (80, 80, 255)),
    ((78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191), True, (80, 80, 255)),
    (geometry.NOSE_RIDGE, False, (0, 255, 120)),
    ((98, 97, 2, 326, 327), False, (0, 255, 120)),
)
GREY, RED, YELLOW = (150, 150, 150), (60, 60, 230), (0, 230, 255)


def put(image, text, position, color=(255, 255, 255), scale=0.45, thickness=1):
    cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(image, text, position, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def mirror(points: np.ndarray, width: int) -> np.ndarray:
    flipped = points.copy()
    flipped[:, 0] = width - 1 - flipped[:, 0]
    return flipped


def draw_mesh(panel: np.ndarray, points: np.ndarray, regions: bool) -> None:
    for x, y in np.round(points[:, :2]).astype(int):
        cv2.circle(panel, (x, y), 1, (190, 190, 190), -1)
    if regions:
        for indexes, closed, color in REGIONS:
            cv2.polylines(panel, [np.round(points[list(indexes), :2]).astype(np.int32)], closed, color, 1, cv2.LINE_AA)


class Display:
    def __init__(self) -> None:
        self.trail: collections.deque = collections.deque(maxlen=400)
        self.straight: np.ndarray | None = None
        self.infrared = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        put(self.infrared, "waiting for a lit infrared frame", (12, 28), GREY, 0.6)

    def color(self, image: np.ndarray, count: int, face, timestamp: float, milliseconds: float) -> np.ndarray:
        panel = cv2.flip(image, 1)
        if face is None:
            put(panel, "no face" if count == 0 else f"{count} faces: the turn would break here", (12, 28), RED, 0.7, 2)
            return panel
        points = face.points
        yaw, pitch, roll = geometry.pose(points)
        shown = mirror(points, image.shape[1])
        draw_mesh(panel, shown, True)
        for x, y in np.round(mirror(np.c_[geometry.alignment_points(points), np.zeros(5)], image.shape[1])[:, :2]).astype(int):
            cv2.circle(panel, (x, y), 5, YELLOW, 1, cv2.LINE_AA)
        if abs(yaw) < 5 and abs(pitch) < 10:
            self.straight = points
        motion = None
        if self.straight is not None:
            motion = geometry.parallax(geometry.rigid_points(self.straight), geometry.rigid_points(points),
                                       geometry.eye_distance(self.straight), geometry.eye_distance(points))
        self.trail.append((timestamp, yaw))
        angle = decision.bin_of(yaw)
        put(panel, f"yaw {yaw:+.0f}  pitch {pitch:+.0f}  roll {roll:+.0f} deg   bin {angle if angle is not None else '-'}",
            (12, 26), YELLOW, 0.6, 2)
        put(panel, f"3D parallax vs straight view: {'-' if motion is None else f'{100 * motion:.0f}%'}"
                   f"   (flat picture < 2%, required {100 * decision.PARALLAX_MINIMUM:.0f}%)", (12, 50), (0, 255, 120), 0.45)
        put(panel, f"detect + 478-point mesh {milliseconds:.1f} ms   presence {face.presence:.2f}", (12, 72), GREY, 0.42)
        if abs(yaw) <= decision.FRONTAL_YAW and abs(pitch) <= decision.FRONTAL_PITCH:
            for row, (name, value) in enumerate(zip(geometry.SHAPE_NAMES, geometry.shape(points))):
                put(panel, f"{name} {value:.3f}", (12, 250 + 15 * row), GREY, 0.38)
        return panel

    def infrared_frame(self, lit: np.ndarray, difference: np.ndarray, count: int, face) -> None:
        panel = cv2.flip(cv2.cvtColor(lit, cv2.COLOR_GRAY2BGR), 1)
        if face is None:
            put(panel, "the infrared camera cannot see one face", (12, 28), RED, 0.65, 2)
        else:
            draw_mesh(panel, mirror(face.points, lit.shape[1]), False)
            level = vision.infrared_level(difference, face.points)
            yaw = geometry.pose(face.points)[0]
            put(panel, f"infrared: 478 points, yaw {yaw:+.0f} deg, reflection {level if level is None else round(level)}",
                (12, 28), YELLOW, 0.55, 2)
        self.infrared = panel

    def strip(self, now: float) -> np.ndarray:
        strip = np.zeros((STRIP, WIDTH * 2, 3), np.uint8)
        left, right, top, bottom = 60, WIDTH * 2 - 20, 22, STRIP - 40

        def x_of(value: float) -> int:
            return int(left + (value - decision.BINS[0]) / (decision.BINS[-1] - decision.BINS[0]) * (right - left))
        for index in range(decision.BIN_COUNT):
            x0, x1 = x_of(decision.BINS[index]), x_of(decision.BINS[index + 1])
            cv2.rectangle(strip, (x0, bottom + 6), (x1 - 2, bottom + 22), (60, 60, 60), -1)
            put(strip, f"{decision.BINS[index]:+.0f}", (x0 + 2, bottom + 19), GREY, 0.36)
        for limit in (-decision.TURN_SIDE, decision.TURN_SIDE):
            cv2.line(strip, (x_of(limit), top), (x_of(limit), bottom + 22), RED, 1)
        put(strip, "a turn must pass both red lines, from the + side to the - side", (left, 14), GREY, 0.42)
        points = [(x_of(float(np.clip(value, decision.BINS[0], decision.BINS[-1]))),
                   int(bottom - (now - moment) / TRAIL_SECONDS * (bottom - top)))
                  for moment, value in self.trail if now - moment <= TRAIL_SECONDS]
        for start, end in zip(points, points[1:]):
            cv2.line(strip, start, end, YELLOW, 2, cv2.LINE_AA)
        recent = [value for moment, value in self.trail if now - moment <= 3.0]
        put(strip, f"yaw in the last 3 s: {min(recent):+.0f} to {max(recent):+.0f} degrees" if recent
            else "no face in the last 3 s", (left, STRIP - 6), (255, 255, 255), 0.45)
        return strip


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Live drawing of what a verification measures.")
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--library", default=LIBRARY)
    arguments = parser.parse_args(argv)
    if os.geteuid() == 0:
        print("Run this as yourself, not as root.", file=sys.stderr)
        return 1
    sys.path.insert(0, os.path.join(arguments.library, "runtime"))
    from head_turn_face_unlock.models import Models
    try:
        settings = load()
    except (ConfigError, OSError):
        settings = Settings()
    models = Models(os.path.join(arguments.library, "models"), 2)
    color_finder, infrared_finder = models.new_finder(), models.new_finder()
    pair = cameras.discover(settings)
    if pair is None:
        print("No color and infrared camera pair was found.", file=sys.stderr)
        return 1
    color = v4l2.Camera(pair.color_path, pair.color_mode)
    infrared = v4l2.Camera(pair.infrared_path, pair.infrared_mode)
    try:
        for camera in (color, infrared):
            camera.open()
        for camera in (color, infrared):
            camera.start()
    except OSError as error:
        print(f"The cameras cannot be opened: {error}", file=sys.stderr)
        return 1
    pairer, display = vision.InfraredPairer(), Display()
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    deadline = time.monotonic() + arguments.seconds
    try:
        while time.monotonic() < deadline:
            panel = None
            for camera, frame in v4l2.wait_for_frames([color, infrared], 0.05):
                if camera is color:
                    image = vision.decode_half(frame.data)
                    if image is None:
                        continue
                    started = time.perf_counter()
                    count, face = color_finder.find(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                    panel = display.color(image, count, face, frame.timestamp, (time.perf_counter() - started) * 1000)
                elif len(frame.data) >= frame.stride * frame.mode.height:
                    lit = pairer.push(np.ascontiguousarray(frame.grey()))
                    if lit is not None:
                        count, face = infrared_finder.find(np.repeat(lit[0][:, :, None], 3, axis=2))
                        display.infrared_frame(lit[0], lit[1], count, face)
            if panel is None:
                continue
            panels = [cv2.resize(item, (WIDTH, HEIGHT)) if item.shape[:2] != (HEIGHT, WIDTH) else item
                      for item in (panel, display.infrared)]
            cv2.imshow(WINDOW, np.vstack([np.hstack(panels), display.strip(time.monotonic())]))
            if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        color.close()
        infrared.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
