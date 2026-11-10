"""Write SHA256SUMS.txt for the project and build the release archive.

Run:  python tools/make_release.py
Creates:
    SHA256SUMS.txt                                  one line per packaged file
    dist/Passive_Acoustic_UAS_Sensor_v<version>.zip the package
    dist/Passive_Acoustic_UAS_Sensor_v<version>.zip.sha256
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {".venv", "__pycache__", "dist", "recordings", "logs", ".git", ".claude", "executive_brief"}    # the brief describes the package, so it is not part of it
EXCLUDED_FILES = {"SHA256SUMS.txt"}
EXCLUDED_NAMES = {"node.json"}          # a unit's own configuration is not part of the release


def packaged_files() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if relative.as_posix() in EXCLUDED_FILES or (relative.parts[0] == "config" and path.name in EXCLUDED_NAMES):
            continue
        if path.suffix == ".pyc" or path.name.startswith("~$") or ".tmp." in path.name:
            continue                    # compiled files, and lock or temporary files left by an editor
        files.append(path)
    return files


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    sys.path.insert(0, str(ROOT))
    from pasensor import __version__
    if __version__ != version:
        print(f"VERSION file says {version} but pasensor reports {__version__}", file=sys.stderr)
        return 1
    files = packaged_files()
    lines = [f"{sha256(path)}  {path.relative_to(ROOT).as_posix()}" for path in files]
    (ROOT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    name = f"Passive_Acoustic_UAS_Sensor_v{version}"
    archive = dist / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in files + [ROOT / "SHA256SUMS.txt"]:
            bundle.write(path, f"{name}/{path.relative_to(ROOT).as_posix()}")
    digest = sha256(archive)
    (dist / f"{name}.zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    print(f"{len(files)} files listed in SHA256SUMS.txt")
    print(f"{archive.name}  {archive.stat().st_size} bytes  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
