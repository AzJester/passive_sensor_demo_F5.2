"""Revision A recording command, kept for compatibility.

    python record.py --device 3 --seconds 30 --output-dir recordings

It now calls "python -m pasensor record", which writes a metadata sidecar
beside every WAV file and never overwrites an existing recording.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pasensor.cli import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    argv = ["record", "--device", args.device, "--seconds", str(args.seconds), "--out-dir", str(args.output_dir)]
    if args.config:
        argv += ["--config", str(args.config)]
    raise SystemExit(main(argv))
