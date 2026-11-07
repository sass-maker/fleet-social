from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mashup.extension.paths import open_beneath

MAX_SKILL_BYTES = 128 * 1024


class Skills:
    def __init__(self, roots: list[Path]):
        self.roots = [root.expanduser().resolve(strict=True) for root in roots]
        if any(not root.is_dir() for root in self.roots):
            raise ValueError("Each skills root must be an existing directory")

    def _entries(self) -> dict[str, Path]:
        entries = {}
        for root in self.roots:
            for candidate in sorted(root.glob("*/SKILL.md")):
                try:
                    path = candidate.resolve(strict=True)
                    if (
                        not path.is_relative_to(root)
                        or not path.is_file()
                        or path.stat().st_size > MAX_SKILL_BYTES
                    ):
                        continue
                except (OSError, RuntimeError):
                    continue
                skill_id = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                entries[skill_id] = path
                if len(entries) >= 256:
                    return entries
        return entries

    def list(self) -> list[dict]:
        result = []
        for skill_id, path in self._entries().items():
            try:
                with open_beneath(path, self.roots) as source:
                    text = source.read(MAX_SKILL_BYTES + 1).decode("utf-8")
            except (OSError, ValueError):
                continue
            if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
                continue
            description = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
            result.append(
                {
                    "id": skill_id,
                    "name": path.parent.name,
                    "description": description.group(1).strip("\"'")[:600] if description else "",
                    "canonicalPath": str(path),
                    "execution": "guidance-only",
                }
            )
        return result

    def read(self, skill_id: str) -> dict:
        path = self._entries().get(skill_id)
        if not path:
            raise ValueError("Skill not found within configured roots")
        with open_beneath(path, self.roots) as source:
            content = source.read(MAX_SKILL_BYTES + 1)
        if len(content) > MAX_SKILL_BYTES:
            raise ValueError("Skill exceeds the read size limit")
        return {
            "id": skill_id,
            "canonicalPath": str(path),
            "execution": "guidance-only",
            "content": content.decode("utf-8"),
            "notice": (
                "Host guidance only. This server neither executes this skill "
                "nor grants its permissions."
            ),
        }
