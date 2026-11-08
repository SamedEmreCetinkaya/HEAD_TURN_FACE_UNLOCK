"""Checks and loads the three models once: SFace in OpenCV, BlazeFace and Face Mesh V2 in LiteRT."""

from __future__ import annotations

import os

import numpy as np

from . import vision
from .landmarks import FaceFinder, model_paths

RUNTIME_DIRECTORY = "/usr/local/lib/head-turn-face-unlock/runtime"


class Models:
    def __init__(self, directory: str, threads: int) -> None:
        vision.verify_models(directory)
        self._threads = threads
        self._detector_path, self._mesh_path = model_paths(directory)
        try:
            self.new_finder().find(np.zeros((90, 160, 3), np.uint8))
        except ImportError as error:
            raise vision.ModelError(f"the LiteRT runtime is missing ({error}); run SCRIPTS/INSTALL.sh again") from None
        self.recognizer = vision.Recognizer(directory, threads)

    def new_finder(self) -> FaceFinder:
        """A detector and mesh for one thread."""
        return FaceFinder(self._detector_path, self._mesh_path, self._threads)


def runtime_on_path(directory: str = RUNTIME_DIRECTORY) -> None:
    """Lets Python find the LiteRT runtime that INSTALL.sh unpacked beside the package."""
    import sys
    if os.path.isdir(directory) and directory not in sys.path:
        sys.path.insert(0, directory)
