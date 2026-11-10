"""Audit all locked versions across platforms/extras, without advisory suppressions."""

import argparse
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Optional pip-audit JSON report path"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / "uv.lock").read_text())
    versions = sorted(
        {
            (p["name"], p["version"].split("+")[0])
            for p in lock["package"]
            if p["name"] != "lassy"
        }
    )
    with tempfile.TemporaryDirectory(prefix="lassy-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        requirements.write_text(
            "".join(f"{name}=={version}\n" for name, version in versions)
        )
        print(
            f"Auditing {len(versions)} locked third-party versions across all extras/platforms.",
            flush=True,
        )
        command = [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "--no-deps",
            "--disable-pip",
            "--progress-spinner",
            "off",
        ]
        if args.output:
            command += ["--format", "json", "--output", str(args.output)]
        return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
