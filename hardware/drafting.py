"""Small drafting engine: one display list, written as SVG and as vector PDF.

Sheet coordinates are millimetres with the origin at the top left and Y running
down. Line weights and dash patterns follow common ASME Y14.2 practice. The
PDF writer uses only the standard Helvetica fonts, embeds nothing, and writes
no dates, so the same input always gives the same bytes.

Brand values are the S&L Milestone palette: Obsidian, Marine, Light Marine,
and the light background tint. Linework stays near-black so sheets print well.
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

INK = "#141416"
OBSIDIAN = "#1C1C1E"
MARINE = "#1E5470"
LIGHT_MARINE = "#5A9AB5"
LIGHT_BG = "#F0F4F6"
GRAY = "#5A5A5C"
WHITE = "#FFFFFF"
COMPANY = "S&L MILESTONE SOLUTIONS"
FONT_STACK = "Aptos, 'Segoe UI', Arial, Helvetica, sans-serif"

# name: (width mm, dash pattern mm or None)
LINES = {
    "border": (0.70, None), "visible": (0.50, None), "thin": (0.25, None), "frame": (0.35, None),
    "hidden": (0.25, (2.5, 1.2)), "center": (0.25, (8.0, 1.5, 1.5, 1.5)),
    "phantom": (0.25, (10.0, 1.5, 1.5, 1.5, 1.5, 1.5)), "cut": (0.70, (12.0, 2.0, 2.0, 2.0, 2.0, 2.0)),
    "hatch": (0.18, None), "strap": (0.90, None),
}
PT = 72.0 / 25.4

_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
         556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
         722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
         556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
         260, 334, 584]
_HELV_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
              556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722,
              611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556,
              611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778,
              556, 556, 500, 389, 280, 389, 584]


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Width in millimetres of text set in Helvetica at a cap-height-style size in millimetres."""
    table = _HELV_BOLD if bold else _HELV
    units = sum(table[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 600 for ch in text)
    return units / 1000.0 * size * 1.4          # font size is 1.4 x the nominal text height


def _rgb(colour: str) -> tuple:
    return tuple(int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


class Sheet:
    """One drawing sheet. Add primitives, then write SVG or include in a PDF."""

    def __init__(self, width: float = 431.8, height: float = 279.4):
        self.width, self.height = width, height
        self.items: list = []

    # ------------------------------------------------------------ primitives
    def line(self, x1, y1, x2, y2, kind="visible", colour=INK):
        self.items.append(("poly", [(x1, y1), (x2, y2)], kind, False, None, colour))

    def polyline(self, points, kind="visible", closed=False, fill=None, colour=INK):
        self.items.append(("poly", [tuple(p) for p in points], kind, closed, fill, colour))

    def rect(self, x, y, w, h, kind="visible", fill=None, colour=INK):
        if LINES[kind][1] and not fill:
            # dashed outlines are drawn edge by edge so the dash pattern restarts on every side
            for x1, y1, x2, y2 in ((x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h), (x, y + h, x, y)):
                self.line(x1, y1, x2, y2, kind, colour)
            return
        self.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], kind, True, fill, colour)

    def circle(self, cx, cy, r, kind="visible", fill=None, colour=INK):
        self.items.append(("circle", cx, cy, r, kind, fill, colour))

    def arc(self, cx, cy, r, start_deg, end_deg, kind="visible", colour=INK):
        """Arc from start to end, counterclockwise as seen on the sheet."""
        self.items.append(("arc", cx, cy, r, start_deg, end_deg, kind, colour))

    def text(self, x, y, text, size=2.5, bold=False, anchor="start", rotate=0.0, colour=INK):
        """Text with its baseline anchor at (x, y). size is the nominal letter height in mm."""
        self.items.append(("text", x, y, str(text), size, bold, anchor, rotate, colour))

    def arrow(self, x, y, direction_deg, length=3.0, width=1.0, colour=INK):
        """Filled arrowhead with its tip at (x, y), pointing along direction_deg (counterclockwise from +X as seen)."""
        a = math.radians(direction_deg)
        dx, dy = math.cos(a), -math.sin(a)
        bx, by = x - length * dx, y - length * dy
        self.polyline([(x, y), (bx - width / 2 * dy, by + width / 2 * dx), (bx + width / 2 * dy, by - width / 2 * dx)],
                      "thin", True, colour, colour)

    # -------------------------------------------------------------- drafting
    def dim_h(self, x1, x2, y_feature, y_dim, label, size=2.5, y_feature2=None):
        """Horizontal dimension between x1 and x2, drawn at y_dim."""
        x1, x2 = sorted((x1, x2))
        sign = 1.0 if y_dim > y_feature else -1.0
        for x, y0 in ((x1, y_feature), (x2, y_feature if y_feature2 is None else y_feature2)):
            self.line(x, y0 + sign * 1.5, x, y_dim + sign * 2.0, "thin")
        width, middle = text_width(label, size), (x1 + x2) / 2.0
        if x2 - x1 >= width + 9.0:
            self.line(x1, y_dim, middle - width / 2 - 1.0, y_dim, "thin")
            self.line(middle + width / 2 + 1.0, y_dim, x2, y_dim, "thin")
            self.arrow(x1, y_dim, 180)
            self.arrow(x2, y_dim, 0)
            self.text(middle, y_dim + size * 0.36, label, size, anchor="middle")
        else:
            self.line(x1 - 6.0, y_dim, x1, y_dim, "thin")
            self.line(x2, y_dim, x2 + 7.0 + width, y_dim, "thin")
            self.arrow(x1, y_dim, 0)
            self.arrow(x2, y_dim, 180)
            self.text(x2 + 5.5, y_dim - 1.0, label, size)

    def dim_v(self, y1, y2, x_feature, x_dim, label, size=2.5, x_feature2=None):
        """Vertical dimension between y1 and y2, drawn at x_dim. Text reads horizontally."""
        y1, y2 = sorted((y1, y2))
        sign = 1.0 if x_dim > x_feature else -1.0
        for y, x0 in ((y1, x_feature), (y2, x_feature if x_feature2 is None else x_feature2)):
            self.line(x0 + sign * 1.5, y, x_dim + sign * 2.0, y, "thin")
        middle = (y1 + y2) / 2.0
        if y2 - y1 >= 9.0 and text_width(label, size) > 9.0:
            # a long label sits beside an unbroken dimension line, on the side away from the part
            self.line(x_dim, y1, x_dim, y2, "thin")
            self.arrow(x_dim, y1, 90)
            self.arrow(x_dim, y2, 270)
            self.text(x_dim + sign * 2.0, middle + size * 0.36, label, size, anchor="start" if sign > 0 else "end")
        elif y2 - y1 >= size + 9.0:
            self.line(x_dim, y1, x_dim, middle - size / 2 - 1.0, "thin")
            self.line(x_dim, middle + size / 2 + 1.0, x_dim, y2, "thin")
            self.arrow(x_dim, y1, 90)
            self.arrow(x_dim, y2, 270)
            self.text(x_dim, middle + size * 0.36, label, size, anchor="middle")
        else:
            self.line(x_dim, y1 - 6.0, x_dim, y1, "thin")
            self.line(x_dim, y2, x_dim, y2 + 6.0, "thin")
            self.arrow(x_dim, y1, 270)
            self.arrow(x_dim, y2, 90)
            self.text(x_dim + sign * 1.5, y2 + 6.0 + size, label, size, anchor="start" if sign > 0 else "end")

    def ordinate_x(self, x, y_from, y_to, label, size=2.5):
        """Ordinate dimension along X: a witness line down (or up) from the part with the value at its end."""
        sign = 1.0 if y_to > y_from else -1.0
        self.line(x, y_from + sign * 1.5, x, y_to, "thin")
        self.text(x + size * 0.36, y_to + sign * 1.2, label, size, anchor="end" if sign > 0 else "start", rotate=90)

    def ordinate_y(self, y, x_from, x_to, label, size=2.5):
        """Ordinate dimension along Y: a witness line sideways from the part with the value at its end."""
        sign = 1.0 if x_to > x_from else -1.0
        self.line(x_from + sign * 1.5, y, x_to, y, "thin")
        self.text(x_to + sign * 1.2, y + size * 0.36, label, size, anchor="start" if sign > 0 else "end")

    def leader(self, points, lines, size=2.5, side="right", line_gap=1.5):
        """Leader from a text shoulder (first point) to a feature (last point, with arrowhead)."""
        self.polyline(points, "thin")
        (ax, ay), (bx, by) = points[-2], points[-1]
        self.arrow(bx, by, math.degrees(math.atan2(-(by - ay), bx - ax)))
        x0, y0 = points[0]
        for i, text in enumerate(lines):
            y = y0 + size * 0.36 + (i - (len(lines) - 1) / 2.0) * (size + line_gap)
            self.text(x0 + (1.2 if side == "right" else -1.2), y, text, size, anchor="start" if side == "right" else "end")

    def balloon(self, cx, cy, label, target, radius=4.2, dot=False):
        """Item balloon with a leader to the part."""
        tx, ty = target
        distance = math.hypot(tx - cx, ty - cy)
        ux, uy = (tx - cx) / distance, (ty - cy) / distance
        self.line(cx + radius * ux, cy + radius * uy, tx, ty, "thin")
        if dot:
            self.circle(tx, ty, 0.6, "thin", INK)
        else:
            self.arrow(tx, ty, math.degrees(math.atan2(-uy, ux)))
        self.circle(cx, cy, radius, "thin", WHITE)
        self.text(cx, cy + 1.15, label, 3.2, bold=True, anchor="middle")

    def center_mark(self, cx, cy, half=3.5):
        self.line(cx - half, cy, cx + half, cy, "center")
        self.line(cx, cy - half, cx, cy + half, "center")

    def hatch_rect(self, x, y, w, h, spacing=2.2):
        """45 degree section lining clipped to a rectangle. Phase is global so adjacent areas line up."""
        k = math.ceil((x + y) / spacing) * spacing
        while k < x + w + y + h:
            xa, xb = max(x, k - (y + h)), min(x + w, k - y)
            if xb - xa > 0.05:
                self.line(xa, k - xa, xb, k - xb, "hatch")
            k += spacing

    def table(self, x, y, widths, rows, row_height=5.5, size=2.3, header=True, align=None):
        """Ruled table. The first row is a brand header band when header is true."""
        total = sum(widths)
        for r, row in enumerate(rows):
            top = y + r * row_height
            if header and r == 0:
                self.rect(x, top, total, row_height, "thin", OBSIDIAN)
            elif r % 2 == 0:
                self.rect(x, top, total, row_height, "thin", LIGHT_BG)
            left = x
            for c, (cell, width) in enumerate(zip(row, widths)):
                mode = (align[c] if align else "start")
                tx = left + 1.5 if mode == "start" else left + width / 2.0
                self.text(tx, top + row_height / 2 + size * 0.36, cell, size, bold=header and r == 0, anchor=mode,
                          colour=WHITE if header and r == 0 else INK)
                left += width
        self.rect(x, y, total, row_height * len(rows), "frame")
        for r in range(1, len(rows)):
            self.line(x, y + r * row_height, x + total, y + r * row_height, "thin")
        left = x
        for width in widths[:-1]:
            left += width
            self.line(left, y, left, y + row_height * len(rows), "thin")

    def view_title(self, x, y, title, scale=None):
        self.text(x, y, title, 3.2, bold=True, anchor="middle")
        width = text_width(title, 3.2, True)
        self.line(x - width / 2, y + 1.2, x + width / 2, y + 1.2, "thin")
        if scale:
            self.text(x, y + 5.2, scale, 2.3, anchor="middle")

    def notes(self, x, y, lines, size=2.4, gap=1.55, heading="NOTES:"):
        """Numbered general notes. Each entry may be a list of wrapped lines."""
        self.text(x, y, heading, 2.8, bold=True, colour=MARINE)
        y += size + gap + 1.0
        for number, entry in enumerate(lines, start=1):
            parts = entry if isinstance(entry, (list, tuple)) else [entry]
            self.text(x, y, f"{number}.", size)
            for part in parts:
                self.text(x + 6.0, y, part, size)
                y += size + gap
            y += 0.8
        return y

    # ------------------------------------------------------------------ frame
    def frame(self, info: dict, revisions: list) -> None:
        """Border, zone marks, revision block, and the company title block."""
        w, h = self.width, self.height
        left, right, top, bottom = 15.0, w - 15.0, 10.0, h - 10.0
        self.rect(left, top, right - left, bottom - top, "border")
        columns, rows = 4, 4
        for i in range(columns):
            x0 = left + (right - left) * i / columns
            if i:
                self.line(x0, top - 3.0, x0, top, "thin")
                self.line(x0, bottom, x0, bottom + 3.0, "thin")
            label = str(columns - i)
            cx = x0 + (right - left) / columns / 2
            self.text(cx, top - 3.0, label, 2.6, anchor="middle", colour=GRAY)
            self.text(cx, bottom + 5.8, label, 2.6, anchor="middle", colour=GRAY)
        for j in range(rows):
            y0 = top + (bottom - top) * j / rows
            if j:
                self.line(left - 3.0, y0, left, y0, "thin")
                self.line(right, y0, right + 3.0, y0, "thin")
            label = "DCBA"[j]
            cy = y0 + (bottom - top) / rows / 2 + 1.0
            self.text(left - 5.0, cy, label, 2.6, anchor="middle", colour=GRAY)
            self.text(right + 5.0, cy, label, 2.6, anchor="middle", colour=GRAY)

        # revision block, top right
        widths = [12.0, 92.0, 22.0, 24.0]
        rx = right - sum(widths)
        self.rect(rx, top, sum(widths), 5.0, "thin", OBSIDIAN)
        self.text(rx + sum(widths) / 2, top + 3.6, "REVISIONS", 2.4, bold=True, anchor="middle", colour=WHITE)
        self.rect(rx, top + 5.0, sum(widths), 5.0, "thin", LIGHT_BG)
        x0 = rx
        for title, width in zip(("REV", "DESCRIPTION", "DATE", "APPROVED"), widths):
            self.text(x0 + width / 2, top + 8.5, title, 2.0, bold=True, anchor="middle", colour=MARINE)
            x0 += width
        for r, row in enumerate(revisions):
            y0 = top + 10.0 + r * 5.5
            x0 = rx
            for c, (cell, width) in enumerate(zip(row, widths)):
                self.text(x0 + (1.5 if c == 1 else width / 2), y0 + 3.8, cell, 2.2, anchor="start" if c == 1 else "middle")
                x0 += width
        rev_height = 10.0 + 5.5 * len(revisions)
        self.rect(rx, top, sum(widths), rev_height, "frame")
        for y0 in [top + 5.0, top + 10.0] + [top + 10.0 + 5.5 * r for r in range(1, len(revisions))]:
            self.line(rx, y0, right, y0, "thin")
        x0 = rx
        for width in widths[:-1]:
            x0 += width
            self.line(x0, top + 5.0, x0, top + rev_height, "thin")

        # title block, bottom right: three columns
        bw, bh = 200.0, 50.0
        bx, by = right - bw, bottom - bh
        self.rect(bx, by, bw, bh, "border")
        c1, c2 = bx + 62.0, bx + 120.0
        self.line(c1, by, c1, bottom, "frame")
        self.line(c2, by, c2, bottom, "frame")

        def label(x, y, text):
            self.text(x, y, text, 1.7, bold=True, colour=MARINE)

        # column 1: tolerances and projection
        label(bx + 1.5, by + 3.4, "UNLESS OTHERWISE SPECIFIED")
        for i, text in enumerate(info["tolerances"]):
            self.text(bx + 1.5, by + 7.6 + i * 3.6, text, 2.1)
        self.line(bx, by + 33.0, c1, by + 33.0, "thin")
        px, py = bx + 6.0, by + 41.5                      # third-angle symbol: circles on the left, cone on the right
        self.circle(px + 3.0, py, 3.0, "thin")
        self.circle(px + 3.0, py, 1.5, "thin")
        self.line(px - 1.0, py, px + 21.0, py, "center")
        self.polyline([(px + 11.0, py - 1.5), (px + 19.0, py - 3.0), (px + 19.0, py + 3.0), (px + 11.0, py + 1.5)], "thin", True)
        self.text(bx + 27.0, by + 38.6, "THIRD ANGLE PROJECTION", 1.7)
        self.text(bx + 27.0, by + 45.4, "DO NOT SCALE DRAWING", 1.7, bold=True)

        # column 2: sign-off, material, finish, status
        cols = [c1, c1 + 16.0, c1 + 40.0, c2]
        for i in range(1, 5):
            self.line(c1, by + i * 6.0, c2, by + i * 6.0, "thin")
        self.line(cols[1], by, cols[1], by + 24.0, "thin")
        self.line(cols[2], by, cols[2], by + 24.0, "thin")
        self.rect(c1, by, c2 - c1, 6.0, "thin", LIGHT_BG)
        label(cols[1] + 1.5, by + 4.0, "NAME")
        label(cols[2] + 1.5, by + 4.0, "DATE")
        for i, (role, name, date) in enumerate(info["signoff"], start=1):
            label(c1 + 1.5, by + i * 6.0 + 4.0, role)
            self.text(cols[1] + 1.5, by + i * 6.0 + 4.2, name, 2.1)
            self.text(cols[2] + 1.5, by + i * 6.0 + 4.2, date, 2.1)
        label(c1 + 1.5, by + 27.2, "MATERIAL")
        for i, text in enumerate(info["material"]):
            self.text(c1 + 1.5, by + 30.6 + i * 3.1, text, 2.0)
        self.line(c1, by + 35.0, c2, by + 35.0, "thin")
        label(c1 + 1.5, by + 38.0, "FINISH")
        self.text(c1 + 1.5, by + 41.4, info["finish"], 2.0)
        self.line(c1, by + 43.0, c2, by + 43.0, "thin")
        label(c1 + 1.5, by + 46.0, "STATUS")
        self.text(c1 + 14.0, by + 47.6, info["status"], 2.3, bold=True)

        # column 3: company, title, numbers
        self.rect(c2, by, right - c2, 12.0, "thin", OBSIDIAN)
        self.rect(c2, by + 10.8, right - c2, 1.2, "thin", MARINE, MARINE)
        company_size = min(4.0, 4.0 * (right - c2 - 5.0) / text_width(COMPANY, 4.0, True))
        self.text((c2 + right) / 2, by + 7.6, COMPANY, company_size, bold=True, anchor="middle", colour=WHITE)
        label(c2 + 1.5, by + 15.4, "TITLE")
        room = right - c2 - 4.0

        def fitted(text, size, bold):
            """Largest size up to the nominal that keeps the text inside the title cell."""
            return min(size, size * room / max(text_width(text, size, bold), 1e-6))

        self.text((c2 + right) / 2, by + 21.0, info["title"], fitted(info["title"], 3.6, True), bold=True, anchor="middle")
        self.text((c2 + right) / 2, by + 25.6, info["subtitle"], fitted(info["subtitle"], 2.3, False), anchor="middle")
        self.line(c2, by + 28.0, right, by + 28.0, "frame")
        s1, s2 = c2 + 12.0, right - 16.0
        self.line(s1, by + 28.0, s1, by + 40.0, "thin")
        self.line(s2, by + 28.0, s2, by + 40.0, "thin")
        label(c2 + 1.5, by + 31.2, "SIZE")
        self.text(c2 + 6.0, by + 38.2, info["size"], 4.2, bold=True, anchor="middle")
        label(s1 + 1.5, by + 31.2, "DWG NO")
        self.text((s1 + s2) / 2, by + 38.0, info["number"], 4.0, bold=True, anchor="middle")
        label(s2 + 1.5, by + 31.2, "REV")
        self.text((s2 + right) / 2, by + 38.2, info["rev"], 4.2, bold=True, anchor="middle")
        self.line(c2, by + 40.0, right, by + 40.0, "frame")
        t1, t2 = c2 + 26.0, right - 26.0
        self.line(t1, by + 40.0, t1, bottom, "thin")
        self.line(t2, by + 40.0, t2, bottom, "thin")
        label(c2 + 1.5, by + 43.2, "SCALE")
        self.text(c2 + 13.0, by + 48.0, info["scale"], 2.8, bold=True, anchor="middle")
        label(t1 + 1.5, by + 43.2, "PROJECT")
        self.text((t1 + t2) / 2, by + 48.0, info["project"], 2.2, anchor="middle")
        label(t2 + 1.5, by + 43.2, "SHEET")
        self.text((t2 + right) / 2, by + 48.0, info["sheet"], 2.8, bold=True, anchor="middle")

    # ---------------------------------------------------------------- output
    def text_boxes(self) -> list:
        """Approximate bounding box (x0, y0, x1, y1, text) of every text item, for layout checks."""
        boxes = []
        for item in self.items:
            if item[0] != "text":
                continue
            _, x, y, text, size, bold, anchor, rotate, _colour = item
            width = text_width(text, size, bold)
            shift = {"start": 0.0, "middle": width / 2.0, "end": width}[anchor]
            if rotate == 0:
                boxes.append((x - shift, y - size, x - shift + width, y + 0.25 * size, text))
            elif rotate == 90:
                boxes.append((x - size, y + shift - width, x + 0.25 * size, y + shift, text))
        return boxes

    def to_svg(self) -> str:
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}mm" height="{self.height}mm" '
               f'viewBox="0 0 {self.width} {self.height}">',
               f'<rect width="{self.width}" height="{self.height}" fill="{WHITE}"/>',
               f'<g font-family="{FONT_STACK}" stroke-linecap="butt" stroke-linejoin="round">']

        def stroke(kind, colour):
            width, dash = LINES[kind]
            text = f'stroke="{colour}" stroke-width="{width}"'
            if dash:
                text += ' stroke-dasharray="' + " ".join(f"{d:g}" for d in dash) + '"'
            return text

        for item in self.items:
            if item[0] == "poly":
                _, points, kind, closed, fill, colour = item
                path = "M" + " L".join(f"{x:.3f} {y:.3f}" for x, y in points) + (" Z" if closed else "")
                out.append(f'<path d="{path}" fill="{fill or "none"}" {stroke(kind, colour)}/>')
            elif item[0] == "circle":
                _, cx, cy, r, kind, fill, colour = item
                out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="{fill or "none"}" {stroke(kind, colour)}/>')
            elif item[0] == "arc":
                _, cx, cy, r, a0, a1, kind, colour = item
                x0, y0 = cx + r * math.cos(math.radians(a0)), cy - r * math.sin(math.radians(a0))
                x1, y1 = cx + r * math.cos(math.radians(a1)), cy - r * math.sin(math.radians(a1))
                large = 1 if (a1 - a0) % 360.0 > 180.0 else 0
                out.append(f'<path d="M{x0:.3f} {y0:.3f} A{r:.3f} {r:.3f} 0 {large} 0 {x1:.3f} {y1:.3f}" fill="none" {stroke(kind, colour)}/>')
            else:
                _, x, y, text, size, bold, anchor, rotate, colour = item
                transform = f' transform="rotate({-rotate:g} {x:.3f} {y:.3f})"' if rotate else ""
                weight = ' font-weight="bold"' if bold else ""
                out.append(f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size * 1.4:.3f}" text-anchor="{anchor}" '
                           f'fill="{colour}"{weight}{transform}>{escape(text)}</text>')
        out.append("</g></svg>")
        return "\n".join(out) + "\n"

    def to_pdf_stream(self) -> bytes:
        h = self.height
        out = ["1 j 0 J"]

        def setup(kind, colour, fill):
            width, dash = LINES[kind]
            out.append("%.3f %.3f %.3f RG %.3f w" % (*_rgb(colour), width * PT))
            out.append("[" + " ".join(f"{d * PT:.3f}" for d in dash) + "] 0 d" if dash else "[] 0 d")
            if fill:
                out.append("%.3f %.3f %.3f rg" % _rgb(fill))

        def pt(x, y):
            return f"{x * PT:.3f} {(h - y) * PT:.3f}"

        for item in self.items:
            if item[0] == "poly":
                _, points, kind, closed, fill, colour = item
                setup(kind, colour, fill)
                out.append(pt(*points[0]) + " m " + " ".join(pt(x, y) + " l" for x, y in points[1:])
                           + ((" h B" if fill else " h S") if closed else " S"))
            elif item[0] in ("circle", "arc"):
                if item[0] == "circle":
                    _, cx, cy, r, kind, fill, colour = item
                    a0, a1 = 0.0, 360.0
                else:
                    _, cx, cy, r, a0, a1, kind, colour = item
                    fill = None
                    a1 = a0 + ((a1 - a0) % 360.0 or 360.0)
                setup(kind, colour, fill)
                pieces = max(int(math.ceil((a1 - a0) / 90.0)), 1)
                step = math.radians((a1 - a0) / pieces)
                k = 4.0 / 3.0 * math.tan(step / 4.0)
                angle = math.radians(a0)
                px, py = cx * PT, (h - cy) * PT
                rr = r * PT
                path = [f"{px + rr * math.cos(angle):.3f} {py + rr * math.sin(angle):.3f} m"]
                for _ in range(pieces):
                    nxt = angle + step
                    path.append("%.3f %.3f %.3f %.3f %.3f %.3f c" % (
                        px + rr * (math.cos(angle) - k * math.sin(angle)), py + rr * (math.sin(angle) + k * math.cos(angle)),
                        px + rr * (math.cos(nxt) + k * math.sin(nxt)), py + rr * (math.sin(nxt) - k * math.cos(nxt)),
                        px + rr * math.cos(nxt), py + rr * math.sin(nxt)))
                    angle = nxt
                closing = (" h B" if fill else " h S") if item[0] == "circle" else " S"
                out.append(" ".join(path) + closing)
            else:
                _, x, y, text, size, bold, anchor, rotate, colour = item
                width = text_width(text, size, bold)
                shift = {"start": 0.0, "middle": width / 2.0, "end": width}[anchor]
                a = math.radians(rotate)
                x0, y0 = x - shift * math.cos(a), y + shift * math.sin(a)
                encoded = text.encode("cp1252", "replace").decode("latin-1")
                encoded = encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                out.append("BT %.3f %.3f %.3f rg /%s %.3f Tf %.5f %.5f %.5f %.5f %s Tm (%s) Tj ET" % (
                    *_rgb(colour), "F2" if bold else "F1", size * 1.4 * PT, math.cos(a), math.sin(a), -math.sin(a),
                    math.cos(a), pt(x0, y0), encoded))
        return "\n".join(out).encode("latin-1")


def write_pdf(sheets: list, title: str) -> bytes:
    """A deterministic multi-page vector PDF with one page per sheet."""
    objects: list = [b"", b"",
                     b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                     b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for sheet in sheets:
        stream = sheet.to_pdf_stream()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                        "/Contents %d 0 R >>" % (sheet.width * PT, sheet.height * PT, len(objects))).encode())
        kids.append(len(objects))
    safe_title = title.replace("\\", "").replace("(", "").replace(")", "")
    objects.append(f"<< /Title ({safe_title}) /Author ({COMPANY}) /Producer (hardware/drafting.py) >>".encode("latin-1"))
    info_number = len(objects)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = ("<< /Type /Pages /Kids [" + " ".join(f"{k} 0 R" for k in kids) + f"] /Count {len(kids)} >>").encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_number} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)
