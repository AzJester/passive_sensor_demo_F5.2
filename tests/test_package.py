"""Consistency of the delivered package: versions, documents, BOM, CAD, and checksums."""
import csv
import hashlib
import importlib.util
import json
import math
import re
import sys
import unittest
import xml.etree.ElementTree as ET

import pasensor
from tests.helpers import ROOT

TEXT_SUFFIXES = {".md", ".py", ".json", ".csv", ".txt", ".ps1", ".cmd", ".html", ".js", ".css", ".scad", ".svg"}
SKIPPED_DIRS = {".venv", "__pycache__", "dist", "recordings", "logs", "legacy"}
DRAWINGS = ("SLM-PAS-M01", "SLM-PAS-M02", "SLM-PAS-A01", "SLM-PAS-E01")


def load_cad():
    """Import hardware/make_cad.py, which is a script and not part of the package."""
    if "make_cad" in sys.modules:
        return sys.modules["make_cad"]
    spec = importlib.util.spec_from_file_location("make_cad", ROOT / "hardware" / "make_cad.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_cad"] = module
    spec.loader.exec_module(module)
    return module


def text_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if path.is_file() and path.suffix in TEXT_SUFFIXES and not (set(relative.parts) & SKIPPED_DIRS):
            yield path


class PackageTests(unittest.TestCase):
    def test_version_is_consistent(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(pasensor.__version__, version)
        self.assertIn(f"## {version},", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn(f"software version {version}", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_house_style_no_long_dashes(self):
        long_dashes = re.compile("[" + chr(0x2013) + chr(0x2014) + "]")      # en dash and em dash
        offenders = [str(p.relative_to(ROOT)) for p in text_files()
                     if long_dashes.search(p.read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual(offenders, [])

    def test_documents_the_readme_points_to_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://azjester.github.io/passive_sensor_demo_F5.2/", "\n".join(readme.splitlines()[:6]), "the public demo link belongs at the top")
        for name in re.findall(r"`((?:docs|hardware|firmware|records|config|tools|legacy|demo)/[A-Za-z0-9_./-]*)`", readme):
            self.assertTrue((ROOT / name).exists(), name)
        for name in ("docs/AUDIT.md", "docs/REQUIREMENTS.md", "docs/TEST_PROCEDURES.md", "docs/VERIFICATION_REPORT.md",
                     "docs/LIMITATIONS.md", "docs/OPERATING.md", "docs/DSP_NOTES.md", "docs/MULTINODE.md",
                     "docs/ML_WORKFLOW.md", "hardware/BOM.md", "hardware/HARDWARE_BUILD.md", "firmware/PROVISIONING.md"):
            self.assertTrue((ROOT / name).exists(), name)

    def test_requirement_ids_are_unique_and_have_a_status(self):
        text = (ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
        rows = [line.split("|") for line in text.splitlines() if re.match(r"\| [A-Z]{2,3}-\d\d \|", line)]
        ids = [row[1].strip() for row in rows]
        self.assertGreater(len(ids), 50)
        self.assertEqual(len(ids), len(set(ids)))
        statuses = ("SW verified", "Demonstrated (synthetic)", "Bench pending", "Field pending", "Target", "Not started",
                    "Templates provided", "Procedural", "Stated", "Verified by inspection")
        for row in rows:
            self.assertEqual(len(row), 7, row[1])
            self.assertTrue(row[2].strip() and row[3].strip() and row[4].strip(), row[1])
            self.assertTrue(any(s in row[5] for s in statuses), f"{row[1]}: {row[5]}")

    def test_tests_named_in_the_requirements_exist(self):
        text = (ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
        source = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py"))
        for name in sorted(set(re.findall(r"`((?:test_[a-z0-9_]+)|(?:[A-Z][A-Za-z]+Tests))`", text))):
            self.assertRegex(source, rf"(def|class) {name}\b", name)

    def test_bom_rows_are_complete_and_dated(self):
        with open(ROOT / "hardware" / "bom.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 15)
        for row in rows:
            for key in ("ref", "item", "manufacturer", "part_number", "quantity", "unit_price_usd", "price_seen", "price_date", "url"):
                self.assertTrue(row[key].strip(), f"{row['ref']}: {key}")
            self.assertTrue(row["url"].startswith("https://"))
            self.assertRegex(row["price_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn(row["price_seen"], ("page", "search excerpt"))
            float(row["unit_price_usd"])
        required = sum(float(r["unit_price_usd"]) for r in rows if r["group"] == "required")
        self.assertAlmostEqual(required, 208.99, places=2)                      # matches the subtotal printed in BOM.md
        self.assertIn("208.99", (ROOT / "hardware" / "BOM.md").read_text(encoding="utf-8"))

    def test_cad_outputs_match_the_generator(self):
        cad = load_cad()
        outputs = cad.outputs()
        self.assertEqual(sorted(outputs), sorted(
            ["cad/carrier.scad", "cad/carrier.dxf", "drawings/SLM-PAS-DRAWING-SET.pdf"]
            + [f"drawings/{number}.{kind}" for number in DRAWINGS for kind in ("svg", "pdf")]))
        for name, content in outputs.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            self.assertEqual((ROOT / "hardware" / name).read_bytes(), data, f"{name} is out of date; run hardware/make_cad.py")
        self.assertEqual(sorted(p.name for p in (ROOT / "hardware" / "cad").iterdir()), ["carrier.dxf", "carrier.scad"])
        dxf = (ROOT / "hardware" / "cad" / "carrier.dxf").read_text(encoding="utf-8").split("\n")
        self.assertEqual((dxf.count("CIRCLE"), dxf.count("ARC")), (7, 4))       # 6 holes + case outline; 2 slots
        scad = (ROOT / "hardware" / "cad" / "carrier.scad").read_text(encoding="utf-8")
        self.assertEqual(scad.count("{"), scad.count("}"))
        self.assertIn("not cut or fitted", scad)

    def test_drawing_sheets_are_branded_numbered_and_preliminary(self):
        cad = load_cad()
        for number, make in cad.SHEETS:
            sheet = make()
            words = [box[4] for box in sheet.text_boxes()]
            self.assertIn("S&L MILESTONE SOLUTIONS", words, number)
            self.assertIn(number, words)
            self.assertIn("PRELIMINARY", words, number)                       # nothing has been built or checked by a person
            self.assertIn("DO NOT SCALE DRAWING", words, number)
            self.assertIn(cad.REVISION, words, number)
            root = ET.parse(ROOT / "hardware" / "drawings" / f"{number}.svg").getroot()
            self.assertEqual(root.get("viewBox"), "0 0 431.8 279.4", number)  # ANSI B, millimetres
            joined = " ".join(words).upper()
            for banned in ("CLASSIFIED", "CUI", "FOUO", "DISTRIBUTION STATEMENT", "FABRICATION READY", "RELEASED FOR"):
                self.assertNotIn(banned, joined, number)

    def test_drawing_text_does_not_collide_or_leave_the_sheet(self):
        cad = load_cad()
        for number, make in cad.SHEETS:
            sheet = make()
            boxes = sheet.text_boxes()
            self.assertGreater(len(boxes), 60, number)
            for index, a in enumerate(boxes):
                self.assertTrue(a[0] >= 6 and a[1] >= 3 and a[2] <= sheet.width - 6 and a[3] <= sheet.height - 2, f"{number}: {a[4]!r} leaves the sheet")
                for b in boxes[index + 1:]:
                    overlap = min(a[2], b[2]) - max(a[0], b[0]) > 0.3 and min(a[3], b[3]) - max(a[1], b[1]) > 0.3
                    self.assertFalse(overlap, f"{number}: {a[4]!r} overlaps {b[4]!r}")

    def test_drawing_pdfs_are_well_formed(self):
        folder = ROOT / "hardware" / "drawings"
        for name, pages in [(f"{number}.pdf", 1) for number in DRAWINGS] + [("SLM-PAS-DRAWING-SET.pdf", 4)]:
            data = (folder / name).read_bytes()
            self.assertTrue(data.startswith(b"%PDF-1.4"), name)
            self.assertTrue(data.rstrip().endswith(b"%%EOF"), name)
            self.assertEqual(len(re.findall(rb"/Type /Page /Parent", data)), pages, name)
            self.assertIn(b"/MediaBox [0 0 1224.00 792.00]", data, name)        # 17 x 11 in
            self.assertIn(b"/Author (S&L MILESTONE SOLUTIONS)", data, name)
            xref = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
            self.assertTrue(data[xref:].startswith(b"xref\n"), name)
            count = int(re.match(rb"xref\n0 (\d+)\n", data[xref:]).group(1))
            entries = re.findall(rb"(\d{10}) 00000 n \n", data[xref:])
            self.assertEqual(len(entries), count - 1, name)
            for number, offset in enumerate(entries, start=1):                 # every cross-reference entry points at its object
                self.assertTrue(data[int(offset):].startswith(b"%d 0 obj\n" % number), f"{name} object {number}")
            for stream in re.finditer(rb"<< /Length (\d+) >>\nstream\n", data):
                self.assertEqual(data[stream.end() + int(stream.group(1)):][:10], b"\nendstream", name)

    def test_carrier_plate_features_stay_clear_of_each_other(self):
        cad = load_cad()
        plate = cad.PLATE
        centre = plate["side"] / 2.0
        strap_half = cad.ASSEMBLY["strap_width"] / 2.0
        radius = plate["tie_hole_d"] / 2.0
        for x, y in plate["tie_holes"]:                                        # the fault the first sketches had
            self.assertGreater(abs(y - centre) - radius, strap_half + 2.0, "a cable-tie hole sits under the strap")
            self.assertGreater(min(x, y, plate["side"] - x, plate["side"] - y) - radius, 3.0, "a hole is too close to the edge")
            self.assertGreater(math.hypot(x - centre, y - centre) - radius, plate["case_d"] / 2.0, "a hole sits under the case")
        slot_w, slot_l = plate["strap_slot"]
        self.assertGreaterEqual(slot_l, cad.ASSEMBLY["strap_width"] + 4.0)       # the strap passes through with room
        self.assertGreater(plate["strap_slot_x"] - slot_w / 2.0, plate["case_d"] / 2.0 + 2.0)   # slots clear the case
        self.assertGreater(plate["strap_slot_x"] - slot_w / 2.0, cad.ASSEMBLY["adapter"][0] / 2.0 + 2.0)   # and the adapter plate below
        self.assertLess(cad.FOAM["pad"][0], 2 * plate["strap_slot_x"] - slot_w)  # the pad fits between the slots
        self.assertLess(plate["mic_square"] * math.sqrt(2) / 2.0, plate["case_d"] / 2.0)   # microphones lie inside the case

    def test_demo_page_is_self_contained_and_labelled_simulated(self):
        page = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")
        self.assertIn("SIMULATED DATA - NOT REAL AUDIO", page)
        self.assertIn("S&amp;L MILESTONE SOLUTIONS", page)
        self.assertIn("heuristic score, not a probability", page)
        self.assertIn("direction only, no range", page)
        self.assertIn("It is not a photograph", page)
        for needle in ("http://", "https://", "src=", "@import", "url(", "getUserMedia", "fetch(", "XMLHttpRequest", "WebSocket", "<link"):
            self.assertNotIn(needle, page, f"the demo must load nothing and open no device: {needle}")
        lowered = page.lower()
        for claim in ("probability of", "confidence", "detection range", "range ring", "% accurate", "live audio"):
            self.assertNotIn(claim, lowered, claim)
        cad = load_cad()
        for constant in ("PLATE = 160", "PAD = 12.7", "CASE_R = 51", "CASE_H = 16", "CAP = 140", "CAP_T = 25.4", "POCKET = 12"):
            self.assertIn(constant, page)                                       # the mock-up uses the drawing dimensions
        self.assertEqual((cad.PLATE["side"], cad.FOAM["pad"][2], cad.PLATE["case_d"] / 2, cad.ASSEMBLY["case_height_ref"],
                          cad.FOAM["cap"][0], cad.FOAM["cap"][2], cad.FOAM["pocket_depth"]), (160.0, 12.7, 51.0, 16.0, 140.0, 25.4, 12.0))
        for x, y in cad.PLATE["tie_holes"]:
            self.assertIn(f"[{x:g}, {y:g}]", page)
        # an inline column layout overrides the narrow-screen rule, so the 3D tab would not stack on a phone
        self.assertNotIn('style="grid-template-columns', page)
        self.assertIn("@media (max-width:1100px) { .wide3d { grid-template-columns:1fr; } }", page)

    def test_templates_and_examples_parse(self):
        for path in list((ROOT / "records").glob("*.json")) + list((ROOT / "config").glob("*.json")) + list((ROOT / "examples").glob("*.json")):
            json.loads(path.read_text(encoding="utf-8"))
        with open(ROOT / "records" / "dataset_manifest_template.csv", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle))[0]["label"], "background")

    def test_no_firmware_or_model_binaries_are_bundled(self):
        for path in ROOT.rglob("*"):
            if path.is_file() and not (set(path.relative_to(ROOT).parts) & {".venv", "dist"}):
                self.assertNotIn(path.suffix.lower(), (".bin", ".dfu", ".pkl", ".pickle", ".onnx", ".pt", ".exe", ".dll"), str(path))
        self.assertEqual(list(ROOT.rglob("model.json")), [])

    def test_checksums_match_when_present(self):
        sums = ROOT / "SHA256SUMS.txt"
        if not sums.exists():
            self.skipTest("SHA256SUMS.txt is written by tools/make_release.py")
        for line in sums.read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)


if __name__ == "__main__":
    unittest.main()
