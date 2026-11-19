"""Build the Lambda code bundle without Docker.

`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary :all:` downloads
Linux/arm64 wheels on any machine (incl. macOS), so `cdk synth` needs no container.
boto3 is NOT bundled: the Lambda Python runtime already provides it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "build" / "lambda"
PLATFORM = "aarch64-manylinux_2_28"  # Lambda python3.12 runs on Amazon Linux 2023 (glibc 2.34)


def locked_requirements() -> Path:
    """Runtime deps exactly as pinned in uv.lock (no dev/infra groups), so Lambda == local."""
    req = ROOT / "build" / "lambda-requirements.txt"
    req.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--frozen",
            "--color",
            "never",
            "--quiet",
            "--output-file",
            str(req),
        ],
        cwd=ROOT,
        check=True,
    )
    return req


def build_bundle(force: bool = False) -> Path:
    req = locked_requirements()
    marker = BUNDLE / ".deps-installed"
    if force or (marker.exists() and marker.read_text() != req.read_text()):
        shutil.rmtree(BUNDLE, ignore_errors=True)  # lockfile changed -> rebuild dependencies
    if not marker.exists():
        BUNDLE.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                "--target",
                str(BUNDLE),
                "--python-version",
                "3.12",
                "--python-platform",
                PLATFORM,
                "--only-binary",
                ":all:",
                "-r",
                str(req),
            ],
            check=True,
        )
        marker.write_text(req.read_text())
    # Always refresh our own package so the bundle matches the working tree.
    dest = BUNDLE / "jobpulse"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "src" / "jobpulse", dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return BUNDLE
