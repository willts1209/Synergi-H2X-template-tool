"""Small PDF helpers: matrices, a content-stream walker for text, rendering."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pikepdf

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def asset(name: str) -> Path:
    """Path of a bundled file (works from source and inside the PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    p = base / "h2x_template" / "assets" / name
    if not p.exists():
        p = Path(__file__).resolve().parent / "assets" / name
    return p


def mat_mul(a, b):
    """a then b (PDF convention: point * a * b)."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def mat_inv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))


def apply(m, x, y):
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


def nums(operands):
    return tuple(float(v) for v in operands)


def content_list(page) -> list:
    """The page's content streams as a list (a single stream becomes a one-item list)."""
    c = page.obj.get("/Contents")
    if c is None:
        return []
    if isinstance(c, pikepdf.Array):
        return [s for s in c]
    return [c]


@dataclass
class TextShow:
    """One Tj/TJ operation: where it is in the page and what it draws."""
    stream: int            # index in content_list(page)
    op: int                # index of the Tj op in that stream's parsed operations
    tm_op: int | None      # index of the Tm op that positioned it (same BT block), if any
    font: str              # font resource name, e.g. '/F2'
    size: float            # Tf size (text space)
    tz: float              # horizontal scaling, percent
    ctm: tuple             # CTM in force
    tm: tuple              # text matrix at the start of the show
    codes: bytes           # raw string operand
    oc: str | None = None  # optional-content tag in force (/OC /oc2 BDC), if any
    extra: dict = field(default_factory=dict)

    @property
    def matrix(self):
        """Text space -> page space at the start of the string."""
        return mat_mul(self.tm, self.ctm)

    @property
    def origin(self):
        return apply(self.matrix, 0.0, 0.0)

    @property
    def page_size(self):
        """Font size in page points (vertical)."""
        m = self.matrix
        return self.size * (m[2] ** 2 + m[3] ** 2) ** 0.5

    @property
    def x_scale(self):
        """Page points per text-space unit along the baseline (including Tz)."""
        m = self.matrix
        return (m[0] ** 2 + m[1] ** 2) ** 0.5 * self.tz / 100.0


def walk_text(page) -> tuple[list[TextShow], list[list], tuple]:
    """Parse every content stream; return (text shows, parsed ops per stream, CTM at the end).

    Graphics state carries across stream boundaries, as PDF requires."""
    shows: list[TextShow] = []
    parsed: list[list] = []
    ctm, stack = IDENTITY, []
    font, size, tz = None, 0.0, 100.0
    tm = tlm = IDENTITY
    tm_op = None
    oc_stack: list = []
    for si, stream in enumerate(content_list(page)):
        ops = list(pikepdf.parse_content_stream(stream))
        parsed.append(ops)
        for oi, ins in enumerate(ops):
            op = str(ins.operator)
            a = ins.operands
            if op == "q":
                stack.append((ctm, font, size, tz))
            elif op == "Q":
                if stack:
                    ctm, font, size, tz = stack.pop()
            elif op == "cm":
                ctm = mat_mul(nums(a), ctm)
            elif op == "Tf":
                font, size = str(a[0]), float(a[1])
            elif op == "Tz":
                tz = float(a[0])
            elif op == "BT":
                tm = tlm = IDENTITY
                tm_op = None
            elif op == "Tm":
                tm = tlm = nums(a)
                tm_op = oi
            elif op == "Td":
                tlm = mat_mul((1, 0, 0, 1, float(a[0]), float(a[1])), tlm)
                tm = tlm
                tm_op = None
            elif op in ("Tj", "TJ"):
                codes = bytes(a[0]) if op == "Tj" else b"".join(bytes(x) for x in a[0] if isinstance(x, pikepdf.String))
                shows.append(TextShow(si, oi, tm_op, font, size, tz, ctm, tm, codes,
                                      oc_stack[-1] if oc_stack else None, {"op": op}))
            elif op == "BDC":
                tag = str(a[0])
                oc_stack.append(str(a[1]) if tag == "/OC" and isinstance(a[1], pikepdf.Name) else
                                (oc_stack[-1] if oc_stack else None))
            elif op == "BMC":
                oc_stack.append(oc_stack[-1] if oc_stack else None)
            elif op == "EMC":
                if oc_stack:
                    oc_stack.pop()
    return shows, parsed, ctm


# ----------------------------------------------------------------------------- rendering
def render(path_or_pdf, index: int, dpi: float, crop=None) -> np.ndarray:
    """Greyscale-free RGB render of a page as uint8 array.

    ``crop`` = (left, bottom, right, top) in points to cut from each edge."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path_or_pdf if not isinstance(path_or_pdf, Path) else str(path_or_pdf))
    try:
        page = doc[index]
        kw = {"scale": dpi / 72.0, "may_draw_forms": True}
        if crop is not None:
            kw["crop"] = tuple(max(0.0, c) for c in crop)
        bmp = page.render(**kw)
        img = bmp.to_numpy()
        if img.ndim == 3 and img.shape[2] >= 3:
            img = img[..., :3]
            # pypdfium2 renders BGR(x) by default
            img = img[..., ::-1]
        img = np.ascontiguousarray(img)
        page.close()
    finally:
        doc.close()
    return img


def save_png(img: np.ndarray, path):
    from PIL import Image
    Image.fromarray(img).save(path)
