#!/usr/bin/python3 -IB
"""Installs the models and the LiteRT runtime, accepting a file only if its size and SHA-256
equal the pinned values in SOURCE/head_turn_face_unlock/model_files.py.

Usage: python3 -IB FETCH_COMPONENTS.py LIBRARY_DIRECTORY [LOCAL_COPY_DIRECTORY]

  LIBRARY_DIRECTORY/models   the SFace recognizer, BlazeFace and Face Mesh V2 (taken out of
                             the Face Landmarker bundle unchanged)
  LIBRARY_DIRECTORY/runtime  the LiteRT wheel for this Python and processor, unpacked; pip is
                             not used, so nothing else is installed and nothing runs at install

Files found in LOCAL_COPY_DIRECTORY under their published names are used instead of
downloading; they are checked the same way. Files already installed and intact are kept.
"""

import hashlib
import io
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "SOURCE"))
from head_turn_face_unlock.model_files import (  # noqa: E402
    BUNDLE, BUNDLE_MEMBERS, DOWNLOADS, INSTALLED, RECOGNIZER, RUNTIME_VERSION, RUNTIME_WHEELS)

RUNTIME_STAMP = ".WHEEL_SHA256"


class FetchError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def obtain(url: str, size: int, expected: str, local: str | None) -> bytes:
    """The file's bytes from the local copy directory or the network, checked."""
    name = url.rsplit("/", 1)[1]
    if local and os.path.isfile(os.path.join(local, name)):
        with open(os.path.join(local, name), "rb") as handle:
            data = handle.read(size + 1)
        source = os.path.join(local, name)
    else:
        print(f"{name}: downloading {size / 1e6:.1f} MB")
        chunks, received = [], 0
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URLs
            while block := response.read(1 << 20):
                received += len(block)
                if received > size:
                    raise FetchError(f"{name} is larger than the published {size} bytes")
                chunks.append(block)
        data, source = b"".join(chunks), url
    if len(data) != size or sha256(data) != expected:
        raise FetchError(f"{name} from {source} does not match its published checksum")
    return data


def install_file(directory: str, name: str, data: bytes) -> None:
    handle, staging = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
        os.chmod(staging, 0o644)
        os.replace(staging, os.path.join(directory, name))
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def fetch_models(library: str, local: str | None) -> None:
    models = os.path.join(library, "models")
    os.makedirs(models, mode=0o755, exist_ok=True)
    if file_sha256(os.path.join(models, RECOGNIZER)) == INSTALLED[RECOGNIZER]:
        print(f"{RECOGNIZER}: already installed and verified")
    else:
        install_file(models, RECOGNIZER, obtain(*DOWNLOADS[RECOGNIZER], local))
        print(f"{RECOGNIZER}: installed and verified")
    missing = [name for name in BUNDLE_MEMBERS if file_sha256(os.path.join(models, name)) != INSTALLED[name]]
    if not missing:
        print(f"{BUNDLE}: models already installed and verified")
        return
    with zipfile.ZipFile(io.BytesIO(obtain(*DOWNLOADS[BUNDLE], local))) as bundle:
        for name, (size, expected) in BUNDLE_MEMBERS.items():
            info = bundle.getinfo(name)
            if info.file_size != size:
                raise FetchError(f"{name} in {BUNDLE} has an unexpected size")
            data = bundle.read(info)
            if sha256(data) != expected:
                raise FetchError(f"{name} in {BUNDLE} does not match its checksum")
            install_file(models, name, data)
            print(f"{name}: installed from {BUNDLE} and verified")


def fetch_runtime(library: str, local: str | None) -> None:
    key = (f"cp{sys.version_info.major}{sys.version_info.minor}", platform.machine())
    if key not in RUNTIME_WHEELS:
        supported = ", ".join(sorted(f"Python {tag[2]}.{tag[3:]} on {machine}" for tag, machine in RUNTIME_WHEELS))
        raise FetchError(f"LiteRT {RUNTIME_VERSION} has no package for Python {key[0][2]}.{key[0][3:]} on "
                         f"{key[1]}; supported: {supported}")
    url, size, expected = RUNTIME_WHEELS[key]
    runtime = os.path.join(library, "runtime")
    stamp = os.path.join(runtime, RUNTIME_STAMP)
    if os.path.isfile(stamp) and open(stamp, encoding="ascii").read().strip() == expected:
        print(f"LiteRT {RUNTIME_VERSION}: already installed")
        return
    data = obtain(url, size, expected, local)
    fresh = os.path.join(library, "runtime.new")
    if os.path.lexists(fresh):
        shutil.rmtree(fresh)
    os.makedirs(fresh, mode=0o755)
    with zipfile.ZipFile(io.BytesIO(data)) as wheel:
        for member in wheel.infolist():
            parts = member.filename.split("/")
            if member.filename.startswith("/") or ".." in parts or "\\" in member.filename:
                raise FetchError(f"unsafe path in the LiteRT package: {member.filename}")
            target = os.path.join(fresh, *parts)
            if member.is_dir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            with wheel.open(member) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, 0o644)
    with open(os.path.join(fresh, RUNTIME_STAMP), "w", encoding="ascii") as handle:
        handle.write(expected + "\n")
    retired = os.path.join(library, "runtime.old")
    if os.path.lexists(retired):
        shutil.rmtree(retired)
    if os.path.lexists(runtime):
        os.rename(runtime, retired)
    os.rename(fresh, runtime)
    if os.path.lexists(retired):
        shutil.rmtree(retired)
    print(f"LiteRT {RUNTIME_VERSION}: installed and verified ({key[0]}, {key[1]})")


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 2
    library, local = argv[0], argv[1] if len(argv) == 2 else None
    try:
        fetch_models(library, local)
        fetch_runtime(library, local)
    except (OSError, FetchError, zipfile.BadZipFile, KeyError) as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
