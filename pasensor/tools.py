"""Command line tools registered under python -m pasensor.

chanmap, chaninfo, calibrate   commissioning a physical unit
evaluate                       event-level metrics on labelled recordings
fuse                           multi-node bearing fusion
train, make-synthetic-dataset  classifier training workflow
purge                          delete old raw recordings (retention)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _config(args):
    from .cli import default_config_path
    from .config import ConfigError, load_config
    try:
        return load_config(args.config or default_config_path())
    except ConfigError as error:
        raise SystemExit(f"Configuration error: {error}")


def _write(path, payload) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(payload if isinstance(payload, str) else json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- chanmap
def cmd_chanmap(args) -> int:
    import numpy as np
    from .calibration import channel_map, tap_analysis
    from .ml import read_wav
    cfg = _config(args)
    labels = args.labels.split(",")
    if args.wav:
        samples, rate = read_wav(args.wav)
        segments = [tuple(float(v) for v in s.split("-")) for s in args.segments.split(",")]
        result = channel_map(samples, rate, segments, labels, cfg)
    else:
        from .acquisition import LiveSource, SourceFault
        hop = cfg["processing"]["hop_frames"]
        source = LiveSource(cfg["device"], hop, override=args.device)
        try:
            source.open()
        except SourceFault as error:
            print(f"Cannot open the device: {error}", file=sys.stderr)
            return 2
        taps = []
        try:
            for label in labels:
                input(f"Get ready to tap or scratch gently beside {label} only. Press Enter, then tap for {args.seconds:.0f} s.")
                while source._queue.qsize():          # discard audio captured while waiting for the operator
                    source.read()
                chunks = [source.read().samples for _ in range(int(args.seconds * cfg.sample_rate / hop))]
                tap = tap_analysis(np.concatenate(chunks))
                tap["label"] = label
                taps.append(tap)
                print(f"  {label}: loudest capture channel {tap['loudest_channel']} (leads by {tap['margin_db']:.1f} dB)")
        finally:
            source.close()
        suggested = [t["loudest_channel"] for t in taps]
        configured = list(cfg["device"]["raw_indices"])
        result = {"taps": taps, "suggested_raw_indices": suggested, "configured_raw_indices": configured,
                  "matches_configuration": suggested == configured,
                  "problems": [f"{t['label']}: weak margin, repeat" for t in taps if not t["confident"]]
                  + (["two microphones mapped to one channel"] if len(set(suggested)) != len(suggested) else [])}
    result.update(node_id=cfg.node_id, labels=labels)
    print(json.dumps(result, indent=2))
    if args.out:
        _write(args.out, result)
    return 0 if result["matches_configuration"] and not result["problems"] else 1


def cmd_chaninfo(args) -> int:
    from .calibration import channel_report
    from .ml import read_wav
    cfg = _config(args)
    samples, rate = read_wav(args.wav)
    report = channel_report(samples, rate, cfg["device"]["raw_indices"][0])
    print(f"Reference: capture channel {report['reference_channel']} (first configured raw microphone)")
    print(f"{'channel':>7}  {'rms dBFS':>9}  {'peak dBFS':>9}  {'delay ms':>9}  role in configuration")
    for ch in report["channels"]:
        role = "raw microphone" if ch["channel"] in cfg["device"]["raw_indices"] else "not used (processed output?)"
        print(f"{ch['channel']:>7}  {ch['rms_dbfs']:>9.1f}  {ch['peak_dbfs']:>9.1f}  {ch['delay_ms_vs_reference']:>9.3f}  {role}")
    print("Raw microphones on one board should sit within about 0.3 ms of each other and at similar levels.")
    if args.out:
        _write(args.out, report)
    return 0


# -------------------------------------------------------------- calibrate
def cmd_calibrate(args) -> int:
    from .calibration import run_calibration
    cfg = _config(args)
    record = run_calibration(args.manifest, cfg, operator=args.operator or "")
    _write(args.out, record)
    result = record["result"]
    print(f"Evidence kind: {record['evidence_kind']}")
    for p in record["positions"]:
        mean = "none" if p["raw_mean_deg"] is None else f"{p['raw_mean_deg']:.1f}"
        print(f"  {p['file']}: truth {p['true_azimuth_deg']:.0f}, raw mean {mean}, "
              f"bearing in {100 * (1 - p['unavailable_fraction']):.0f}% of frames")
    if result["local_angle_offset_deg"] is not None:
        print(f"Fit: mirror={result['mirror']}, offset={result['local_angle_offset_deg']:.1f} deg, "
              f"median error {result['median_abs_error_deg']:.1f} deg, 95th percentile {result['p95_abs_error_deg']:.1f} deg")
    for problem in result["problems"]:
        print(f"PROBLEM: {problem}")
    print(f"Result: {'PASSED' if result['passed'] else 'NOT PASSED'}. Record written to {args.out}")
    if result["passed"] and record["evidence_kind"] == "physical":
        print("To accept it, set these values in the node configuration:")
        print(json.dumps({"calibration": {"geometry_validated": True, "record": str(args.out),
                                          "local_angle_offset_deg": round(result["local_angle_offset_deg"], 2),
                                          "mirror": result["mirror"],
                                          "bearing_sigma_deg": round(result["bearing_sigma_deg"], 1)}}, indent=2))
    elif result["passed"]:
        print("This record was not made from physical recordings, so the configuration will not accept it.")
    return 0 if result["passed"] else 1


# --------------------------------------------------------------- evaluate
def cmd_evaluate(args) -> int:
    from .evaluate import evaluate, report_markdown
    cfg = _config(args)
    report = evaluate(args.manifest, cfg)
    text = report_markdown(report)
    print(text)
    if args.out:
        _write(Path(args.out).with_suffix(".json"), report)
        _write(Path(args.out).with_suffix(".md"), text)
    return 0 if not report["failed_sessions"] else 1


# ------------------------------------------------------------------- fuse
def cmd_fuse(args) -> int:
    from .multinode import FusionRejected, fuse_tracks, load_site, observation_to_bearing
    try:
        site = load_site(args.site)
    except FusionRejected as error:
        print(json.dumps({"status": "rejected", "code": error.code, "reason": str(error)}), file=sys.stderr)
        return 2
    tracks: dict = {}
    skipped = 0
    for path in args.observations:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                bearing = observation_to_bearing(json.loads(line), site, allow_synthetic=args.allow_synthetic)
            except (FusionRejected, ValueError):
                skipped += 1
                continue
            if bearing.time_s is not None:
                tracks.setdefault(bearing.node_id, []).append((bearing.time_s, bearing.site_azimuth_deg, bearing.sigma_deg))
    for track in tracks.values():
        track.sort()
    if len(tracks) < 2:
        print(json.dumps({"status": "rejected", "code": "TOO_FEW_NODES",
                          "reason": f"usable observations came from {len(tracks)} node(s); skipped {skipped} records"}), file=sys.stderr)
        return 2
    start = max(t[0][0] for t in tracks.values())
    end = min(t[-1][0] for t in tracks.values())
    fixes = rejected = 0
    t = start
    while t <= end:
        try:
            result = fuse_tracks(tracks, site, t, monte_carlo=args.monte_carlo)
            fixes += 1
        except FusionRejected as error:
            result = {"status": "rejected", "code": error.code, "reason": str(error), "emission_time_s": t}
            rejected += 1
        print(json.dumps(result))
        t += args.step
    print(json.dumps({"summary": True, "estimates": fixes, "rejected": rejected, "skipped_records": skipped}), file=sys.stderr)
    return 0 if fixes else 1


# ------------------------------------------------------------------ train
def cmd_train(args) -> int:
    from .ml import DatasetError, train
    cfg = _config(args)
    try:
        model = train(args.manifest, args.out, group_by=tuple(args.group_by.split(",")), target_fpr=args.target_fpr,
                      provenance=args.provenance or "", raw_indices=cfg["device"]["raw_indices"],
                      capture_channels=cfg["device"]["capture_channels"])
    except DatasetError as error:
        print(f"Dataset error: {error}", file=sys.stderr)
        return 2
    print((Path(args.out) / "MODEL_CARD.md").read_text(encoding="utf-8"))
    print(f"Model written to {Path(args.out) / 'model.json'} (id {model['model_id']}).")
    return 0


def cmd_synthetic(args) -> int:
    from .ml import make_synthetic_dataset
    manifest = make_synthetic_dataset(args.out, sessions=args.sessions, seconds=args.seconds)
    print(f"SYNTHETIC dataset written. Manifest: {manifest}. Use it to test the pipeline only.")
    return 0


# ------------------------------------------------------------------ purge
def cmd_purge(args) -> int:
    cutoff = time.time() - args.older_than_days * 86400.0
    victims = []
    for wav in sorted(Path(args.dir).glob("*.wav")):
        sidecar = wav.with_suffix(".json")
        if not sidecar.exists():
            continue                                   # only files this software recorded
        try:
            started = float(json.loads(sidecar.read_text(encoding="utf-8"))["start_time_s"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if started < cutoff:
            victims.append((wav, sidecar))
    for wav, sidecar in victims:
        print(("DELETE " if args.confirm else "would delete ") + str(wav))
        if args.confirm:
            wav.unlink()
            sidecar.unlink()
    print(f"{len(victims)} recording(s) older than {args.older_than_days} days."
          + ("" if args.confirm else " Nothing was deleted. Add --confirm to delete them permanently."))
    return 0


def register(sub) -> None:
    def add(name, handler, help_text):
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("--config", type=Path)
        parser.set_defaults(handler=handler)
        return parser

    p = add("chanmap", cmd_chanmap, "identify which capture channel belongs to each physical microphone")
    p.add_argument("--labels", default="MIC1,MIC2,MIC3,MIC4", help="physical labels in the order of array.geometry_m")
    p.add_argument("--wav", type=Path, help="recording with one tap segment per microphone")
    p.add_argument("--segments", help="with --wav: start-end seconds per label, e.g. 1-3,5-7,9-11,13-15")
    p.add_argument("--device", help="live: device index or name")
    p.add_argument("--seconds", type=float, default=3.0, help="live: listening time per microphone")
    p.add_argument("--out", type=Path)

    p = add("chaninfo", cmd_chaninfo, "report level and delay of every capture channel in a recording")
    p.add_argument("--wav", type=Path, required=True)
    p.add_argument("--out", type=Path)

    p = add("calibrate", cmd_calibrate, "build a bearing calibration record from surveyed recordings")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--operator")

    p = add("evaluate", cmd_evaluate, "event-level metrics on labelled recordings")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, help="write <out>.json and <out>.md")

    p = sub.add_parser("fuse", help="bearing-only position estimates from several nodes' observation logs")
    p.add_argument("--site", type=Path, required=True)
    p.add_argument("--observations", type=Path, nargs="+", required=True)
    p.add_argument("--step", type=float, default=1.0, help="seconds between estimates")
    p.add_argument("--monte-carlo", type=int, default=0)
    p.add_argument("--allow-synthetic", action="store_true", help="accept simulated observations (test data only)")
    p.set_defaults(handler=cmd_fuse)

    p = add("train", cmd_train, "train and evaluate a classifier from a labelled manifest")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--group-by", default="session_id", help="comma list of manifest columns that define a group")
    p.add_argument("--target-fpr", type=float, default=0.01)
    p.add_argument("--provenance", help="where the recordings came from and their licences")

    p = sub.add_parser("make-synthetic-dataset", help="write a small synthetic dataset to test the training pipeline")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sessions", type=int, default=12)
    p.add_argument("--seconds", type=float, default=12.0)
    p.set_defaults(handler=cmd_synthetic)

    p = sub.add_parser("purge", help="list or delete raw recordings older than a number of days")
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--older-than-days", type=float, required=True)
    p.add_argument("--confirm", action="store_true", help="actually delete (permanent)")
    p.set_defaults(handler=cmd_purge)
