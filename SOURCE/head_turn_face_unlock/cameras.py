"""Finds the color and infrared cameras and picks how each one streams.

Laptops with face login ("Windows Hello" cameras) expose one USB device with a
color node that offers MJPG and an infrared node that offers only 8-bit grey.
Both report the same bus position, which is how they are paired. Nothing is
hard-wired to /dev/video0 or /dev/video2.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable

from . import v4l2_capture as v4l2
from .config import Settings

SYSFS_VIDEO = "/sys/class/video4linux"
COLOR_FORMAT = "MJPG"
INFRARED_FORMATS = ("GREY",)
# The recognizer aligns faces to 112x112 pixels. At arm's length a face spans about a
# fifth of a laptop camera's width, so frames at least 5 x 112 x 2 = 1120 pixels wide keep
# the aligned face at native resolution with a factor of 2 to spare. Wider frames only
# cost decode time.
RECOGNIZER_INPUT = 112
FACE_FRACTION_OF_WIDTH = 5
RESOLUTION_MARGIN = 2
MINIMUM_COLOR_WIDTH = RECOGNIZER_INPUT * FACE_FRACTION_OF_WIDTH * RESOLUTION_MARGIN


@dataclass(frozen=True)
class CameraPair:
    color_path: str
    color_mode: v4l2.Mode
    infrared_path: str
    infrared_mode: v4l2.Mode


def video_nodes(sysfs: str = SYSFS_VIDEO) -> list[str]:
    try:
        names = os.listdir(sysfs)
    except OSError:
        return []
    numbered = [name for name in names if re.fullmatch(r"video\d+", name)]
    return [f"/dev/{name}" for name in sorted(numbered, key=lambda name: int(name[5:]))]


def describe_all(paths: Iterable[str], describe: Callable[[str], v4l2.DeviceInfo | None] = v4l2.describe
                 ) -> list[v4l2.DeviceInfo]:
    devices = []
    for path in paths:
        try:
            info = describe(path)
        except OSError:
            continue
        if info is not None and info.modes:
            devices.append(info)
    return devices


def choose_color_mode(modes: Iterable[v4l2.Mode]) -> v4l2.Mode | None:
    candidates = [mode for mode in modes if mode.pixel_format == COLOR_FORMAT]
    if not candidates:
        return None
    fastest = max(mode.frames_per_second for mode in candidates)
    candidates = [mode for mode in candidates if mode.frames_per_second == fastest]
    wide_enough = [mode for mode in candidates if mode.width >= MINIMUM_COLOR_WIDTH]
    if wide_enough:
        return min(wide_enough, key=lambda mode: mode.width * mode.height)
    return max(candidates, key=lambda mode: mode.width * mode.height)


def choose_infrared_mode(modes: Iterable[v4l2.Mode]) -> v4l2.Mode | None:
    candidates = [mode for mode in modes if mode.pixel_format in INFRARED_FORMATS]
    if not candidates:
        return None
    return max(candidates, key=lambda mode: (mode.frames_per_second, mode.width * mode.height))


def is_infrared(device: v4l2.DeviceInfo) -> bool:
    return all(mode.pixel_format in INFRARED_FORMATS for mode in device.modes)


def find_pair(devices: list[v4l2.DeviceInfo], settings: Settings) -> CameraPair | None:
    by_path = {device.path: device for device in devices}
    if settings.COLOR_CAMERA != "auto":
        colors = [by_path[settings.COLOR_CAMERA]] if settings.COLOR_CAMERA in by_path else []
    else:
        colors = [device for device in devices if not is_infrared(device) and choose_color_mode(device.modes)]
    if settings.INFRARED_CAMERA != "auto":
        infrareds = [by_path[settings.INFRARED_CAMERA]] if settings.INFRARED_CAMERA in by_path else []
    else:
        infrareds = [device for device in devices if is_infrared(device)]
    for color in colors:
        for infrared in infrareds:
            same_device = color.bus_info == infrared.bus_info
            chosen_by_hand = settings.COLOR_CAMERA != "auto" and settings.INFRARED_CAMERA != "auto"
            if not (same_device or chosen_by_hand):
                continue
            color_mode, infrared_mode = choose_color_mode(color.modes), choose_infrared_mode(infrared.modes)
            if color_mode and infrared_mode:
                return CameraPair(color.path, color_mode, infrared.path, infrared_mode)
    return None


def discover(settings: Settings) -> CameraPair | None:
    return find_pair(describe_all(video_nodes()), settings)
