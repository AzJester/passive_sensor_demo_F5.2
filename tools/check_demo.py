"""Run the demo page's built-in self-test in a headless browser and check the result.

    python tools/check_demo.py                      check only
    python tools/check_demo.py --screenshots DIR    also save pictures of the main views

The demo (demo/index.html) synthesizes four microphone signals in the browser
and runs a simplified JavaScript estimator on them. This tool checks that the
page runs without script errors and that its logic behaves as designed on its
own simulated scenes. It says nothing about real audio or real hardware.

It needs a Chromium-based browser (Microsoft Edge or Google Chrome). It opens
the page from disk and uses no network.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "demo" / "index.html"
BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "msedge", "google-chrome", "chromium", "chromium-browser", "chrome")
VIEWS = (("sensor_candidate", "fast=40", 1500, 1250), ("sensor_false_alert_source", "fast=40&scenario=fan", 1500, 1250),
         ("sensor_tone_rejected", "fast=40&scenario=tone", 1500, 1250), ("sensor_input_stale", "fast=40&fault=stall", 1500, 1250),
         ("hardware_head", "tab=3d", 1500, 900), ("hardware_exploded", "tab=3d&explode=100&xray=0", 1500, 900),
         ("hardware_from_above", "tab=3d&view=top&nocap=1", 1500, 900), ("hardware_from_below", "tab=3d&view=under&xray=0", 1500, 900),
         ("hardware_deployed", "tab=3d&view=site", 1500, 900))


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run_browser(browser: str, arguments: list, query: str, timeout: float = 600.0, page: Path | None = None) -> str:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        command = [browser, "--headless=new", "--enable-unsafe-swiftshader", "--hide-scrollbars", f"--user-data-dir={profile}",
                   *arguments, (page or PAGE).as_uri() + "?" + query]
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    return result.stdout.decode("utf-8", "replace")


def narrow_check(browser: str) -> tuple:
    """(passed, description). Loads the page twice in 390 px wide frames, one per tab, because a headless
    browser window cannot be made that narrow. Neither tab may scroll sideways and the 3D tab must stack."""
    inner = html.escape(PAGE.read_text(encoding="utf-8"), quote=True)
    frame = '<iframe id="{0}" style="width:390px;height:900px;border:0" srcdoc="{1}"></iframe>'
    script = ('<script>setTimeout(function () { const out = []; for (const id of ["a", "b"]) { '
              'const w = document.getElementById(id).contentWindow; if (id === "b") w.showTab("3d"); '
              'const d = w.document.documentElement, stage = w.document.getElementById("stage").getBoundingClientRect(), '
              'panel = w.document.querySelector(".panel3d").getBoundingClientRect(); '
              'out.push(d.scrollWidth + "/" + d.clientWidth + (id === "b" ? "/" + (panel.top >= stage.bottom ? "stacked" : "side") : "")); } '
              'document.body.dataset.narrow = out.join(" "); }, 1500);</script>')
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        wrapper = Path(folder) / "narrow.html"
        wrapper.write_text('<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">'
                           + frame.format("a", inner) + frame.format("b", inner) + script + "</body></html>", encoding="utf-8")
        dom = run_browser(browser, ["--window-size=820,920", "--virtual-time-budget=4000", "--dump-dom"], "narrow=1", page=wrapper)
    found = re.search(r'data-narrow="(\d+)/(\d+) (\d+)/(\d+)/(\w+)"', dom)
    if not found:
        return False, "narrow screen: the 390 px frames gave no measurement"
    sim_scroll, sim_width, hw_scroll, hw_width, layout = found.groups()
    passed = int(sim_scroll) <= int(sim_width) and int(hw_scroll) <= int(hw_width) and layout == "stacked"
    return passed, (f"narrow screen: in a {sim_width} px wide frame the sensor tab is {sim_scroll} px wide and the 3D tab is {hw_scroll} px wide, "
                    f"and the 3D view and its parts table are {layout}")


def parse(dom: str) -> tuple:
    """(script error text, cable length in mm, gesture result, {scene name: {field: value}})."""
    error = re.search(r'<div id="err"[^>]*>([^<]*)', dom)
    cable = re.search(r'data-cable-mm="(\d+)"', dom)
    gesture = re.search(r'data-gesture="(\w+)"', dom)
    block = re.search(r'<pre id="selftest">(.*?)</pre>', dom, re.S)
    scenes = {}
    for line in (block.group(1).strip().splitlines() if block else []):
        words = line.split()
        fields = dict(word.split("=", 1) for word in words[3:])
        scenes[" ".join(words[:3])] = {key: (None if value == "n/a" else float(value)) for key, value in fields.items()}
    return ((error.group(1).strip() if error else "page did not load"), (int(cable.group(1)) if cable else None),
            (gesture.group(1) if gesture else "not run"), scenes)


def checks(error: str, cable, gesture: str, scenes: dict) -> list:
    """Each item is (passed, description). The numbers come from simulated scenes, not from hardware."""
    def scene(name):
        return scenes.get(name, {})

    orbit, far, weak = scene("orbit 40m -62dB"), scene("orbit 120m -62dB"), scene("orbit 250m -50dB")
    fan, tone, quiet = scene("fan 40m -62dB"), scene("tone 40m -62dB"), scene("quiet 40m -62dB")
    return [
        (error == "", f"page ran with no script errors ({error or 'none'})"),
        (len(scenes) == 6, f"self-test reported {len(scenes)} of 6 scenes"),
        (cable is not None and cable < 2000, f"cable W1 path in the 3D mock-up is {cable} mm, under the 2000 mm cable"),
        (gesture == "ok", f"3D view: a scripted one-pointer drag turns it and a two-pointer spread zooms it ({gesture})"),
        (bool(orbit) and orbit["candidateFrames"] == orbit["frames"] and orbit["bearingShown"] >= 0.95 * orbit["frames"] and orbit["meanErr"] < 3.0,
         f"simulated multirotor at 40 m: candidate on every frame, mean bearing error {orbit.get('meanErr')} deg"),
        (bool(far) and far["candidateFrames"] >= 0.95 * far["frames"] and far["meanErr"] < 3.0,
         f"simulated multirotor at 120 m: mean bearing error {far.get('meanErr')} deg"),
        (bool(weak) and weak["candidateFrames"] == 0, "simulated multirotor below the level gate raises no candidate"),
        (bool(fan) and fan["candidateFrames"] > 0, "simulated machine hum raises a candidate: the heuristic cannot tell it from a rotor, and the page says so"),
        (bool(tone) and tone["candidateFrames"] == 0 and tone["bearingShown"] == 0, "single tone: no candidate and no bearing"),
        (bool(quiet) and quiet["candidateFrames"] == 0 and quiet["bearingShown"] == 0, "ambient noise only: no candidate and no bearing"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--browser", help="path to msedge or chrome")
    parser.add_argument("--screenshots", metavar="DIR", help="also save PNG pictures of the main views in DIR")
    arguments = parser.parse_args()
    browser = arguments.browser or find_browser()
    if not browser:
        print("No Chromium-based browser found. Pass --browser PATH.")
        return 2
    print(f"browser: {browser}")
    print(f"page:    {PAGE.relative_to(ROOT).as_posix()} ({PAGE.stat().st_size} bytes)")
    error, cable, gesture, scenes = parse(run_browser(browser, ["--virtual-time-budget=3000", "--dump-dom"], "selftest=1"))
    print("\nself-test output (simulated scenes, simplified JavaScript estimator):")
    for name, fields in scenes.items():
        print(f"  {name:<20} " + "  ".join(f"{key}={'n/a' if value is None else format(value, 'g')}" for key, value in fields.items()))
    print()
    failed = 0
    for passed, description in checks(error, cable, gesture, scenes) + [narrow_check(browser)]:
        print(("PASS  " if passed else "FAIL  ") + description)
        failed += not passed
    if arguments.screenshots:
        folder = Path(arguments.screenshots).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        for name, query, width, height in VIEWS:
            target = folder / f"{name}.png"
            run_browser(browser, [f"--window-size={width},{height}", "--virtual-time-budget=4500", f"--screenshot={target}"], query)
            print(f"wrote {target.name}" if target.exists() else f"FAILED to write {target.name}")
            failed += not target.exists()
    print("\n" + ("demo check passed" if not failed else f"demo check FAILED ({failed})"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
