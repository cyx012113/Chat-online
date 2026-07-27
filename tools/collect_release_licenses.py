"""Collect license files and build metadata for a portable release."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import platform
import shutil
import sys


RUNTIME_DISTRIBUTIONS = (
    "PyQt6",
    "PyQt6-Qt6",
    "PyQt6-sip",
    "cryptography",
    "cffi",
    "pycparser",
    "markdown-it-py",
    "mdit-py-plugins",
    "Pygments",
    "linkify-it-py",
    "mdurl",
    "uc-micro-py",
    "PyInstaller",
    "altgraph",
)
LICENSE_MARKERS = ("license", "licence", "copying", "notice", "authors")


def is_license_file(relative: Path) -> bool:
    lowered_parts = [part.casefold() for part in relative.parts]
    return (
        "licenses" in lowered_parts
        or any(marker in relative.name.casefold() for marker in LICENSE_MARKERS)
    )


def collect_distribution(name: str, destination: Path) -> tuple[str, int]:
    distribution = metadata.distribution(name)
    package_dir = destination / name
    count = 0
    for relative_file in distribution.files or ():
        relative = Path(str(relative_file))
        if not is_license_file(relative):
            continue
        source = Path(distribution.locate_file(relative))
        if not source.is_file():
            continue
        target = package_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return distribution.version, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    args = parser.parse_args()
    release_dir = args.release_dir.resolve()
    licenses_dir = release_dir / "licenses"
    licenses_dir.mkdir(parents=True, exist_ok=True)

    report = [
        "Chat Online build information",
        f"Built at: {datetime.now(timezone.utc).isoformat()}",
        f"Python: {platform.python_version()} ({platform.architecture()[0]})",
        f"Platform: {platform.platform()}",
        "",
        "Bundled Python distributions:",
    ]
    for name in RUNTIME_DISTRIBUTIONS:
        try:
            version, file_count = collect_distribution(name, licenses_dir)
        except metadata.PackageNotFoundError:
            continue
        report.append(f"- {name} {version} ({file_count} license files copied)")

    python_license_candidates = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
    )
    for candidate in python_license_candidates:
        if candidate.is_file():
            target = licenses_dir / "Python" / candidate.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            break

    (release_dir / "BUILD-INFO.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(release_dir / "BUILD-INFO.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
