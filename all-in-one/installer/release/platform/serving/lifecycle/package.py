"""Bounded, checksum-verified model extraction for native Triton delivery.

Never extract an untrusted tar using extractall. Only regular files/directories
are supported, into a newly-created directory, after the whole archive validates.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile


class PackageError(ValueError):
    pass


def unpack_model(archive: Path, destination: Path, sha256: str,
                 max_bytes: int = 2 * 1024**3, max_files: int = 10000) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PackageError("A lowercase SHA-256 checksum is required")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != sha256:
        raise PackageError("Model package checksum mismatch")
    if destination.exists() or destination.is_symlink():
        raise PackageError("Destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".model-", dir=destination.parent))
    try:
        with tarfile.open(archive, "r:*") as package:
            members = []
            seen = set()
            total = 0
            for member in package:
                name = member.name
                path = PurePosixPath(name)
                if (not name or path.is_absolute() or ".." in path.parts
                        or "\\" in name or "\x00" in name or path == PurePosixPath(".")):
                    raise PackageError("Unsafe archive path")
                if not (member.isfile() or member.isdir()):
                    raise PackageError("Links and special archive entries are prohibited")
                canonical = str(path)
                if canonical in seen:
                    raise PackageError("Duplicate archive path")
                seen.add(canonical)
                total += member.size
                if member.size < 0 or total > max_bytes or len(seen) > max_files:
                    raise PackageError("Model package exceeds extraction limits")
                members.append(member)
            if not any(m.name == "config.pbtxt" and m.isfile() for m in members) or not any(n.startswith("1/") for n in seen):
                raise PackageError("Expected Triton config.pbtxt and version 1 artifacts")
            # Validate everything before writing any member; ignore supplied modes/owners.
            for member in members:
                target = staging.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o600)
        os.rename(staging, destination)
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)
