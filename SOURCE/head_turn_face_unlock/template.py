"""The enrolled face: embeddings per angle bin, the infrared reflection level and the face's proportions.

No image is kept. Each sample is a 128-number SFace embedding, stored as float16 (the
rounding changes a cosine similarity by about 0.001, far below any threshold), so a full
template stays under systemd's 1 MiB credential limit without compression. Reading is
strict: wrong keys, wrong sizes, numbers that are not finite and vectors that are not unit
length are refused.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .decision import BIN_COUNT, Reference
from .geometry import SHAPE_NAMES
from .vision import EMBEDDING_SIZE

FORMAT = "HEAD_TURN_FACE_UNLOCK_TEMPLATE"
VERSION = 2
COLOR_LIMIT_PER_BIN = 60
INFRARED_LIMIT_PER_BIN = 40
MAXIMUM_TEXT_BYTES = 1 << 20
MAXIMUM_ADDITIONS = 1000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_KEYS = {"format", "version", "direction", "color", "infrared", "infrared_level", "shape_median",
         "shape_spread", "created", "additions"}


class TemplateError(ValueError):
    pass


@dataclass
class Template:
    direction: int
    color: list[np.ndarray]
    infrared: list[np.ndarray]
    reference: Reference
    created: str
    additions: list[str] = field(default_factory=list)

    def counts(self) -> tuple[list[int], list[int]]:
        return [len(bank) for bank in self.color], [len(bank) for bank in self.infrared]


def empty_bank() -> list[np.ndarray]:
    return [np.zeros((0, EMBEDDING_SIZE), np.float32) for _ in range(BIN_COUNT)]


def best_similarity(bank: list[np.ndarray], angle: int, embedding: np.ndarray) -> float:
    """Highest cosine similarity against the samples of this angle bin and its neighbours."""
    best = -1.0
    for neighbour in (angle - 1, angle, angle + 1):
        if 0 <= neighbour < len(bank) and len(bank[neighbour]):
            best = max(best, float((bank[neighbour] @ embedding).max()))
    return best


def prune(vectors: np.ndarray, limit: int) -> np.ndarray:
    """Keeps at most limit samples by dropping, one at a time, the sample most similar to
    another one. Different conditions (glasses, light, speed) survive; repeats go first."""
    vectors = np.asarray(vectors, np.float32)
    while len(vectors) > limit:
        similarity = vectors @ vectors.T
        np.fill_diagonal(similarity, -1.0)
        vectors = np.delete(vectors, int(similarity.max(axis=1).argmax()), axis=0)
    return vectors


def merge(old: Template, new: Template) -> Template:
    if old.direction != new.direction:
        raise TemplateError("the turn direction differs from the existing enrollment")
    color = [prune(np.vstack([a, b]), COLOR_LIMIT_PER_BIN) for a, b in zip(old.color, new.color)]
    infrared = [prune(np.vstack([a, b]), INFRARED_LIMIT_PER_BIN) for a, b in zip(old.infrared, new.infrared)]
    first, second = old.reference, new.reference
    median = (first.shape_median + second.shape_median) / 2
    # The spread grows to cover both sessions; it never shrinks below either.
    spread = np.maximum(np.maximum(first.shape_spread, second.shape_spread),
                        np.abs(first.shape_median - second.shape_median) / 2)
    reference = Reference((first.infrared_level + second.infrared_level) / 2, median, spread)
    additions = (old.additions + [new.created])[-MAXIMUM_ADDITIONS:]
    return Template(old.direction, color, infrared, reference, old.created, additions)


def to_json(template: Template) -> str:
    def encode(bank: list[np.ndarray]) -> list[str]:
        return [base64.b64encode(np.asarray(rows, "<f2").tobytes()).decode("ascii") for rows in bank]
    text = json.dumps({
        "format": FORMAT, "version": VERSION, "direction": template.direction,
        "color": encode(template.color), "infrared": encode(template.infrared),
        "infrared_level": template.reference.infrared_level,
        "shape_median": [float(value) for value in template.reference.shape_median],
        "shape_spread": [float(value) for value in template.reference.shape_spread],
        "created": template.created, "additions": template.additions,
    }, separators=(",", ":"), allow_nan=False)
    if len(text) > MAXIMUM_TEXT_BYTES:
        raise TemplateError("the template is larger than a system credential may be")
    return text


def from_json(text: str | bytes) -> Template:
    if len(text) > MAXIMUM_TEXT_BYTES:
        raise TemplateError("the template is too large")
    try:
        data = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_no_constants)
    except (ValueError, RecursionError) as error:
        raise TemplateError(f"not a valid template: {error}") from None
    if not isinstance(data, dict) or set(data) != _KEYS:
        raise TemplateError("the template has missing or unknown fields")
    if data["format"] != FORMAT or data["version"] != VERSION:
        raise TemplateError("unsupported template format or version; enroll again")
    if data["direction"] not in (1, -1) or isinstance(data["direction"], bool):
        raise TemplateError("direction must be 1 or -1")
    color = _decode_bank(data["color"], COLOR_LIMIT_PER_BIN, "color")
    infrared = _decode_bank(data["infrared"], INFRARED_LIMIT_PER_BIN, "infrared")
    level = _number(data["infrared_level"], "infrared_level")
    median = _numbers(data["shape_median"], "shape_median")
    spread = _numbers(data["shape_spread"], "shape_spread")
    if not (level > 0 and (median > 0).all() and (spread >= 0).all()):
        raise TemplateError("reference values are out of range")
    created, additions = data["created"], data["additions"]
    if not (isinstance(created, str) and _DATE.match(created)):
        raise TemplateError("created must be a date like 2026-01-31 12:00")
    if not (isinstance(additions, list) and len(additions) <= MAXIMUM_ADDITIONS
            and all(isinstance(item, str) and _DATE.match(item) for item in additions)):
        raise TemplateError("additions must be a list of dates")
    return Template(data["direction"], color, infrared, Reference(level, median, spread), created, additions)


def _decode_bank(value: object, limit: int, name: str) -> list[np.ndarray]:
    if not (isinstance(value, list) and len(value) == BIN_COUNT and all(isinstance(item, str) for item in value)):
        raise TemplateError(f"{name} must hold {BIN_COUNT} encoded bins")
    bank = []
    for item in value:
        try:
            raw = base64.b64decode(item, validate=True)
        except (binascii.Error, ValueError):
            raise TemplateError(f"{name} holds invalid base64") from None
        if len(raw) % (EMBEDDING_SIZE * 2):
            raise TemplateError(f"{name} holds a partial embedding")
        rows = np.frombuffer(raw, "<f2").astype(np.float32).reshape(-1, EMBEDDING_SIZE)
        if len(rows) > limit:
            raise TemplateError(f"{name} holds more than {limit} samples in one bin")
        if not np.isfinite(rows).all():
            raise TemplateError(f"{name} holds a number that is not finite")
        norms = np.linalg.norm(rows, axis=1)
        if len(rows) and not ((norms > 0.98) & (norms < 1.02)).all():
            raise TemplateError(f"{name} holds a vector that is not unit length")
        bank.append(rows / norms[:, None] if len(rows) else rows)
    return bank


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TemplateError(f"{name} must be a finite number")
    return float(value)


def _numbers(value: object, name: str) -> np.ndarray:
    if not (isinstance(value, list) and len(value) == len(SHAPE_NAMES)):
        raise TemplateError(f"{name} must hold {len(SHAPE_NAMES)} numbers")
    return np.array([_number(item, name) for item in value])


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"repeated key {key}")
        result[key] = value
    return result


def _no_constants(name: str) -> None:
    raise ValueError(f"{name} is not allowed")
