#!/usr/bin/env python3
"""Build dist/videoswami.pyz, a single-file zipapp of the videoswami package.

Usage: python3 build.py     then: python3 dist/videoswami.pyz <command> ...
"""
import pathlib
import shutil
import sys
import tempfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parent
PACKAGE = ROOT / "videoswami"
DIST = ROOT / "dist"


def main():
    if not (PACKAGE / "__main__.py").is_file():
        print("build failed: %s/__main__.py is missing" % PACKAGE, file=sys.stderr)
        return 1
    DIST.mkdir(exist_ok=True)
    target = DIST / "videoswami.pyz"
    with tempfile.TemporaryDirectory() as tmp:
        stage = pathlib.Path(tmp)
        shutil.copytree(PACKAGE, stage / "videoswami", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # The zipapp entry point imports the package's own CLI.
        (stage / "__main__.py").write_text("import sys\nfrom videoswami.__main__ import main\nsys.exit(main())\n", encoding="utf-8")
        zipapp.create_archive(stage, target, interpreter="/usr/bin/env python3", compressed=True)
    target.chmod(0o755)
    print(str(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
