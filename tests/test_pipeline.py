"""Device selection, live capture plumbing, replay, recording, fault injection, and pipeline integration.

Live capture is exercised through a stand-in for the sounddevice module. No
real audio device is opened by this suite.
"""
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from pasensor.acquisition import (LiveSource, ReplaySource, SourceEnded, SourceFault, fault_injecting_factory,
                                  list_input_devices, resolve_device)
from pasensor.config import DEFAULTS
from pasensor.pipeline import Pipeline
from pasensor.recorder import JsonlWriter, SessionRecorder
from tests.helpers import (CollectingState, FakeSoundDevice, angular_error, device, late_rotor, make_config, quiet_scene,
                           rotor_scene, run_pipeline, sim_factory, six_channel, step_gain, write_wav)

DEVICE_CFG = DEFAULTS["device"]

ARRAY_WASAPI = device("Microphone (reSpeaker XVF3800 4-Mic Array)", 1, 6)
ARRAY_MME = device("Microphone (reSpeaker XVF3800 4", 0, 6)
LAPTOP = device("Microphone Array (Realtek Audio)", 1, 2)


class DeviceSelectionTests(unittest.TestCase):
    def test_prefers_configured_host_api_and_name(self):
        sd = FakeSoundDevice([LAPTOP, ARRAY_MME, ARRAY_WASAPI])
        chosen = resolve_device(sd, DEVICE_CFG)
        self.assertEqual((chosen["index"], chosen["hostapi"]), (2, "Windows WASAPI"))
        self.assertEqual(len(list_input_devices(sd)), 3)

    def test_explicit_index_and_name_override(self):
        sd = FakeSoundDevice([LAPTOP, ARRAY_MME, ARRAY_WASAPI])
        self.assertEqual(resolve_device(sd, DEVICE_CFG, override=1)["index"], 1)
        self.assertEqual(resolve_device(sd, DEVICE_CFG, override="2")["index"], 2)
        with self.assertRaises(SourceFault) as caught:
            resolve_device(sd, DEVICE_CFG, override=0)           # the two-channel laptop microphone
        self.assertEqual(caught.exception.kind, "FORMAT")

    def test_missing_device_lists_what_was_seen(self):
        with self.assertRaises(SourceFault) as caught:
            resolve_device(FakeSoundDevice([LAPTOP]), DEVICE_CFG)
        self.assertEqual(caught.exception.kind, "NOT_FOUND")
        self.assertIn("Realtek", str(caught.exception))

    def test_two_channel_endpoint_is_refused(self):
        stereo = device("Microphone (reSpeaker XVF3800 4-Mic Array)", 1, 2)
        with self.assertRaises(SourceFault) as caught:
            resolve_device(FakeSoundDevice([stereo]), DEVICE_CFG)
        self.assertEqual(caught.exception.kind, "FORMAT")

    def test_two_arrays_are_ambiguous(self):
        with self.assertRaises(SourceFault) as caught:
            resolve_device(FakeSoundDevice([ARRAY_WASAPI, ARRAY_WASAPI]), DEVICE_CFG)
        self.assertEqual(caught.exception.kind, "AMBIGUOUS")

    def test_driver_rejecting_the_format_is_reported(self):
        with self.assertRaises(SourceFault) as caught:
            resolve_device(FakeSoundDevice([ARRAY_WASAPI], reject=True), DEVICE_CFG)
        self.assertEqual(caught.exception.kind, "FORMAT")


class LiveSourceTests(unittest.TestCase):
    def open(self, **kwargs):
        sd = FakeSoundDevice([ARRAY_WASAPI])
        source = LiveSource(DEVICE_CFG, 2048, sd=sd, stall_timeout_s=0.4, **kwargs)
        source.open()
        return source, sd.stream

    def test_opens_one_six_channel_stream_at_16k(self):
        source, stream = self.open()
        self.assertEqual((stream.kwargs["channels"], stream.kwargs["samplerate"], stream.kwargs["blocksize"]), (6, 16000.0, 2048))
        self.assertTrue(stream.active)
        source.close()
        self.assertTrue(stream.closed)

    def test_chunks_are_contiguous_and_stamped_from_the_adc_clock(self):
        source, stream = self.open()
        ramp = np.arange(3 * 2048 * 6, dtype=np.float32).reshape(-1, 6)
        for i in range(3):
            stream.feed(ramp[i * 2048:(i + 1) * 2048], adc=100.0 + (i + 1) * 0.128)
        chunks = [source.read() for _ in range(3)]
        self.assertEqual([c.index for c in chunks], [0, 2048, 4096])
        np.testing.assert_array_equal(np.concatenate([c.samples for c in chunks]), ramp)
        self.assertTrue(all(c.time_quality == "adc_clock" and not c.overflow and not c.gap_before for c in chunks))
        self.assertAlmostEqual(chunks[1].time_s - chunks[0].time_s, 0.128, places=6)
        self.assertAlmostEqual(chunks[0].time_s, time.time() + 0.128, delta=2.0)

    def test_odd_buffer_sizes_are_reassembled(self):
        source, stream = self.open()
        data = np.random.default_rng(0).normal(size=(4096, 6)).astype(np.float32)
        for start, stop in ((0, 1000), (1000, 2500), (2500, 4096)):
            stream.feed(data[start:stop])
        np.testing.assert_array_equal(np.concatenate([source.read().samples, source.read().samples]), data)

    def test_driver_overflow_marks_a_gap(self):
        source, stream = self.open()
        stream.feed(np.zeros((2048, 6)))
        stream.feed(np.zeros((2048, 6)), overflow=True)
        first, second = source.read(), source.read()
        self.assertFalse(first.overflow)
        self.assertTrue(second.overflow and second.gap_before)
        self.assertEqual(source.stats["overflow_events"], 1)

    def test_full_queue_drops_are_counted_and_flagged(self):
        source, stream = self.open(queue_blocks=4)
        for _ in range(7):
            stream.feed(np.zeros((2048, 6)))
        self.assertEqual(source.stats["queue_dropped_frames"], 3 * 2048)
        for _ in range(4):
            self.assertFalse(source.read().gap_before)
        stream.feed(np.ones((2048, 6)))
        self.assertTrue(source.read().gap_before)                # the loss is reported, never hidden

    def test_stall_and_disconnect_raise_faults(self):
        source, stream = self.open()
        with self.assertRaises(SourceFault) as caught:
            source.read()
        self.assertEqual(caught.exception.kind, "STALLED")
        stream.active = False
        with self.assertRaises(SourceFault) as caught:
            source.read()
        self.assertEqual(caught.exception.kind, "DISCONNECTED")


class RecorderReplayTests(unittest.TestCase):
    def test_no_audio_files_unless_recording_is_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config()
            log = JsonlWriter(Path(tmp) / "events.jsonl")
            final, _state, _ = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, rotor_scene(), limit_chunks=60), event_log=log)
            log.close()
            self.assertEqual(final, "REPLAY_COMPLETE")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["events.jsonl"])
            self.assertNotIn(b"RIFF", (Path(tmp) / "events.jsonl").read_bytes())

    def test_record_then_replay_round_trip_with_gap_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(recording={"rotate_minutes": 0.1})                 # 6 s files
            recorder = SessionRecorder(tmp, cfg, "SIMULATED", labels={"site": "bench"})
            factory = fault_injecting_factory(sim_factory(cfg, rotor_scene(), limit_chunks=80), {30: "overflow"})
            run_pipeline(cfg, "SIMULATED", factory, recorder=recorder)
            recorder.close()
            self.assertIsNone(recorder.error)
            wavs = sorted(Path(tmp).glob("*.wav"))
            self.assertEqual(len(wavs), 2)                                       # 80 chunks = 10.24 s
            meta = json.loads(wavs[0].with_suffix(".json").read_text())
            self.assertTrue(meta["synthetic"])
            self.assertEqual(meta["labels"], {"site": "bench"})
            self.assertEqual(meta["raw_indices"], [2, 3, 4, 5])
            self.assertEqual([g["sample_index"] for g in meta["gaps"]], [30 * 2048])
            self.assertEqual(meta["config_sha256"], cfg.sha256)
            with wave.open(str(wavs[0])) as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth()), (6, 16000, 2))
                self.assertEqual(w.getnframes(), meta["frames"])
            source = ReplaySource(wavs[0], cfg["device"], 2048, realtime=False)
            source.open()
            chunks = []
            with self.assertRaises(SourceEnded):
                while True:
                    chunks.append(source.read())
            source.close()
            self.assertEqual(chunks[0].time_quality, "recording_clock")
            self.assertAlmostEqual(chunks[0].time_s, meta["start_time_s"], places=6)
            self.assertAlmostEqual(chunks[5].time_s - chunks[0].time_s, 5 * 0.128, places=6)
            self.assertEqual([c.index for c in chunks if c.gap_before], [30 * 2048])

    def test_replay_rejects_wrong_format_and_reads_24_bit(self):
        cfg = make_config()
        with tempfile.TemporaryDirectory() as tmp:
            stereo = write_wav(Path(tmp) / "stereo.wav", np.zeros((4096, 2)))
            with self.assertRaises(SourceFault) as caught:
                ReplaySource(stereo, cfg["device"], 2048).open()
            self.assertEqual(caught.exception.kind, "FORMAT")
            with self.assertRaises(SourceFault):
                ReplaySource(Path(tmp) / "missing.wav", cfg["device"], 2048).open()
            path = Path(tmp) / "pcm24.wav"
            values = np.array([[-8388608, 8388607, 1, -1, 0, 4194304]] * 2048, dtype=np.int32)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(6)
                w.setsampwidth(3)
                w.setframerate(16000)
                w.writeframes(b"".join(int(v).to_bytes(3, "little", signed=True) for v in values.ravel()))
            source = ReplaySource(path, cfg["device"], 2048, realtime=False)
            source.open()
            first = source.read().samples[0]
            source.close()
            np.testing.assert_allclose(first, [-1.0, 8388607 / 8388608, 1 / 8388608, -1 / 8388608, 0.0, 0.5], atol=1e-7)

    def test_replay_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(calibration={"show_uncalibrated_bearing": True})
            scene = rotor_scene(azimuth=200.0, gain=step_gain(4.0, 9.0))
            wav = write_wav(Path(tmp) / "session.wav", six_channel(scene.render(16000 * 12)))
            runs = []
            for _ in range(2):
                factory = lambda: ReplaySource(wav, cfg["device"], 2048, realtime=False)  # noqa: E731
                _final, state, _ = run_pipeline(cfg, "REPLAY", factory)
                runs.append((state.events(), [(r["t"], r["brg"], r["score"], r["cand"]) for r in state.history()]))
            self.assertEqual(runs[0], runs[1])                   # same file and configuration, same results
            self.assertEqual(len(runs[0][0]), 1)
            self.assertEqual(runs[0][0][0]["time_quality"], "file_relative")
            self.assertAlmostEqual(runs[0][0][0]["onset_time_s"], 4.6, delta=0.6)


class PipelineIntegrationTests(unittest.TestCase):
    def test_simulated_candidate_with_bearing_and_labels(self):
        cfg = make_config()
        scene = rotor_scene(azimuth=75.0, gain=step_gain(4.0))
        final, state, pipeline = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, scene, limit_chunks=110))
        self.assertEqual(final, "REPLAY_COMPLETE")
        analysed = [s for s in state.published if s.get("detection") and "harmonic_score" in s["detection"]]
        self.assertTrue(all(s["mode"] == "SIMULATED" for s in state.published))
        before = [s for s in analysed if s["frame_end_s"] - 1_000_000.0 < 3.5]
        self.assertTrue(before and not any(s["detection"]["candidate"] for s in before))
        candidates = [s for s in analysed if s["detection"]["candidate"]]
        self.assertGreater(len(candidates), 30)
        onset = candidates[0]["frame_end_s"] - 1_000_000.0
        self.assertLess(onset - 4.0, 1.2)                         # block length plus four qualifying steps
        shown = [s["bearing"] for s in candidates if s["bearing"]["available"]]
        self.assertGreater(len(shown), 0.8 * len(candidates))
        self.assertLess(max(angular_error(b["bearing_local_deg"], 75.0) for b in shown), 5.0)
        self.assertTrue(all(b["simulated_geometry"] and not b["calibrated"] for b in shown))
        self.assertEqual(candidates[0]["identity"]["available"], False)
        event = state.events()[0]
        self.assertEqual(event["mode"], "SIMULATED")
        self.assertAlmostEqual(event["evidence"]["median_f0_hz"], 165.0, delta=3.0)
        self.assertEqual(pipeline.stats["candidates"], 1)
        json.dumps(state.snapshot(), allow_nan=False)

    def test_source_present_from_start_up_is_masked(self):
        # Known limit, kept visible: the ambient floor is learned from what is heard first. A source that
        # is already sounding at start-up sets the floor and does not rise above it.
        cfg = make_config()
        _final, state, pipeline = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, rotor_scene(), limit_chunks=80))
        self.assertEqual(pipeline.stats["candidates"], 0)
        last = [s for s in state.published if s.get("detection") and "blocked_by" in s["detection"]][-1]
        self.assertEqual(last["detection"]["blocked_by"], ["LEVEL_BELOW_MARGIN"])
        self.assertGreater(last["detection"]["harmonic_score"], 0.5)        # the evidence is still shown

    def test_quiet_scene_raises_nothing(self):
        cfg = make_config()
        _final, state, pipeline = run_pipeline(cfg, "SIMULATED", sim_factory(cfg, quiet_scene(), limit_chunks=240))
        self.assertEqual(pipeline.stats["candidates"], 0)
        shown = [s for s in state.published if s.get("bearing") and s["bearing"]["available"]]
        self.assertEqual(len(shown), 0)                           # ambient noise alone never yields a bearing

    def test_uncalibrated_live_bearing_is_withheld(self):
        cfg = make_config()
        scene = late_rotor(azimuth=75.0)
        _final, state, _ = run_pipeline(cfg, "LIVE", sim_factory(cfg, scene, mode="LIVE", limit_chunks=60))
        analysed = [s for s in state.published if s.get("bearing") and "metrics" in s["bearing"]]
        self.assertTrue(analysed)
        self.assertFalse(any(s["bearing"]["available"] for s in analysed))
        self.assertTrue(any("UNCALIBRATED" in s["bearing"]["reasons"] for s in analysed))
        self.assertTrue(any(s["detection"]["candidate"] for s in analysed))   # detection still works
        self.assertIsNone(state.events()[0]["bearing"]["mean_deg"])

    def test_calibrated_bearing_applies_offset_mirror_and_site_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = {"schema": "pasensor.calibration-record.v1", "evidence_kind": "physical", "node_id": "ACOUSTIC-01",
                      "raw_indices": [2, 3, 4, 5], "geometry_m": DEFAULTS["array"]["geometry_m"],
                      "result": {"passed": True, "mirror": True, "local_angle_offset_deg": 30.0}}
            (Path(tmp) / "cal.json").write_text(json.dumps(record))
            cfg = make_config(base_dir=Path(tmp), site={"heading_deg": 90.0, "east_m": 0.0, "north_m": 0.0},
                              calibration={"geometry_validated": True, "record": "cal.json", "mirror": True,
                                           "local_angle_offset_deg": 30.0, "bearing_sigma_deg": 5.0})
            scene = rotor_scene(azimuth=75.0)
            _final, state, _ = run_pipeline(cfg, "LIVE", sim_factory(cfg, scene, mode="LIVE", limit_chunks=60))
        shown = [s["bearing"] for s in state.published if s.get("bearing") and s["bearing"]["available"]]
        self.assertTrue(shown)
        b = shown[-1]
        self.assertTrue(b["calibrated"])
        self.assertLess(angular_error(b["bearing_local_deg"], (-75.0 + 30.0) % 360.0), 3.0)   # mirror, then offset
        self.assertLess(angular_error(b["bearing_site_deg"], (90.0 - 45.0) % 360.0), 3.0)     # heading 90 added
        self.assertLess(angular_error(b["compass_deg"], 45.0), 3.0)
        self.assertEqual(b["sigma_deg"], 5.0)

    def test_overflow_block_is_excluded_and_reported(self):
        cfg = make_config()
        factory = fault_injecting_factory(sim_factory(cfg, late_rotor(), limit_chunks=80), {40: "overflow", 41: "overflow"})
        _final, state, pipeline = run_pipeline(cfg, "SIMULATED", factory)
        self.assertEqual(pipeline.stats["overflow_count"], 2)
        hit = [s for s in state.published if (s.get("acquisition") or {}).get("status") == "OVERFLOW"]
        self.assertEqual(len(hit), 2)
        for status in hit:
            self.assertFalse(status["bearing"]["available"])
            self.assertFalse(status["detection"]["candidate"])
            self.assertIn("OVERFLOW", status["bearing"]["reasons"])
        self.assertEqual(pipeline.stats["frames_analysed"], 80 - 1 - 2)       # start-up chunk and the two lost chunks
        self.assertTrue(any(e.get("end_note") == "ended by lost samples" for e in state.events()))

    def test_bad_audio_blocks_give_no_candidate_or_bearing(self):
        cfg = make_config()
        for action, expected in (("nan", "NONFINITE"), ("clip", "CLIPPING"), ("dead:3", "DEAD_CHANNEL")):
            with self.subTest(action=action):
                factory = fault_injecting_factory(sim_factory(cfg, late_rotor(), limit_chunks=50), {30: action, 31: action})
                _final, state, _ = run_pipeline(cfg, "SIMULATED", factory)
                bad = [s for s in state.published if s.get("audio") and s["audio"]["status"] == expected]
                self.assertTrue(bad)
                for status in bad:
                    self.assertFalse(status["bearing"]["available"])
                    self.assertIn("AUDIO_FAULT", status["detection"]["blocked_by"])


class PolarityWarningTests(unittest.TestCase):
    def test_inverted_microphone_raises_a_visible_warning(self):
        cfg = make_config()
        factory = sim_factory(cfg, late_rotor(), limit_chunks=60)

        def inverted():
            source = factory()
            inner = source.read

            def read():
                chunk = inner()
                chunk.samples[:, 4] *= -1.0                      # capture channel 4 is microphone index 2
                return chunk
            source.read = read
            return source
        _final, state, _ = run_pipeline(cfg, "SIMULATED", inverted)
        warned = [s for s in state.published if s.get("audio") and "POLARITY_SUSPECT" in s["audio"]["warnings"]]
        self.assertGreater(len(warned), 10)
        self.assertFalse(any(s["bearing"]["available"] for s in warned))


class RecoveryTests(unittest.TestCase):
    def live(self, plan, chunks=90, **cfg_changes):
        cfg = make_config(health={"restart_backoff_s": [0.0]}, **cfg_changes)
        factory = fault_injecting_factory(sim_factory(cfg, late_rotor(), mode="LIVE", limit_chunks=chunks), plan)
        return run_pipeline(cfg, "LIVE", factory)

    def test_live_recovers_from_disconnect_and_stall(self):
        # Each restart builds a fresh source, so the run ends when the last one reaches its chunk limit.
        _final, state, pipeline = self.live({20: "disconnect", 50: "stall"})
        self.assertEqual(pipeline.stats["opens"], 3)
        self.assertEqual(pipeline.stats["fault_count"], 2)
        states = [s["state"] for s in state.published]
        self.assertIn("FAULT", states)
        self.assertIn("RECONNECTING", states)
        fault = next(s for s in state.published if s["state"] == "FAULT")
        self.assertIsNone(fault["bearing"])                       # nothing current is shown during a fault
        self.assertIn("unplugged", fault["message"])
        after = state.published[max(i for i, s in enumerate(state.published) if s["state"] == "RECONNECTING") + 1:]
        self.assertTrue(any(s.get("detection") and s["detection"]["candidate"] for s in after))
        self.assertEqual(after[-2]["acquisition"]["restarts"], 2)

    def test_device_missing_at_start_then_appears(self):
        _final, state, pipeline = self.live({"open_failures": 2}, chunks=40)
        self.assertEqual(pipeline.stats["fault_count"], 2)
        self.assertEqual(pipeline.stats["opens"], 1)
        self.assertGreater(pipeline.stats["frames_analysed"], 30)

    def test_software_fault_restarts_then_gives_up_when_repeated(self):
        _final, _state, pipeline = self.live({25: "crash"})
        self.assertEqual(pipeline.stats["software_faults"], 1)
        self.assertEqual(pipeline.stats["opens"], 2)
        final, state, pipeline = self.live({i: "crash" for i in range(0, 400)})
        self.assertEqual(final, "FAILED")
        self.assertEqual(pipeline.stats["software_faults"], 5)
        self.assertEqual(state.snapshot()["status"]["state"], "FAILED")

    def test_replay_fault_is_final(self):
        cfg = make_config()
        factory = fault_injecting_factory(sim_factory(cfg, rotor_scene(), mode="REPLAY", limit_chunks=50), {10: "crash"})
        final, _state, pipeline = run_pipeline(cfg, "REPLAY", factory)
        self.assertEqual(final, "FAILED")
        self.assertEqual(pipeline.stats["opens"], 1)              # a replay is never restarted silently

    def test_health_records_are_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(health={"restart_backoff_s": [0.0]})
            log = JsonlWriter(Path(tmp) / "events.jsonl")
            factory = fault_injecting_factory(sim_factory(cfg, late_rotor(), mode="LIVE", limit_chunks=60),
                                              {20: "disconnect", 50: "overflow"})
            run_pipeline(cfg, "LIVE", factory, event_log=log)
            log.close()
            records = [json.loads(line) for line in (Path(tmp) / "events.jsonl").read_text().splitlines()]
        kinds = [r["kind"] for r in records if r["record"] == "health"]
        self.assertIn("DISCONNECTED", kinds)
        self.assertIn("OVERFLOW", kinds)
        self.assertTrue(any(r["record"] == "candidate" for r in records))

    def test_stop_event_ends_the_run(self):
        cfg = make_config()
        state = CollectingState()
        pipeline = Pipeline(cfg, "SIMULATED", sim_factory(cfg, quiet_scene()), state)
        stop = threading.Event()
        worker = threading.Thread(target=pipeline.run, args=(stop,))
        worker.start()
        time.sleep(0.5)
        stop.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(state.snapshot()["status"]["state"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
