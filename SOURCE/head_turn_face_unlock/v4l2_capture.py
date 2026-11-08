"""Direct V4L2 capture with memory-mapped buffers and driver timestamps.

OpenCV's VideoCapture decodes every frame on its own thread, renegotiates the
stream on each property change and replaces the driver's timestamps with its
own. This module sets the format once before streaming starts, keeps
compressed frames compressed until someone needs the pixels, and returns the
CLOCK_MONOTONIC time the driver stamped on each frame. The color and infrared
streams can then be paired by when the light reached each sensor instead of
by when a Python thread happened to look.

Only the Python standard library is used. Structure layouts follow
<linux/videodev2.h> for 64-bit Linux and are checked against the kernel's
sizes when the module loads.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import mmap
import os
import select
import time
from dataclasses import dataclass
from typing import Iterator

_U8 = ctypes.c_uint8
_U32 = ctypes.c_uint32

BUFFER_TYPE_VIDEO_CAPTURE = 1
MEMORY_MMAP = 1
CAPABILITY_VIDEO_CAPTURE = 0x00000001
CAPABILITY_STREAMING = 0x04000000
CAPABILITY_DEVICE_CAPS = 0x80000000
BUFFER_FLAG_ERROR = 0x00000040
BUFFER_FLAG_TIMESTAMP_MASK = 0x0000E000
BUFFER_FLAG_TIMESTAMP_MONOTONIC = 0x00002000
FRAME_SIZE_TYPE_DISCRETE = 1
FRAME_INTERVAL_TYPE_DISCRETE = 1


def fourcc(code: str) -> int:
    """Pixel format code as the kernel stores it, for example fourcc("MJPG")."""
    if len(code) != 4:
        raise ValueError(f"a pixel format code has four characters: {code!r}")
    return int.from_bytes(code.encode("ascii"), "little")


def fourcc_name(value: int) -> str:
    return value.to_bytes(4, "little").decode("ascii", "replace")


class _Capability(ctypes.Structure):
    _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32),
                ("bus_info", ctypes.c_char * 32), ("version", _U32),
                ("capabilities", _U32), ("device_caps", _U32), ("reserved", _U32 * 3)]


class _PixelFormat(ctypes.Structure):
    _fields_ = [("width", _U32), ("height", _U32), ("pixelformat", _U32), ("field", _U32),
                ("bytesperline", _U32), ("sizeimage", _U32), ("colorspace", _U32), ("priv", _U32),
                ("flags", _U32), ("ycbcr_enc", _U32), ("quantization", _U32), ("xfer_func", _U32)]


class _FormatUnion(ctypes.Union):
    # The kernel union holds a structure with pointers, so it is aligned to 8 bytes.
    _fields_ = [("pix", _PixelFormat), ("raw_data", _U8 * 200), ("alignment", ctypes.c_void_p)]


class _Format(ctypes.Structure):
    _fields_ = [("type", _U32), ("fmt", _FormatUnion)]


class _Fraction(ctypes.Structure):
    _fields_ = [("numerator", _U32), ("denominator", _U32)]


class _CaptureParameters(ctypes.Structure):
    _fields_ = [("capability", _U32), ("capturemode", _U32), ("timeperframe", _Fraction),
                ("extendedmode", _U32), ("readbuffers", _U32), ("reserved", _U32 * 4)]


class _StreamParametersUnion(ctypes.Union):
    _fields_ = [("capture", _CaptureParameters), ("raw_data", _U8 * 200)]


class _StreamParameters(ctypes.Structure):
    _fields_ = [("type", _U32), ("parm", _StreamParametersUnion)]


class _RequestBuffers(ctypes.Structure):
    _fields_ = [("count", _U32), ("type", _U32), ("memory", _U32), ("capabilities", _U32),
                ("flags", _U8), ("reserved", _U8 * 3)]


class _TimeValue(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_long), ("microseconds", ctypes.c_long)]


class _TimeCode(ctypes.Structure):
    _fields_ = [("type", _U32), ("flags", _U32), ("frames", _U8), ("seconds", _U8),
                ("minutes", _U8), ("hours", _U8), ("userbits", _U8 * 4)]


class _BufferLocation(ctypes.Union):
    _fields_ = [("offset", _U32), ("userptr", ctypes.c_ulong), ("planes", ctypes.c_void_p),
                ("fd", ctypes.c_int32)]


class _Buffer(ctypes.Structure):
    _fields_ = [("index", _U32), ("type", _U32), ("bytesused", _U32), ("flags", _U32),
                ("field", _U32), ("timestamp", _TimeValue), ("timecode", _TimeCode),
                ("sequence", _U32), ("memory", _U32), ("m", _BufferLocation), ("length", _U32),
                ("reserved2", _U32), ("request_fd", ctypes.c_int32)]


class _FormatDescription(ctypes.Structure):
    _fields_ = [("index", _U32), ("type", _U32), ("flags", _U32), ("description", ctypes.c_char * 32),
                ("pixelformat", _U32), ("mbus_code", _U32), ("reserved", _U32 * 3)]


class _FrameSizeUnion(ctypes.Union):
    _fields_ = [("discrete", _U32 * 2), ("stepwise", _U32 * 6)]


class _FrameSizeEnumeration(ctypes.Structure):
    _fields_ = [("index", _U32), ("pixel_format", _U32), ("type", _U32),
                ("size", _FrameSizeUnion), ("reserved", _U32 * 2)]


class _FrameIntervalUnion(ctypes.Union):
    _fields_ = [("discrete", _Fraction), ("stepwise", _Fraction * 3)]


class _FrameIntervalEnumeration(ctypes.Structure):
    _fields_ = [("index", _U32), ("pixel_format", _U32), ("width", _U32), ("height", _U32),
                ("type", _U32), ("interval", _FrameIntervalUnion), ("reserved", _U32 * 2)]


_KERNEL_SIZES = {_Capability: 104, _Format: 208, _StreamParameters: 204, _RequestBuffers: 20,
                 _Buffer: 88, _FormatDescription: 64, _FrameSizeEnumeration: 44,
                 _FrameIntervalEnumeration: 52}
for _structure, _size in _KERNEL_SIZES.items():
    if ctypes.sizeof(_structure) != _size:
        raise ImportError(f"{_structure.__name__} is {ctypes.sizeof(_structure)} bytes, the kernel uses {_size}: "
                          "this build of the module supports 64-bit Linux only")


def _request(direction: int, number: int, structure: type | None) -> int:
    size = ctypes.sizeof(structure) if structure is not None else ctypes.sizeof(ctypes.c_int)
    return (direction << 30) | (size << 16) | (ord("V") << 8) | number


_READ, _WRITE = 2, 1
QUERY_CAPABILITY = _request(_READ, 0, _Capability)
ENUMERATE_FORMATS = _request(_READ | _WRITE, 2, _FormatDescription)
SET_FORMAT = _request(_READ | _WRITE, 5, _Format)
REQUEST_BUFFERS = _request(_READ | _WRITE, 8, _RequestBuffers)
QUERY_BUFFER = _request(_READ | _WRITE, 9, _Buffer)
QUEUE_BUFFER = _request(_READ | _WRITE, 15, _Buffer)
DEQUEUE_BUFFER = _request(_READ | _WRITE, 17, _Buffer)
STREAM_ON = _request(_WRITE, 18, None)
STREAM_OFF = _request(_WRITE, 19, None)
SET_PARAMETERS = _request(_READ | _WRITE, 22, _StreamParameters)
ENUMERATE_FRAME_SIZES = _request(_READ | _WRITE, 74, _FrameSizeEnumeration)
ENUMERATE_FRAME_INTERVALS = _request(_READ | _WRITE, 75, _FrameIntervalEnumeration)


def _ioctl(descriptor: int, request: int, argument: ctypes.Structure | ctypes.c_int) -> None:
    while True:
        try:
            fcntl.ioctl(descriptor, request, argument)
            return
        except InterruptedError:
            continue


@dataclass(frozen=True)
class Mode:
    """One way a camera can stream: format, size and frames per second."""
    pixel_format: str
    width: int
    height: int
    frames_per_second: float


@dataclass(frozen=True)
class Frame:
    """One captured frame. data holds JPEG bytes for MJPG and raw pixels otherwise."""
    sequence: int
    timestamp: float
    data: bytes
    mode: Mode
    stride: int

    def grey(self):
        """An 8-bit grey frame as a height x width numpy array (numpy is imported lazily so
        this module stays usable without it)."""
        import numpy as np
        rows = np.frombuffer(self.data, np.uint8)[:self.stride * self.mode.height]
        return rows.reshape(self.mode.height, self.stride)[:, :self.mode.width]


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    card: str
    bus_info: str
    modes: tuple[Mode, ...]


def describe(path: str) -> DeviceInfo | None:
    """Capabilities and every discrete streaming mode of a device, without streaming.
    Returns None for nodes that cannot capture video (metadata nodes, for example)."""
    descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        capability = _Capability()
        _ioctl(descriptor, QUERY_CAPABILITY, capability)
        caps = capability.device_caps if capability.capabilities & CAPABILITY_DEVICE_CAPS else capability.capabilities
        if not caps & CAPABILITY_VIDEO_CAPTURE or not caps & CAPABILITY_STREAMING:
            return None
        modes: list[Mode] = []
        for pixel_format in _enumerate(descriptor, ENUMERATE_FORMATS, _FormatDescription,
                                       type=BUFFER_TYPE_VIDEO_CAPTURE):
            for size in _enumerate(descriptor, ENUMERATE_FRAME_SIZES, _FrameSizeEnumeration,
                                   pixel_format=pixel_format.pixelformat):
                if size.type != FRAME_SIZE_TYPE_DISCRETE:
                    continue
                width, height = size.size.discrete[0], size.size.discrete[1]
                for interval in _enumerate(descriptor, ENUMERATE_FRAME_INTERVALS, _FrameIntervalEnumeration,
                                           pixel_format=pixel_format.pixelformat, width=width, height=height):
                    if interval.type == FRAME_INTERVAL_TYPE_DISCRETE and interval.interval.discrete.numerator:
                        fraction = interval.interval.discrete
                        modes.append(Mode(fourcc_name(pixel_format.pixelformat), width, height,
                                          fraction.denominator / fraction.numerator))
        return DeviceInfo(path, capability.card.decode(errors="replace"),
                          capability.bus_info.decode(errors="replace"), tuple(modes))
    finally:
        os.close(descriptor)


def _enumerate(descriptor: int, request: int, structure: type, **fields: int) -> Iterator:
    index = 0
    while True:
        item = structure(index=index, **fields)
        try:
            _ioctl(descriptor, request, item)
        except OSError as error:
            if error.errno == errno.EINVAL:
                return
            raise
        yield item
        index += 1


class Camera:
    """One streaming device. Not thread safe; one thread owns it."""

    def __init__(self, path: str, mode: Mode, buffer_count: int = 4) -> None:
        self.path = path
        self.mode = mode
        self._buffer_count = buffer_count
        self._descriptor = -1
        self._maps: list[mmap.mmap] = []
        self._streaming = False
        self.stride = mode.width

    def open(self) -> None:
        """Opens the device, sets the format and frame rate once and queues the buffers."""
        self._descriptor = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            video_format = _Format(type=BUFFER_TYPE_VIDEO_CAPTURE)
            video_format.fmt.pix.width = self.mode.width
            video_format.fmt.pix.height = self.mode.height
            video_format.fmt.pix.pixelformat = fourcc(self.mode.pixel_format)
            _ioctl(self._descriptor, SET_FORMAT, video_format)
            granted = video_format.fmt.pix
            if (granted.width, granted.height, granted.pixelformat) != (
                    self.mode.width, self.mode.height, fourcc(self.mode.pixel_format)):
                raise OSError(errno.EINVAL, f"{self.path} refused {self.mode}, offered "
                                            f"{fourcc_name(granted.pixelformat)} {granted.width}x{granted.height}")
            self.stride = granted.bytesperline or self.mode.width
            parameters = _StreamParameters(type=BUFFER_TYPE_VIDEO_CAPTURE)
            parameters.parm.capture.timeperframe.numerator = 1000
            parameters.parm.capture.timeperframe.denominator = round(self.mode.frames_per_second * 1000)
            _ioctl(self._descriptor, SET_PARAMETERS, parameters)
            request = _RequestBuffers(count=self._buffer_count, type=BUFFER_TYPE_VIDEO_CAPTURE, memory=MEMORY_MMAP)
            _ioctl(self._descriptor, REQUEST_BUFFERS, request)
            if request.count < 2:
                raise OSError(errno.ENOMEM, f"{self.path} granted {request.count} buffers")
            for index in range(request.count):
                buffer = _Buffer(index=index, type=BUFFER_TYPE_VIDEO_CAPTURE, memory=MEMORY_MMAP)
                _ioctl(self._descriptor, QUERY_BUFFER, buffer)
                self._maps.append(mmap.mmap(self._descriptor, buffer.length, mmap.MAP_SHARED, mmap.PROT_READ,
                                            offset=buffer.m.offset))
                _ioctl(self._descriptor, QUEUE_BUFFER, buffer)
        except BaseException:
            self.close()
            raise

    def start(self) -> None:
        _ioctl(self._descriptor, STREAM_ON, ctypes.c_int(BUFFER_TYPE_VIDEO_CAPTURE))
        self._streaming = True

    def fileno(self) -> int:
        return self._descriptor

    def read(self) -> Frame | None:
        """The next finished frame, or None when the driver has none ready.
        The buffer goes back to the driver before this returns."""
        buffer = _Buffer(type=BUFFER_TYPE_VIDEO_CAPTURE, memory=MEMORY_MMAP)
        try:
            _ioctl(self._descriptor, DEQUEUE_BUFFER, buffer)
        except BlockingIOError:
            return None
        # Queueing the buffer again overwrites these fields, so they are read first.
        flags, sequence, used = buffer.flags, buffer.sequence, buffer.bytesused
        if flags & BUFFER_FLAG_TIMESTAMP_MASK == BUFFER_FLAG_TIMESTAMP_MONOTONIC:
            timestamp = buffer.timestamp.seconds + buffer.timestamp.microseconds / 1e6
        else:
            timestamp = time.monotonic()
        try:
            data = self._maps[buffer.index][:used]
        finally:
            _ioctl(self._descriptor, QUEUE_BUFFER, buffer)
        if flags & BUFFER_FLAG_ERROR or used == 0:
            return None
        return Frame(sequence, timestamp, data, self.mode, self.stride)

    def close(self) -> None:
        if self._descriptor < 0:
            return
        try:
            if self._streaming:
                _ioctl(self._descriptor, STREAM_OFF, ctypes.c_int(BUFFER_TYPE_VIDEO_CAPTURE))
        except OSError:
            pass
        for buffer_map in self._maps:
            buffer_map.close()
        self._maps.clear()
        os.close(self._descriptor)
        self._descriptor = -1
        self._streaming = False

    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def wait_for_frames(cameras: list[Camera], timeout: float) -> list[tuple[Camera, Frame]]:
    """Waits until at least one camera has a frame, then drains every ready frame.
    One thread serves all cameras, so nothing is lost between polls."""
    poller = select.poll()
    for camera in cameras:
        poller.register(camera.fileno(), select.POLLIN)
    ready = {descriptor for descriptor, _ in poller.poll(max(0.0, timeout) * 1000)}
    frames = []
    for camera in cameras:
        if camera.fileno() in ready:
            while (frame := camera.read()) is not None:
                frames.append((camera, frame))
    return frames
