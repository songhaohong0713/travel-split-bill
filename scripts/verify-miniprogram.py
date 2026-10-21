"""Compile local WXML/WXSS with installed WeChat developer tools; no upload."""

import argparse
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("compiler_dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "miniprogram"
    with tempfile.TemporaryDirectory(prefix="travel-split-compile-") as directory:
        for extension, compiler in [("wxml", "wcc.exe"), ("wxss", "wcsc.exe")]:
            files = [
                path.relative_to(root).as_posix()
                for path in root.rglob("*." + extension)
            ]
            result = subprocess.run(
                [
                    str(args.compiler_dir / compiler),
                    "-o",
                    str(Path(directory) / extension),
                    *files,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr + result.stdout)
            print(f"{extension}: {len(files)} files compiled")


if __name__ == "__main__":
    main()
