"""
Build the Lambda deployment package: processor/dist/processor.zip

Dependencies are installed for Lambda's runtime (Linux x86_64, Python 3.12) regardless of the
machine running this script. boto3 is provided by the Lambda runtime and is not bundled.

Usage: python processor/build.py
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
OUTPUT = DIST / "processor.zip"
FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)  # deterministic archives


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    BUILD.mkdir()
    DIST.mkdir()

    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet", "--no-cache-dir",
            "--target", str(BUILD),
            "--platform", "manylinux2014_x86_64",
            "--implementation", "cp",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "-r", str(ROOT / "requirements.txt"),
        ],
        check=True,
    )
    shutil.copy(ROOT / "handler.py", BUILD / "handler.py")

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(BUILD.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            info = zipfile.ZipInfo(path.relative_to(BUILD).as_posix(), FIXED_TIMESTAMP)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())

    print(f"Built {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
