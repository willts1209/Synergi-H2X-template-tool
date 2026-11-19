"""Checks run on every generated sheet before it is saved under its real name."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pikepdf

from .pdfutil import content_list, render

DPI = 100


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    warning: bool = False


@dataclass
class Report:
    checks: list = field(default_factory=list)

    def add(self, name, passed, detail="", warning=False):
        self.checks.append(Check(name, bool(passed), detail, warning))

    @property
    def passed(self):
        return not self.failures()

    def failures(self):
        return [c for c in self.checks if not c.passed and not c.warning]

    def warnings(self):
        return [c for c in self.checks if not c.passed and c.warning]

    def summary(self) -> str:
        return "\n".join(("PASS  " if c.passed else "NOTE  " if c.warning else "FAIL  ") + c.name +
                         (f" - {c.detail}" if c.detail else "") for c in self.checks)


class ValidationFailed(Exception):
    def __init__(self, report: Report):
        self.report = report
        fails = "\n".join(f"• {c.name}: {c.detail}" for c in report.failures())
        super().__init__("The sheet failed its checks and was NOT saved:\n" + fails)


# ---------------------------------------------------------------------------------- helpers
def same_object(a, b, seen=None, depth=0) -> bool:
    """Deep equality of two PDF objects (streams by decoded data), across documents."""
    if depth > 60:
        return True
    seen = seen if seen is not None else set()
    if isinstance(a, pikepdf.Object) and a.is_indirect and isinstance(b, pikepdf.Object) and b.is_indirect:
        key = (a.objgen, b.objgen)
        if key in seen:
            return True
        seen.add(key)
    if isinstance(a, pikepdf.Stream) != isinstance(b, pikepdf.Stream):
        return False
    if isinstance(a, pikepdf.Stream):
        try:
            if a.read_bytes() != b.read_bytes():
                return False
        except pikepdf.PdfError:
            if a.read_raw_bytes() != b.read_raw_bytes():
                return False
        ka = set(a.keys()) - {"/Length", "/Filter", "/DecodeParms"}
        kb = set(b.keys()) - {"/Length", "/Filter", "/DecodeParms"}
        if ka != kb:
            return False
        return all(same_object(a[k], b[k], seen, depth + 1) for k in ka)
    if isinstance(a, pikepdf.Dictionary) or isinstance(b, pikepdf.Dictionary):
        if not (isinstance(a, pikepdf.Dictionary) and isinstance(b, pikepdf.Dictionary)):
            return False
        if set(a.keys()) != set(b.keys()):
            return False
        return all(same_object(a[k], b[k], seen, depth + 1) for k in a.keys())
    if isinstance(a, pikepdf.Array) or isinstance(b, pikepdf.Array):
        if not (isinstance(a, pikepdf.Array) and isinstance(b, pikepdf.Array)) or len(a) != len(b):
            return False
        return all(same_object(x, y, seen, depth + 1) for x, y in zip(a, b))
    try:
        return a == b
    except Exception:  # noqa: BLE001
        return str(a) == str(b)


def _chars(path, index=0):
    import pdfplumber
    with pdfplumber.open(str(path)) as doc:
        p = doc.pages[index]
        H = float(p.height)
        return [(c["text"], float(c["x0"]), float(c["y0"]),
                 float(c["size"]), c.get("fontname", "")) for c in p.chars], H


def _inside(x, y, box, tol=0.5):
    return box[0] - tol <= x <= box[2] + tol and box[1] - tol <= y <= box[3] + tol


def _mask(shape, H, boxes, dpi, pad):
    m = np.zeros(shape[:2], bool)
    s = dpi / 72.0
    for x0, y0, x1, y1 in boxes:
        c0 = max(0, int((x0 - pad) * s))
        c1 = min(shape[1], int(np.ceil((x1 + pad) * s)) + 1)
        r0 = max(0, int((H - y1 - pad) * s))
        r1 = min(shape[0], int(np.ceil((H - y0 + pad) * s)) + 1)
        m[r0:r1, c0:c1] = True
    return m


def text_in_box(chars, box, bold=False):
    """Text of the regular-weight characters inside a box, in reading order."""
    sel = [c for c in chars if _inside(c[1] + 0.3, c[2] + 0.3, box, 0.2) and (("Bold" in c[4]) == bold)]
    sel.sort(key=lambda c: (-round(c[2], 0), c[1]))
    lines, cur, last_y = [], [], None
    for c in sel:
        if last_y is not None and abs(c[2] - last_y) > 1.0:
            lines.append("".join(x[0] for x in cur))
            cur = []
        cur.append(c)
        last_y = c[2]
    if cur:
        lines.append("".join(x[0] for x in cur))
    return " ".join(" ".join(ln.split()) for ln in lines)


def _isolated_drawing(h2x_path, index, placement, size) -> Path:
    """A blank page of the sheet size with only the H2X page drawn, made independently of the
    generator (pikepdf's own page-to-form conversion, clipped with a path instead of a BBox)."""
    import os
    import tempfile
    src = pikepdf.open(h2x_path)
    try:
        iso = pikepdf.new()
        form = iso.copy_foreign(src.pages[index].as_form_xobject())
        page = iso.add_blank_page(page_size=size)
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(F=form))
        m = " ".join(f"{v:.6f}" for v in placement.matrix)
        x0, y0, x1, y1 = placement.crop
        page.Contents = iso.make_stream(
            f"q {m} cm {x0:.4f} {y0:.4f} {x1 - x0:.4f} {y1 - y0:.4f} re W n /F Do Q".encode())
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        iso.save(path)
    finally:
        src.close()
    return Path(path)


# ---------------------------------------------------------------------------------- main
def validate(job, info, sheet, placement, editor, out_path: Path) -> Report:
    rep = Report()
    out_path = Path(out_path)
    out = pikepdf.open(out_path)
    src = pikepdf.open(job.h2x)
    tplpdf = pikepdf.open(job.template)
    try:
        page = out.pages[0]
        rep.add("Single page", len(out.pages) == 1, f"{len(out.pages)} pages")
        mb = [float(v) for v in page.MediaBox]
        tmb = [float(v) for v in tplpdf.pages[0].MediaBox]
        rep.add("Sheet size unchanged", mb == tmb, f"{mb} vs template {tmb}")

        # 1. the drawing is the H2X page, unmodified
        xo = page.Resources.get("/XObject", {})
        forms = [k for k in xo.keys() if str(k).startswith("/H2XDrawing")]
        if len(forms) != 1:
            rep.add("H2X drawing present", False, f"found {len(forms)} drawings")
            return rep
        form = xo[forms[0]]
        sp = src.pages[sheet.index]
        src_bytes = b"\n".join(s.read_bytes() for s in content_list(sp))
        rep.add("H2X drawing content identical (byte for byte)", form.read_bytes() == src_bytes,
                f"{len(src_bytes):,} bytes")
        rep.add("H2X drawing resources identical (fonts, images, styles)",
                same_object(form.get("/Resources", pikepdf.Dictionary()), sp.obj.get("/Resources", pikepdf.Dictionary())))

        # 2. placement: uniform scale, inside the drawing area, clear of the title block
        m = placement.matrix
        uniform = abs(m[0] - m[3]) < 1e-9 and abs(m[1]) < 1e-12 and abs(m[2]) < 1e-12 and m[0] > 0
        rep.add("Drawing scaled uniformly (no stretching or rotation)", uniform, f"scale {m[0]:.4f}")
        bb = placement.bbox
        area = info.drawing_area
        inside = bb[0] >= area[0] - 1e-6 and bb[1] >= area[1] - 1e-6 and bb[2] <= area[2] + 1e-6 and bb[3] <= area[3] + 1e-6
        rep.add("Drawing inside the drawing area (clear of border and title block)", inside,
                f"drawing {tuple(round(v, 1) for v in bb)}, area {tuple(round(v, 1) for v in area)}")
        bbox = [float(v) for v in form.BBox]
        rep.add("Drawing crop as planned", all(abs(a - b) < 1e-3 for a, b in zip(bbox, placement.crop)))
    finally:
        out.close()
        src.close()
        tplpdf.close()

    # 3. every character of the H2X drawing (heat loss figures, room data) is present, unchanged, in place
    src_chars, _ = _chars(job.h2x, sheet.index)
    out_chars, _ = _chars(out_path, 0)
    s = placement.scale
    tx, ty = placement.matrix[4], placement.matrix[5]
    crop = placement.crop
    exp = [c for c in src_chars if _inside(c[1], c[2], crop, 0.0)]
    got = [c for c in out_chars if _inside(c[1], c[2], bb, 0.3)]
    ce, cg = Counter(c[0] for c in exp), Counter(c[0] for c in got)
    rep.add("All drawing text present and identical", ce == cg and len(exp) > 0 or (len(exp) == 0 and len(got) == 0),
            f"{sum(ce.values()):,} characters" if ce == cg else
            f"missing {sum((ce - cg).values())}, unexpected {sum((cg - ce).values())}")
    # positions: each expected char maps to an output char of the same text at the scaled spot
    grid = {}
    for c in got:
        grid.setdefault((c[0], round(c[1]), round(c[2])), []).append(c)
    bad = 0
    for c in exp:
        x, y = c[1] * s + tx, c[2] * s + ty
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for g in grid.get((c[0], round(x) + dx, round(y) + dy), []):
                    if abs(g[1] - x) < 0.05 and abs(g[2] - y) < 0.05 and abs(g[3] - c[3] * s) < 0.01:
                        hit = True
        bad += not hit
    rep.add("Drawing text at exactly the scaled positions and sizes", bad == 0,
            f"{bad} characters out of place" if bad else "")

    # 4. the template is unchanged apart from the filled-in values and the drawing
    t_img = render(Path(job.template), 0, DPI)
    o_img = render(out_path, 0, DPI)
    if t_img.shape != o_img.shape:
        rep.add("Template unchanged outside the edits", False, "render sizes differ")
    else:
        mask = _mask(t_img.shape, info.height, [bb] + list(editor.edit_boxes), DPI, 1.0)
        diff = (t_img != o_img).any(axis=2)
        n = int((diff & ~mask).sum())
        rep.add("Template unchanged outside the edited values (lines, labels, logo)", n == 0,
                f"{n} pixels changed" if n else "0 pixels changed")

    # 5. values read back from the output
    for key, want in editor.expected.items():
        if key.startswith("revrow"):
            r, k = key[6:].split(".")
            r = int(r)
            t = info.rev
            c = ["rev", "date", "description", "drn", "chk"].index(k)
            col = t.columns[c]
            y0, y1 = t.rows[r]
            box = (col.x0, y0, col.x1, y1)
            label = f"Revision row {r + 1} {col.name}"
        elif key == "notes":
            lab, cell = info.notes
            box = (cell.x0, cell.y0, cell.x1, lab.y - 1)
            label = "Notes"
        else:
            f = info.fields[key]
            box = (f.cell.x0, f.cell.y0, f.cell.x1, f.label.y - 1)
            label = key.replace("_", " ").capitalize()
        got_t = text_in_box(out_chars, box)
        norm = " ".join(want.split())
        rep.add(f"{label} reads back correctly", got_t == norm, f"'{got_t}'" + ("" if got_t == norm else f" expected '{norm}'"))

    # 6. nothing covers the drawing: the sheet looks exactly like the H2X page drawn on its own
    try:
        iso = _isolated_drawing(job.h2x, sheet.index, placement, (info.width, info.height))
        a = render(iso, 0, DPI)
        b = o_img
        m = _mask(a.shape, info.height, [bb], DPI, -1.0)   # inside the drawing, 1 pt clear of its edge
        n = int(((a != b).any(axis=2) & m).sum())
        rep.add("Nothing in the template covers or alters the drawing", n == 0,
                f"{n} pixels differ from the H2X sheet rendered on its own" if n else "0 pixels differ")
        Path(iso).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        rep.add("Nothing in the template covers or alters the drawing", False, f"could not compare ({exc})")
    return rep
