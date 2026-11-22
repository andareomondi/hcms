"""
Zip the Django project root, excluding __pycache__ and .git.
Run from the project root: python make_zip.py
"""

import os
import zipfile
from pathlib import Path

EXCLUDED_DIRS = {"__pycache__", ".git", "media", "staticfiles", "venv"}

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / f"{ROOT.name}.zip"


def should_skip(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts)


def main():
    if OUTPUT.exists():
        OUTPUT.unlink()

    count = 0
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            for name in filenames:
                full = Path(dirpath) / name
                if full == OUTPUT or should_skip(full):
                    continue
                zf.write(full, full.relative_to(ROOT.parent))
                count += 1

    print(f"Created {OUTPUT} with {count} files.")


if __name__ == "__main__":
    main()
