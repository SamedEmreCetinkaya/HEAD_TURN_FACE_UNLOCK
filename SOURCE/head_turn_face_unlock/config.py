"""Settings file: KEY=VALUE lines, # starts a comment.

Parsing is strict. An unknown key, a value out of range or a repeated key is an
error, because a typo in a security setting must not quietly fall back to
something the administrator did not choose. While the file is invalid the
service refuses to verify faces and every login falls back to the password.
A missing file means the defaults below.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

CONFIG_PATH = "/etc/head-turn-face-unlock/HEAD_TURN_FACE_UNLOCK.conf"
LANGUAGES = ("en", "tr")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    LANGUAGE: str = "en"
    # A device path such as /dev/video0, or auto to find the color and infrared pair.
    COLOR_CAMERA: str = "auto"
    INFRARED_CAMERA: str = "auto"
    # How long one verification may take before the password is asked for.
    ATTEMPT_SECONDS: float = 5.0
    # How long the service keeps its models loaded after the last request.
    IDLE_EXIT_SECONDS: float = 600.0
    # Cosine similarity between face embeddings (SFace). Measured on the author's machine:
    # the enrolled person 0.69-0.91 in color and 0.87-0.94 in infrared, other people 0.21-0.25.
    MATCH_THRESHOLD: float = 0.60
    COLOR_MEDIAN_THRESHOLD: float = 0.65
    MATCH_FLOOR: float = 0.45
    INFRARED_THRESHOLD: float = 0.80
    # How long enrollment may take.
    ENROLL_SECONDS: float = 120.0

    def __post_init__(self) -> None:
        if self.LANGUAGE not in LANGUAGES:
            raise ConfigError(f"LANGUAGE must be one of {', '.join(LANGUAGES)}")
        for name in ("COLOR_CAMERA", "INFRARED_CAMERA"):
            value = getattr(self, name)
            if value != "auto" and not (value.startswith("/dev/") and ".." not in value):
                raise ConfigError(f"{name} must be auto or a path under /dev")
        _require_range("ATTEMPT_SECONDS", self.ATTEMPT_SECONDS, 2, 30)
        _require_range("IDLE_EXIT_SECONDS", self.IDLE_EXIT_SECONDS, 0, 86400)
        _require_range("ENROLL_SECONDS", self.ENROLL_SECONDS, 30, 600)
        for name in ("MATCH_THRESHOLD", "COLOR_MEDIAN_THRESHOLD", "MATCH_FLOOR", "INFRARED_THRESHOLD"):
            _require_range(name, getattr(self, name), 0.3, 0.99)
        if self.MATCH_FLOOR > self.MATCH_THRESHOLD:
            raise ConfigError("MATCH_FLOOR cannot be above MATCH_THRESHOLD")


def _require_range(name: str, value: float, low: float, high: float) -> None:
    if not (math.isfinite(value) and low <= value <= high):
        raise ConfigError(f"{name} must be between {low} and {high}")


def parse(text: str) -> Settings:
    fields = {field.name: field.type for field in dataclasses.fields(Settings)}
    values: dict[str, object] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = (part.strip() for part in line.partition("="))
        if not separator or not key or not value:
            raise ConfigError(f"line {number}: expected KEY=VALUE")
        if key not in fields:
            raise ConfigError(f"line {number}: unknown setting {key}")
        if key in values:
            raise ConfigError(f"line {number}: {key} is set twice")
        if fields[key] in (float, "float"):
            try:
                values[key] = float(value)
            except ValueError:
                raise ConfigError(f"line {number}: {key} must be a number") from None
        else:
            values[key] = value
    return Settings(**values)  # type: ignore[arg-type]


def load(path: str = CONFIG_PATH) -> Settings:
    try:
        with open(path, encoding="utf-8") as handle:
            return parse(handle.read(64 * 1024))
    except FileNotFoundError:
        return Settings()
