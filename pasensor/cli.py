"""Command line entry point: python -m pasensor <command>."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading
import time
from pathlib import Path

from . import REVISION, __version__

ROOT = Path(__file__).resolve().parent.parent
MODE_BANNER = {
    "LIVE": "LIVE AUDIO from a capture device",
    "REPLAY": "RECORDING REPLAY from a WAV file",
    "SIMULATED": "SIMULATED DATA - NOT REAL AUDIO",
}


def default_config_path() -> Path:
    own = ROOT / "config" / "node.json"
    return own if own.exists() else ROOT / "config" / "node.example.json"


def _labels(pairs) -> dict:
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--label expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _setup_logging(log_file) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


# ------------------------------------------------------------------------- run
def cmd_run(args) -> int:
    from .acquisition import LiveSource, ReplaySource, SimulatedSource, fault_injecting_factory
    from .config import ConfigError, load_config
    from .pipeline import Pipeline
    from .recorder import PRIVACY_NOTICE, JsonlWriter, SessionRecorder
    from .server import make_server
    from .state import SharedState

    _setup_logging(args.log_file)
    try:
        cfg = load_config(args.config or default_config_path())
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    mode = args.mode.upper()
    hop = cfg["processing"]["hop_frames"]
    health = cfg["health"]
    if mode == "LIVE":
        if args.wav:
            print("--wav cannot be combined with --mode live.", file=sys.stderr)
            return 2
        def factory():
            return LiveSource(cfg["device"], hop, override=args.device, queue_blocks=health["queue_blocks"],
                              stall_timeout_s=health["stall_timeout_s"])
    elif mode == "REPLAY":
        if not args.wav:
            print("--mode replay needs --wav FILE.", file=sys.stderr)
            return 2
        def factory():
            return ReplaySource(args.wav, cfg["device"], hop, realtime=args.replay_speed == "realtime")
    else:
        def factory():
            return SimulatedSource(cfg["device"], cfg["array"]["geometry_m"], cfg["array"]["temperature_c"], hop,
                                   scenario=args.scenario, seed=args.seed, realtime=args.replay_speed == "realtime")
    if args.inject_fault:
        if mode != "SIMULATED":
            print("--inject-fault is a test aid and works only with --mode simulated.", file=sys.stderr)
            return 2
        plan = {}
        for item in args.inject_fault:
            number, action = item.split(":", 1)
            plan[int(number)] = action
        factory = fault_injecting_factory(factory, plan)

    classifier = None
    if cfg["classifier"]["model_path"]:
        from .classifier import load_classifier
        model_path = Path(cfg["classifier"]["model_path"])
        if not model_path.is_absolute() and cfg.path is not None:
            model_path = cfg.path.resolve().parent / model_path
        try:
            classifier = load_classifier(model_path, cfg, mode)
        except (OSError, ValueError) as error:
            print(f"Classifier model error: {error}", file=sys.stderr)
            return 2

    state = SharedState(health["stale_after_s"])
    event_log = JsonlWriter(args.event_log) if args.event_log else None
    observation_log = JsonlWriter(args.observation_log) if args.observation_log else None
    recorder = None
    if args.record_raw:
        recorder = SessionRecorder(args.record_raw, cfg, mode, labels=_labels(args.label))
        print(PRIVACY_NOTICE)
    pipeline = Pipeline(cfg, mode, factory, state, event_log=event_log, observation_log=observation_log,
                        recorder=recorder, classifier=classifier)
    stop = threading.Event()
    result = {}
    worker = threading.Thread(target=lambda: result.update(final=pipeline.run(stop)), name="pipeline", daemon=True)

    server = None
    if not args.no_server:
        info = {"software_version": __version__, "revision": REVISION, "node_id": cfg.node_id, "mode": mode,
                "config_sha256": cfg.sha256, "calibrated": cfg.calibrated,
                "geometry_m": cfg["array"]["geometry_m"], "geometry_source": cfg["array"]["geometry_source"],
                "band_hz": list(cfg.dsp_settings().band_hz), "site": cfg["site"]}
        try:
            server = make_server(state, info, args.port)
        except OSError as error:
            print(f"Cannot start the dashboard on port {args.port}: {error}", file=sys.stderr)
            return 2
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25}, name="http", daemon=True).start()
    print(f"Passive acoustic sensor {__version__} (Revision {REVISION})  node {cfg.node_id}")
    print(f"MODE: {MODE_BANNER[mode]}")
    if cfg.upgraded_from_v1:
        print("Note: the configuration uses the Revision A layout. It was converted in memory; see config/node.example.json.")
    if mode != "SIMULATED" and not cfg.calibrated:
        print("Bearing calibration: NOT DONE. Bearings stay hidden until a calibration record is accepted.")
    if server is not None:
        print(f"Dashboard: http://127.0.0.1:{server.server_address[1]}  (this computer only)")
    print("Raw audio is saved only when --record-raw is given. Press Ctrl+C to stop.", flush=True)
    worker.start()
    deadline = None if args.duration is None else time.monotonic() + args.duration
    announced = False
    try:
        while True:
            worker.join(timeout=0.25)
            if deadline is not None and time.monotonic() >= deadline:
                break
            if not worker.is_alive():
                if server is None or args.exit_when_done or mode != "REPLAY":
                    break
                if not announced:
                    announced = True
                    print("Replay finished. The dashboard stays up until you press Ctrl+C.", flush=True)
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopping.")
    finally:
        stop.set()
        worker.join(timeout=5)
        if server is not None:
            server.shutdown()
            server.server_close()
        for writer in (recorder, event_log, observation_log):
            if writer is not None:
                writer.close()
    summary = {"final_state": result.get("final", "STOPPED"), **pipeline.stats,
               "dsp_ms_max": round(pipeline._dsp_ms_max, 2), "load_fraction": round(pipeline._load, 4)}
    if recorder is not None:
        summary["recording"] = recorder.status()
    print(json.dumps(summary))
    return 0 if summary["final_state"] != "FAILED" else 1


# --------------------------------------------------------------------- devices
def cmd_devices(_args) -> int:
    from .acquisition import SourceFault, list_input_devices, load_sounddevice
    try:
        sd = load_sounddevice()
    except SourceFault as error:
        print(error, file=sys.stderr)
        return 2
    devices = list_input_devices(sd)
    if not devices:
        print("No audio input devices were found.")
        return 1
    print(f"{'index':>5}  {'channels':>8}  {'default Hz':>10}  host API / name")
    for d in devices:
        mark = "  <-- six or more inputs" if d["max_input_channels"] >= 6 else ""
        print(f"{d['index']:>5}  {d['max_input_channels']:>8}  {d['default_samplerate']:>10.0f}  {d['hostapi']} / {d['name']}{mark}")
    print("\nThe sensor needs one endpoint that offers 6 input channels at 16000 Hz. The same device is listed "
          "once per host API. Prefer the WASAPI entry.")
    return 0


# ---------------------------------------------------------------------- record
def cmd_record(args) -> int:
    from .acquisition import LiveSource, SourceFault
    from .config import ConfigError, load_config
    from .recorder import PRIVACY_NOTICE, SessionRecorder
    try:
        cfg = load_config(args.config or default_config_path())
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if not 1 <= args.seconds <= 3600:
        print("--seconds must be between 1 and 3600", file=sys.stderr)
        return 2
    hop = cfg["processing"]["hop_frames"]
    source = LiveSource(cfg["device"], hop, override=args.device, queue_blocks=cfg["health"]["queue_blocks"],
                        stall_timeout_s=cfg["health"]["stall_timeout_s"])
    try:
        source.open()
    except SourceFault as error:
        print(f"Cannot open the device: {error}", file=sys.stderr)
        return 2
    print(PRIVACY_NOTICE)
    recorder = SessionRecorder(args.out_dir, cfg, "LIVE", device_info=source.describe(), labels=_labels(args.label))
    print(f"Recording {args.seconds} s from [{source.device['index']}] {source.device['name']} to {args.out_dir}")
    lost = 0
    status = 0
    try:
        for _ in range(int(args.seconds * cfg.sample_rate / hop)):
            chunk = source.read()
            lost += int(chunk.overflow or chunk.gap_before)
            recorder.write(chunk)
    except SourceFault as error:
        print(f"Recording stopped early: {error}", file=sys.stderr)
        status = 1
    except KeyboardInterrupt:
        print("Stopped by the operator.")
    finally:
        source.close()
        recorder.close()
    info = recorder.status()
    print(json.dumps({"files": recorder.files, "seconds": info["seconds"], "discontinuities": lost, "error": info["error"]}))
    if lost:
        print("Samples were lost during this recording. The sidecar file lists where. Do not use it for delay calibration.")
    return status if not info["error"] else 1


# ------------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pasensor", description="Passive acoustic sensor node.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the sensor in one explicit mode")
    run.add_argument("--mode", required=True, choices=("live", "replay", "simulated"))
    run.add_argument("--config", type=Path, help="node configuration (default config/node.json, else the example)")
    run.add_argument("--device", help="live: device index or part of its name (default: from the configuration)")
    run.add_argument("--wav", type=Path, help="replay: recording to analyse")
    run.add_argument("--scenario", default="demo", help="simulated: demo, quiet, flyby, or fan")
    run.add_argument("--seed", type=int, default=20261109, help="simulated: random seed")
    run.add_argument("--replay-speed", choices=("realtime", "max"), default="realtime")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--no-server", action="store_true", help="do not start the dashboard")
    run.add_argument("--exit-when-done", action="store_true", help="replay: exit at the end of the file")
    run.add_argument("--duration", type=float, help="stop after this many seconds")
    run.add_argument("--event-log", type=Path, help="append candidate and health records (JSON lines, no audio)")
    run.add_argument("--observation-log", type=Path, help="append per-frame bearing observations for multi-node fusion")
    run.add_argument("--record-raw", type=Path, metavar="DIR", help="ALSO save raw multichannel audio to DIR")
    run.add_argument("--label", action="append", help="key=value stored with a raw recording (repeatable)")
    run.add_argument("--log-file", type=Path, help="diagnostic log file (rotating)")
    run.add_argument("--inject-fault", action="append", metavar="N:ACTION", help=argparse.SUPPRESS)
    run.set_defaults(handler=cmd_run)

    devices = sub.add_parser("devices", help="list audio capture devices")
    devices.set_defaults(handler=cmd_devices)

    record = sub.add_parser("record", help="record raw multichannel audio with a metadata sidecar")
    record.add_argument("--config", type=Path)
    record.add_argument("--device")
    record.add_argument("--seconds", type=float, default=10.0)
    record.add_argument("--out-dir", type=Path, required=True)
    record.add_argument("--label", action="append")
    record.set_defaults(handler=cmd_record)

    from . import tools
    tools.register(sub)

    version = sub.add_parser("version", help="print the software version")
    version.set_defaults(handler=lambda _a: print(f"pasensor {__version__} (Revision {REVISION})") or 0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
