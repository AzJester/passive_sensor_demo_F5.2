"""Revision A command line, kept so existing habits and scripts still work.

    python app.py --demo            ->  python -m pasensor run --mode simulated
    python app.py --live --device N ->  python -m pasensor run --mode live --device N
    python app.py --wav FILE        ->  python -m pasensor run --mode replay --wav FILE

New work should call "python -m pasensor" directly. Run it with --help.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pasensor.cli import main  # noqa: E402


def translate(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true")
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--wav", type=Path)
    parser.add_argument("--device")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    if args.list_devices:
        return ["devices"]
    if not any((args.demo, args.live, args.wav)):
        parser.error("Choose --demo, --live or --wav FILE.")
    out = ["run", "--mode", "simulated" if args.demo else ("live" if args.live else "replay"), "--port", str(args.port)]
    if args.wav:
        out += ["--wav", str(args.wav)]
    if args.device is not None:
        out += ["--device", str(args.device)]
    if args.config:
        out += ["--config", str(args.config)]
    if args.log:
        out += ["--event-log", str(args.log)]
    return out


if __name__ == "__main__":
    raise SystemExit(main(translate(sys.argv[1:])))
