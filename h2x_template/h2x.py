"""Read an H2X export: one sheet per page, each with a drawing frame and H2X's own title column."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .pdfutil import render


class H2XError(ValueError):
    pass


@dataclass
class Sheet:
    index: int
    width: float
    height: float
    frame: tuple                 # drawing frame, page points y-up (x0, y0, x1, y1)
    title_column: tuple | None   # H2X's title column, if found
    content: tuple               # tight box around everything drawn inside the frame
    level: str = ""              # e.g. 'GROUND', 'LEVEL 1'
    subtitle: str = ""           # e.g. 'MECHANICAL SERVICES'
    project: str = ""
    scale_denominator: int | None = None   # 100 for '1:100 @ A1'
    paper: str = ""
    info: dict = field(default_factory=dict)

    def crop(self, mode: str) -> tuple:
        if mode == "page":
            return (0.0, 0.0, self.width, self.height)
        if mode == "frame":
            return self.frame
        return self.content

    @property
    def default_title(self) -> str:
        lvl = _title_case(self.level) if self.level else f"Sheet {self.index + 1}"
        return f"{lvl} - Heat Loss Layout"


def _title_case(s: str) -> str:
    words = []
    for w in s.split():
        words.append(w if any(ch.isdigit() for ch in w) and len(w) <= 3 else w.capitalize())
    return " ".join(words)


def _lines(words):
    """Group words into text lines (top-down order)."""
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"], 0), w["x0"])):
        if rows and abs(rows[-1]["top"] - w["top"]) < 0.4 * w["size"]:
            rows[-1]["words"].append(w)
        else:
            rows.append({"top": w["top"], "size": w["size"], "words": [w]})
    out = []
    for r in rows:
        ws = sorted(r["words"], key=lambda w: w["x0"])
        out.append({"top": r["top"], "size": max(w["size"] for w in ws), "text": " ".join(w["text"] for w in ws),
                    "x0": ws[0]["x0"]})
    return out


def _content_box(path, index, frame, page_h, dpi=50) -> tuple:
    """Tight box around what is drawn inside the frame (rendered, so it catches everything)."""
    x0, y0, x1, y1 = frame
    inset = 3.0
    img = render(Path(path), index, dpi)
    s = dpi / 72.0
    c0, c1 = int((x0 + inset) * s), int((x1 - inset) * s)
    r0, r1 = int((page_h - y1 + inset) * s), int((page_h - y0 - inset) * s)
    sub = img[r0:r1, c0:c1]
    ink = (sub.min(axis=2) < 245) if sub.ndim == 3 else (sub < 245)
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return frame
    pad = 6.0
    bx0 = (c0 + xs.min()) / s - pad
    bx1 = (c0 + xs.max() + 1) / s + pad
    by1 = page_h - ((r0 + ys.min()) / s - pad)
    by0 = page_h - ((r0 + ys.max() + 1) / s + pad)
    return (max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1))


def analyse(path) -> list[Sheet]:
    import pdfplumber
    import pikepdf
    path = str(path)
    with pikepdf.open(path) as pdf:
        if len(pdf.pages) == 0:
            raise H2XError("The H2X PDF has no pages.")
        for i, p in enumerate(pdf.pages):
            if int(p.obj.get("/Rotate", 0)) % 360:
                raise H2XError(f"Page {i + 1} of the H2X PDF is rotated; rotated pages are not supported yet.")
            mb = [float(v) for v in p.MediaBox]
            if abs(mb[0]) > 0.01 or abs(mb[1]) > 0.01:
                raise H2XError(f"Page {i + 1} of the H2X PDF has an unusual page box; not supported.")
    sheets = []
    with pdfplumber.open(path) as doc:
        for i, p in enumerate(doc.pages):
            W, H = float(p.width), float(p.height)
            rects = [r for r in p.rects]
            big = [r for r in rects if (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]) > 0.4 * W * H]
            frame = (0.0, 0.0, W, H)
            title = None
            if big:
                fr = max(big, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))
                frame = (fr["x0"], H - fr["bottom"], fr["x1"], H - fr["top"])
                col = [r for r in rects if abs(r["x0"] - fr["x1"]) < 2 and abs(r["top"] - fr["top"]) < 2
                       and abs(r["bottom"] - fr["bottom"]) < 2 and r["x1"] - r["x0"] > 40]
                if col:
                    c = max(col, key=lambda r: r["x1"] - r["x0"])
                    title = (c["x0"], H - c["bottom"], c["x1"], H - c["top"])
            sheet = Sheet(i, W, H, frame, title, frame)
            if title:
                words = [w for w in p.extract_words(extra_attrs=["size"])
                         if w["x0"] >= title[0] - 1 and w["x1"] <= title[2] + 1]
                lines = _lines(words)
                _read_title(sheet, lines)
            sheet.content = _content_box(path, i, frame, H)
            sheets.append(sheet)
    return sheets


def _read_title(sheet: Sheet, lines):
    texts = [ln["text"] for ln in lines]
    joined = "\n".join(texts)
    m = re.search(r"1\s*:\s*(\d+)(?:\s*@\s*(A\d))?", joined)
    if m:
        sheet.scale_denominator = int(m.group(1))
        sheet.paper = m.group(2) or ""
    # the level name is the largest text in the column
    cand = [ln for ln in lines if re.search(r"[A-Za-z0-9]", ln["text"]) and not ln["text"].endswith(":")]
    if cand:
        big = max(cand, key=lambda ln: ln["size"])
        sheet.level = big["text"].strip()
        k = lines.index(big)
        sub = []
        for ln in lines[k + 1:]:
            if ":" in ln["text"] or ln["size"] < 0.6 * big["size"]:
                break
            sub.append(ln["text"])
        sheet.subtitle = " ".join(sub)
    for k, ln in enumerate(lines):
        if ln["text"].strip().upper() == "PROJECT:":
            proj = []
            for nxt in lines[k + 1:]:
                if nxt["text"].strip() == sheet.level or ":" in nxt["text"]:
                    break
                proj.append(nxt["text"])
            sheet.project = " ".join(proj).strip()
            break
    sheet.info["title_text"] = texts
