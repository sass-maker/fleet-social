from __future__ import annotations

import os
import stat
from pathlib import Path


def open_beneath(path: Path, roots: list[Path]):
    root = next((root for root in roots if path.is_relative_to(root)), None)
    if root is None:
        raise ValueError("File is outside configured roots")
    parts = path.relative_to(root).parts
    if not parts:
        raise ValueError("Expected a file below a configured root")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            os.close(file_descriptor)
            raise ValueError("Expected a regular local file")
        return os.fdopen(file_descriptor, "rb")
    finally:
        os.close(descriptor)
