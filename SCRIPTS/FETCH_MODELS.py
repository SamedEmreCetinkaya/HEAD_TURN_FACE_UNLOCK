#!/usr/bin/python3 -IB
"""Installs the two models into a directory, only if their SHA-256 matches the pinned value.

Usage: python3 -IB FETCH_MODELS.py TARGET_DIRECTORY [LOCAL_COPY_DIRECTORY]
With a local copy directory nothing is downloaded; the copies are checked the same way.
"""

import hashlib
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "SOURCE"))
from head_turn_face_unlock.model_files import DOWNLOAD_BASE, FILES  # noqa: E402


def digest_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, folder: str, size: int, destination: str) -> None:
    url = f"{DOWNLOAD_BASE}/{folder}/{name}"
    with urllib.request.urlopen(url, timeout=60) as response, open(destination, "wb") as output:
        received = 0
        while block := response.read(1 << 20):
            received += len(block)
            if received > size:
                raise ValueError(f"{url} is larger than the published {size} bytes")
            output.write(block)


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 2
    target = argv[0]
    local = argv[1] if len(argv) == 2 else None
    os.makedirs(target, mode=0o755, exist_ok=True)
    for name, (folder, size, expected) in FILES.items():
        path = os.path.join(target, name)
        if os.path.isfile(path) and digest_of(path) == expected:
            print(f"{name}: already installed and verified")
            continue
        handle, staging = tempfile.mkstemp(prefix=f".{name}.", dir=target)
        os.close(handle)
        try:
            if local:
                with open(os.path.join(local, name), "rb") as source, open(staging, "wb") as output:
                    output.write(source.read(size + 1))
            else:
                print(f"{name}: downloading {size / 1e6:.1f} MB")
                fetch(name, folder, size, staging)
            if digest_of(staging) != expected:
                print(f"{name}: checksum does not match the published file; not installed", file=sys.stderr)
                return 1
            os.chmod(staging, 0o644)
            os.replace(staging, path)
            print(f"{name}: installed and verified")
        except (OSError, ValueError) as error:
            print(f"{name}: {error}", file=sys.stderr)
            return 1
        finally:
            if os.path.exists(staging):
                os.unlink(staging)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
