"""Read and write the simple ``key = "value"`` files under ``config/``.

Kept separate from the GUI so the credential handling can be tested without a
display, and so scripts can read the same values the GUI writes.
"""

from __future__ import annotations

import os
from typing import Optional

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
CANVAS_CONFIG = os.path.join(CONFIG_DIR, "canvasAPI.py")
PANOPTO_CONFIG = os.path.join(CONFIG_DIR, "panoptoKey.py")


def readConfigValue(path: str, key: str) -> str:
    """Return the value assigned to ``key``, or an empty string."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                name, separator, value = line.partition("=")
                if separator and name.strip() == key:
                    return value.strip().strip("'\"")
    except OSError:
        pass

    return ""


def writeConfigValue(path: str, key: str, value: str) -> Optional[str]:
    """Set ``key`` in ``path``, creating the file or appending as needed.

    Returns ``None`` on success or an error message on failure. Lines that do
    not define ``key`` are left untouched, so comments and other settings in the
    file survive.
    """

    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        replacement = f'{key} = "{value}"\n'
        for index, line in enumerate(lines):
            name, separator, _ = line.partition("=")
            if separator and name.strip() == key:
                lines[index] = replacement
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(replacement)

        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as exc:
        return str(exc)

    return None
