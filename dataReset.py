"""Clear cached audit data.

WARNING: this erases every result collected so far. Take a copy of
``data/audited_videos.json`` first if the audit history matters.
"""

from __future__ import annotations

import glob
import os

from auditCore import COURSE_MODULES_DIR, DATA_DIR, SORTED_MODULES_DIR


def resetDataFiles() -> int:
    """Delete the cached JSON files. Returns how many files were removed."""

    if not os.path.isdir(DATA_DIR):
        print(f"No '{DATA_DIR}' folder found. Nothing to delete.")
        return 0

    json_files = []
    for folder in (DATA_DIR, COURSE_MODULES_DIR, SORTED_MODULES_DIR):
        json_files.extend(glob.glob(os.path.join(folder, "*.json")))

    if not json_files:
        print("No .json files found to delete.")
        return 0

    removed = 0
    for path in json_files:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            print(f"Failed to delete {path}: {exc}")

    print(f"Removed {removed} data file(s).")
    return removed


def main() -> int:
    return resetDataFiles()


if __name__ == "__main__":
    main()
