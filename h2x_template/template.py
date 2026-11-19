"""Read the Synergi AutoCAD drawing template and fill in its title block.

Everything is located from the template itself (labels, table lines), so a re-plotted template
with moved cells still works.  Only the value text in the title block is changed; the template's
lines, labels, logo and copyright text are never touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

from .fonts import FontError, FontWriter, hexcodes, parse_tounicode, parse_w
from .pdfutil import TextShow, content_list, walk_text


class TemplateError(ValueError):
    pass


# label in the template -> field key
FIELD_LABELS = {
    "Client:": "client",
    "Project Details:": "project",
    "Drawing Title:": "title",
    "Drawing Status:": "status",
    "Document Number:": "document_number",
    "Rev": "rev",
    "Scale:": "scale",
    "Size:": "size",
    "Drawn:": "drawn",
    "Date:": "date",
}
FIELD_NAMES = {
    "client": "Client", "project": "Project details", "title": "Drawing title", "status": "Drawing status",
    "document_number": "Document number", "rev": "Revision", "scale": "Scale", "size": "Size",
    "drawn": "Drawn", "date": "Date",
}
MULTILINE_FIELDS = {"client"}
REV_COLUMNS = ["Rev", "Date", "Description", "DRN", "CHK"]
REV_KEYS = ["rev", "date", "description", "drn", "chk"]
NOTES_LABEL = "Notes / Comments:"

ISO_SIZES = {"A0": (2384, 3370), "A1": (1684, 2384), "A2": (1191, 1684), "A3": (842, 1191), "A4": (595, 842)}


def paper_name(w, h) -> str:
    a, b = sorted((w, h))
    for name, (s, l) in ISO_SIZES.items():
        if abs(a - s) < 6 and abs(b - l) < 6:
            return name
    return ""


@dataclass
class Block:
    show: TextShow
    text: str
    x0: float
    x1: float
    y: float            # baseline, page points, y up
    size: float         # font size in page points
    bold: bool

    @property
    def centre(self):
        return (self.x0 + self.x1) / 2


@dataclass
class Cell:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Field:
    key: str
    label: Block
    value: Block
    cell: Cell

    @property
    def max_width(self):
        return self.cell.x1 - self.value.x0 - 1.5


@dataclass
class RevColumn:
    name: str
    x0: float
    x1: float
    align: str          # 'centre' or 'left'
    offset: float       # left inset for 'left'


@dataclass
class RevTable:
    columns: list
    rows: list                         # [(y0, y1)], first = the row next to the header
    baseline_offset: float             # baseline above the row's lower line
    existing: dict = field(default_factory=dict)   # (row, col) -> Block


@dataclass
class TemplateInfo:
    path: str
    width: float
    height: float
    size_name: str
    fields: dict
    rev: RevTable | None
    notes: tuple | None                # (label Block, Cell)
    drawing_area: tuple                # x0, y0, x1, y1 (page points, y up)
    value_font: str
    value_size: float
    value_tz: float
    value_oc: str | None

    def defaults(self) -> dict:
        out = {k: f.value.text for k, f in self.fields.items()}
        return out

    def rev_defaults(self) -> list:
        rows = []
        if self.rev:
            for r in range(len(self.rev.rows)):
                vals = {k: (self.rev.existing[(r, c)].text if (r, c) in self.rev.existing else "")
                        for c, k in enumerate(REV_KEYS)}
                rows.append(vals)
        return rows


# ------------------------------------------------------------------------------ geometry
def _segments(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as doc:
        p = doc.pages[0]
        H = float(p.height)
        segs = []
        for obj in p.lines + p.curves:
            pts = obj.get("pts") or [(obj["x0"], obj["top"]), (obj["x1"], obj["bottom"])]
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                segs.append((float(xa), H - float(ya), float(xb), H - float(yb)))
        for r in p.rects:
            x0, x1, y0, y1 = r["x0"], r["x1"], H - r["bottom"], H - r["top"]
            segs += [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]
    hs = [(min(xa, xb), max(xa, xb), (ya + yb) / 2) for xa, ya, xb, yb in segs if abs(ya - yb) < 0.3 and abs(xa - xb) > 2]
    vs = [(min(ya, yb), max(ya, yb), (xa + xb) / 2) for xa, ya, xb, yb in segs if abs(xa - xb) < 0.3 and abs(ya - yb) > 2]
    return hs, vs


def _cell(hs, vs, x, y) -> Cell:
    tol = 0.5
    right = [v[2] for v in vs if v[2] > x + tol and v[0] - tol <= y <= v[1] + tol]
    left = [v[2] for v in vs if v[2] < x - tol and v[0] - tol <= y <= v[1] + tol]
    top = [h[2] for h in hs if h[2] > y + tol and h[0] - tol <= x <= h[1] + tol]
    bot = [h[2] for h in hs if h[2] < y - tol and h[0] - tol <= x <= h[1] + tol]
    if not (right and left and top and bot):
        raise TemplateError(f"Could not find the table cell around ({x:.0f}, {y:.0f}).")
    return Cell(max(left), max(bot), min(right), min(top))


# ------------------------------------------------------------------------------ analysis
class _Fonts:
    def __init__(self, page):
        self.res = page.Resources.get("/Font", {})
        self.cache = {}

    def get(self, name):
        if name not in self.cache:
            f = self.res.get(name)
            if f is None:
                self.cache[name] = None
            else:
                cid = f.DescendantFonts[0] if f.get("/Subtype") == "/Type0" else None
                tu = parse_tounicode(f.ToUnicode.read_bytes()) if "/ToUnicode" in f else {}
                w = parse_w(cid.get("/W")) if cid is not None else {}
                dw = float(cid.get("/DW", 1000)) if cid is not None else 1000.0
                bold = "bold" in str(f.get("/BaseFont", "")).lower()
                self.cache[name] = (tu, w, dw, bold, cid is not None)
        return self.cache[name]


def _block(show: TextShow, fonts: _Fonts) -> Block | None:
    info = fonts.get(show.font)
    if info is None or not info[4]:
        return None
    tu, w, dw, bold, _ = info
    codes = show.codes
    gids = [int.from_bytes(codes[i:i + 2], "big") for i in range(0, len(codes) - 1, 2)]
    text = "".join(tu.get(g, "?") for g in gids)
    width = sum(w.get(g, dw) for g in gids) / 1000.0 * show.size * show.x_scale
    x, y = show.origin
    return Block(show, text, x, x + width, y, show.page_size, bold)


def analyse(path) -> TemplateInfo:
    path = str(path)
    pdf = pikepdf.open(path)
    try:
        if len(pdf.pages) != 1:
            raise TemplateError("The template must be a single-page PDF.")
        page = pdf.pages[0]
        if int(page.obj.get("/Rotate", 0)) % 360:
            raise TemplateError("Rotated template pages are not supported.")
        mb = [float(v) for v in page.MediaBox]
        W, H = mb[2] - mb[0], mb[3] - mb[1]
        shows, _, _ = walk_text(page)
        fonts = _Fonts(page)
        blocks = [b for b in (_block(s, fonts) for s in shows) if b is not None]
    finally:
        pdf.close()
    hs, vs = _segments(path)

    labels = [b for b in blocks if b.bold]
    values = [b for b in blocks if not b.bold and b.text.strip()]

    # revision table header: the bold column names sharing one baseline with 'Description'
    rev = None
    desc = [b for b in labels if b.text.strip() == "Description"]
    header = []
    if desc:
        d = desc[0]
        header = [b for b in labels if abs(b.y - d.y) < 1.5 and b.text.strip() in REV_COLUMNS]
        header.sort(key=lambda b: b.x0)
    header_ids = {id(b) for b in header}

    fields: dict[str, Field] = {}
    for lab in labels:
        key = FIELD_LABELS.get(lab.text.strip())
        if key is None or id(lab) in header_ids or key in fields:
            continue
        cell = _cell(hs, vs, lab.x0 + 1, lab.y + 1)
        cands = [v for v in values if v.y < lab.y - 0.5 and v.y > cell.y0 and abs(v.x0 - lab.x0) < 5
                 and v.x0 < cell.x1]
        if not cands:
            continue
        val = max(cands, key=lambda v: v.y)
        fields[key] = Field(key, lab, val, cell)
    missing = [FIELD_NAMES[k] for k in FIELD_LABELS.values() if k not in fields]
    if missing:
        raise TemplateError("Could not find these title block fields in the template: " + ", ".join(missing))

    if [b.text.strip() for b in header] == REV_COLUMNS:
        hcell = _cell(hs, vs, header[0].x0 + 0.5, header[0].y + 0.5)
        cols = []
        for b in header:
            c = _cell(hs, vs, b.x0 + 0.5, b.y + 0.5)
            cols.append([b.text.strip(), c.x0, c.x1])
        tx0, tx1 = cols[0][1], cols[-1][2]
        span = [h[2] for h in hs if h[0] <= tx0 + 0.6 and h[1] >= tx1 - 0.6]
        above = sorted(y for y in span if y >= hcell.y1 - 0.3)
        below = sorted((y for y in span if y <= hcell.y0 + 0.3), reverse=True)
        lines = above if len(above) >= len(below) else below
        uniq = []
        for y in lines:
            if not uniq or abs(y - uniq[-1]) > 1.0:
                uniq.append(y)
        rows = [(min(a, b), max(a, b)) for a, b in zip(uniq, uniq[1:])]
        if rows:   # drop slivers such as the gap of a double rule
            med = sorted(r[1] - r[0] for r in rows)[len(rows) // 2]
            rows = [r for r in rows if r[1] - r[0] > 0.6 * med]
        existing, offsets = {}, []
        for v in values:
            for ri, (y0, y1) in enumerate(rows):
                if y0 < v.y < y1:
                    for ci, (_, x0, x1) in enumerate(cols):
                        if x0 < v.x0 < x1:
                            existing[(ri, ci)] = v
                            offsets.append(v.y - y0)
        inset = min((f.value.x0 - f.cell.x0 for f in fields.values()), default=1.8)
        rcols = []
        for ci, (name, x0, x1) in enumerate(cols):
            sample = next((existing[k] for k in sorted(existing) if k[1] == ci), None)
            if sample is None or abs(sample.centre - (x0 + x1) / 2) < 1.5:
                rcols.append(RevColumn(name, x0, x1, "centre", 0.0))
            else:
                rcols.append(RevColumn(name, x0, x1, "left", min(sample.x0 - x0, inset)))
        rh = rows[0][1] - rows[0][0] if rows else 10
        size0 = next(iter(existing.values())).size if existing else 4.5
        off = sum(offsets) / len(offsets) if offsets else (rh - size0 * 0.72) / 2
        rev = RevTable(rcols, rows, off, existing)

    notes = None
    nl = [b for b in labels if b.text.strip() == NOTES_LABEL]
    if nl:
        notes = (nl[0], _cell(hs, vs, nl[0].x0 + 1, nl[0].y - nl[0].size * 2))

    # drawing area: above the title block, right of the sheet border
    W_long = [h for h in hs if h[1] - h[0] > 0.8 * W]
    V_long = [v for v in vs if v[1] - v[0] > 0.5 * H]
    tb_top = max((h[2] for h in W_long if h[2] < 0.5 * H), default=None)
    if tb_top is None:
        raise TemplateError("Could not find the top of the title block.")
    x_left = min((v[2] for v in V_long if v[2] < 0.2 * W), default=mb[0] + 20)
    y_top = max((v[1] for v in V_long if v[2] < 0.2 * W), default=H - 20)
    x_right = max(h[1] for h in W_long if abs(h[2] - tb_top) < 3)
    area = (x_left, tb_top, x_right, y_top)

    vb = fields["title"].value
    return TemplateInfo(path, W, H, paper_name(W, H), fields, rev, notes, area,
                        vb.show.font, vb.size, vb.show.tz, vb.show.oc)


# ------------------------------------------------------------------------------ editing
def wrap(text: str, width_of, max_width: float) -> list[str]:
    """Greedy word wrap; words longer than a line are broken."""
    out = []
    for para in text.replace("\r\n", "\n").split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if width_of(cand) <= max_width:
                line = cand
                continue
            if line:
                out.append(line)
            line = ""
            while width_of(word) > max_width:
                k = len(word)
                while k > 1 and width_of(word[:k]) > max_width:
                    k -= 1
                out.append(word[:k])
                word = word[k:]
            line = word
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


class Editor:
    """Fills one copy of the template.  Call the setters, then ``finish()`` and save ``pdf``."""

    LINE_PITCH = 1.45      # x font size

    def __init__(self, info: TemplateInfo, pdf: pikepdf.Pdf | None = None):
        self.info = info
        self.pdf = pdf or pikepdf.open(info.path)
        self.page = self.pdf.pages[0]
        self.streams = content_list(self.page)
        self.shows, self.parsed, _ = walk_text(self.page)
        self.dirty: set[int] = set()
        self.font = FontWriter(self.pdf, self.page.Resources.Font[info.value_font])
        self.new_text: list = []      # (x, y, gids)
        self.prepend: list[bytes] = []
        self.edit_boxes: list = []    # page boxes whose look changes (for validation)
        self.expected: dict = {}      # key -> text expected in that box

    # -- measuring
    def width(self, text: str, size=None) -> float:
        size = size or self.info.value_size
        return self.font.text_width(text) * size * self.info.value_tz / 100.0

    def _show_of(self, block: Block) -> TextShow:
        for s in self.shows:
            if s.stream == block.show.stream and s.op == block.show.op:
                return s
        raise TemplateError("internal: text block not found")

    # -- low level
    def _replace(self, block: Block, text: str, align="left", centre_x=None):
        s = self._show_of(block)
        gids = self.font.encode(text) if text else []
        ops = self.parsed[s.stream]
        ins = ops[s.op]
        if str(ins.operator) != "Tj":
            raise TemplateError("internal: only Tj text can be replaced")
        ops[s.op] = pikepdf.ContentStreamInstruction([pikepdf.String(hexcodes(gids))], pikepdf.Operator("Tj"))
        new_w = self.font.width(gids) * s.size * s.x_scale
        x_new = block.x0
        if align == "centre":
            cx = centre_x if centre_x is not None else block.centre
            x_new = cx - new_w / 2
        if abs(x_new - block.x0) > 1e-6:
            if s.tm_op is None:
                raise TemplateError("internal: cannot move text without a Tm")
            tm = ops[s.tm_op]
            vals = [float(v) for v in tm.operands]
            m = s.ctm
            # shift along page x: convert to text-matrix units (no rotation in AutoCAD title blocks)
            vals[4] += (x_new - block.x0) / m[0]
            ops[s.tm_op] = pikepdf.ContentStreamInstruction([round(v, 4) for v in vals], pikepdf.Operator("Tm"))
        self.dirty.add(s.stream)
        self.edit_boxes.append((min(block.x0, x_new), block.y - block.size * 0.35,
                                max(block.x1, x_new + new_w), block.y + block.size * 1.0))
        return x_new, new_w

    def _shift(self, block: Block, dx: float, new_width: float):
        """Move a (replaced) text block sideways."""
        s = self._show_of(block)
        ops = self.parsed[s.stream]
        vals = [float(v) for v in ops[s.tm_op].operands]
        vals[4] += dx / s.ctm[0]
        ops[s.tm_op] = pikepdf.ContentStreamInstruction([round(v, 4) for v in vals], pikepdf.Operator("Tm"))
        self.edit_boxes.append((block.x0 + dx, block.y - block.size * 0.35,
                                block.x0 + dx + new_width, block.y + block.size))

    def _add(self, x, y, text):
        if not text:
            return
        gids = self.font.encode(text)
        w = self.font.width(gids) * self.info.value_size * self.info.value_tz / 100.0
        self.new_text.append((x, y, gids))
        size = self.info.value_size
        self.edit_boxes.append((x, y - size * 0.35, x + w, y + size))

    # -- fields
    def check(self, values: dict, rev_rows: list, notes: str) -> dict:
        """{field key: problem} for values that cannot be written.  Empty dict = all fine."""
        errs = {}
        for key, text in values.items():
            f = self.info.fields.get(key)
            if f is None:
                continue
            try:
                lines = self._field_lines(f, text)
            except FontError as e:
                errs[key] = str(e)
                continue
            except TemplateError as e:
                errs[key] = str(e)
                continue
            if key not in MULTILINE_FIELDS:
                over = self.width(text) - f.max_width
                if over > 0:
                    errs[key] = f"{FIELD_NAMES[key]} is too long for its box (about {self._chars(over)} characters too many)."
            del lines
        if self.info.rev:
            for r, row in enumerate(rev_rows):
                for c, k in enumerate(REV_KEYS):
                    t = (row or {}).get(k, "")
                    if not t:
                        continue
                    if r >= len(self.info.rev.rows):
                        errs[f"revrow{r}"] = f"The revision table only has {len(self.info.rev.rows)} rows."
                        continue
                    col = self.info.rev.columns[c]
                    try:
                        over = self.width(t) - (col.x1 - col.x0 - 2 - (col.offset if col.align == 'left' else 0))
                    except FontError as e:
                        errs[f"revrow{r}.{k}"] = str(e)
                        continue
                    if over > 0:
                        errs[f"revrow{r}.{k}"] = f"Revision row {r + 1} {col.name} is too long (about {self._chars(over)} characters too many)."
        if notes and self.info.notes:
            try:
                self._notes_lines(notes)
            except (TemplateError, FontError) as e:
                errs["notes"] = str(e)
        return errs

    def _chars(self, over_pt):
        return max(1, int(round(over_pt / (self.info.value_size * 0.55))))

    def _pitch(self):
        return self.info.value_size * self.LINE_PITCH

    def _field_lines(self, f: Field, text: str) -> list[str]:
        if f.key not in MULTILINE_FIELDS:
            if "\n" in text:
                raise TemplateError(f"{FIELD_NAMES[f.key]} must be a single line.")
            self.font.encode(text)
            return [text]
        lines = wrap(text, self.width, f.max_width)
        room = int((f.value.y - (f.cell.y0 + self.info.value_size * 0.4)) // self._pitch()) + 1
        if len(lines) > room:
            raise TemplateError(f"{FIELD_NAMES[f.key]} needs {len(lines)} lines but its box holds {room}.")
        return lines

    def _notes_lines(self, text: str):
        lab, cell = self.info.notes
        x = lab.x0
        maxw = cell.x1 - x - 3
        lines = wrap(text, self.width, maxw)
        first = lab.y - self._pitch() * 1.25
        room = int((first - (cell.y0 + self.info.value_size * 0.5)) // self._pitch()) + 1
        if len(lines) > room:
            raise TemplateError(f"Notes need {len(lines)} lines but the box holds {room}.")
        return x, first, lines

    def set_field(self, key: str, text: str):
        f = self.info.fields[key]
        text = text or ""
        if text == f.value.text:
            self.expected[key] = text
            return
        lines = self._field_lines(f, text)
        self._replace(f.value, lines[0] if lines else "")
        for i, line in enumerate(lines[1:], start=1):
            self._add(f.value.x0, f.value.y - i * self._pitch(), line)
        self.expected[key] = " ".join(lines)

    def set_revisions(self, rows: list):
        t = self.info.rev
        if t is None:
            return
        for r in range(len(t.rows)):
            row = rows[r] if r < len(rows) else {}
            for c, k in enumerate(REV_KEYS):
                text = (row or {}).get(k, "") or ""
                col = t.columns[c]
                y = t.rows[r][0] + t.baseline_offset
                existing = t.existing.get((r, c))
                if existing is not None:
                    misplaced = col.align == "left" and abs(existing.x0 - (col.x0 + col.offset)) > 0.3
                    if misplaced and text:
                        _, w = self._replace(existing, text, "left")
                        self._shift(existing, col.x0 + col.offset - existing.x0, w)
                    elif text != existing.text:
                        self._replace(existing, text, col.align, (col.x0 + col.x1) / 2)
                elif text:
                    w = self.width(text)
                    x = (col.x0 + col.x1) / 2 - w / 2 if col.align == "centre" else col.x0 + col.offset
                    self._add(x, y, text)
                if text:
                    self.expected[f"revrow{r}.{k}"] = text
        if len(rows) > len(t.rows) and any(any(v for v in (row or {}).values()) for row in rows[len(t.rows):]):
            raise TemplateError(f"The revision table only has {len(t.rows)} rows.")

    def set_notes(self, text: str):
        if not text or not text.strip() or self.info.notes is None:
            return
        x, y, lines = self._notes_lines(text)
        for i, line in enumerate(lines):
            self._add(x, y - i * self._pitch(), line)
        self.expected["notes"] = " ".join(lines)

    # -- drawing
    def add_drawing(self, form: pikepdf.Object, matrix: tuple, layer_name="H2X drawing"):
        """Draw a Form XObject (the H2X sheet) under the template, on its own PDF layer."""
        res = self.page.Resources
        if "/XObject" not in res:
            res.XObject = pikepdf.Dictionary()
        name = "/H2XDrawing"
        k = 0
        while name in res.XObject:
            k += 1
            name = f"/H2XDrawing{k}"
        res.XObject[name] = form
        oc_name = self._layer(layer_name)
        m = " ".join(f"{v:.6f}" for v in matrix)
        self.prepend.append(f"q\n/OC {oc_name} BDC\nq {m} cm {name} Do Q\nEMC\nQ\n".encode())
        return name

    def _layer(self, layer_name):
        root = self.pdf.Root
        ocg = self.pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(layer_name)))
        if "/OCProperties" not in root:
            root.OCProperties = pikepdf.Dictionary(OCGs=pikepdf.Array(), D=pikepdf.Dictionary(Order=pikepdf.Array()))
        ocp = root.OCProperties
        ocp.OCGs.append(ocg)
        d = ocp.get("/D")
        if d is not None:
            if "/Order" not in d:
                d.Order = pikepdf.Array()
            d.Order.append(ocg)
        res = self.page.Resources
        if "/Properties" not in res:
            res.Properties = pikepdf.Dictionary()
        name = "/ocH2X"
        res.Properties[name] = ocg
        return name

    # -- write
    def finish(self):
        self.font.commit()
        for si in sorted(self.dirty):
            self.streams[si].write(pikepdf.unparse_content_stream(self.parsed[si]))
        chunks = list(self.prepend)
        if self.new_text:
            info = self.info
            body = []
            for x, y, gids in self.new_text:
                body.append(f"BT {info.value_font} {info.value_size:.5f} Tf {info.value_tz:.5f} Tz "
                            f"1 0 0 1 {x:.4f} {y:.4f} Tm <{hexcodes(gids).hex()}> Tj ET")
            tag = f"/OC {info.value_oc} BDC\n" if info.value_oc else ""
            end = "EMC\n" if info.value_oc else ""
            chunks.append(("q\n0 0 0 rg\n" + tag + "\n".join(body) + "\n" + end + "Q\n").encode())
        if chunks:
            new = self.pdf.make_stream(b"".join(chunks))
            arr = pikepdf.Array([new] + list(self.streams))
            self.page.obj.Contents = arr
        self.prepend, self.new_text = [], []


def default_template() -> Path:
    from .pdfutil import asset
    return asset("Synergi_A3_Drawing_Template.pdf")


def bump_number(doc: str, k: int) -> str:
    """Add k to the trailing number of a document number, keeping its width."""
    m = re.search(r"(\d+)(\D*)$", doc or "")
    if not m or k == 0:
        return doc
    n = m.group(1)
    return doc[:m.start(1)] + str(int(n) + k).zfill(len(n)) + m.group(2)

