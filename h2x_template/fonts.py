"""Write new text with the template's own embedded Comfortaa font.

The AutoCAD template embeds a GID-preserving subset of Comfortaa 3.105 (the variable font, default
instance = Regular) as a Type0/Identity-H font.  Characters that are not yet in the subset are
added by swapping in the complete, bundled Comfortaa 3.105 font program - but only after proving
that every glyph already in the template is identical in it (outline point-for-point and advance
width), so existing text cannot change by a single pixel.
"""
from __future__ import annotations

import io
import re

import pikepdf
from fontTools.ttLib import TTFont

from .pdfutil import asset


class FontError(ValueError):
    pass


_SRC = {}


def source_font() -> tuple[TTFont, bytes]:
    if "f" not in _SRC:
        data = asset("Comfortaa-VF.ttf").read_bytes()
        _SRC["f"] = (TTFont(io.BytesIO(data)), data)
    return _SRC["f"]


def parse_tounicode(data: bytes) -> dict[int, str]:
    text = data.decode("latin-1")
    out: dict[int, str] = {}
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for a, b in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            out[int(a, 16)] = bytes.fromhex(b).decode("utf-16-be", "replace")
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for a, b, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", blk):
            lo, hi = int(a, 16), int(b, 16)
            if rest.startswith("["):
                for i, v in zip(range(lo, hi + 1), re.findall(r"<([0-9A-Fa-f]+)>", rest)):
                    out[i] = bytes.fromhex(v).decode("utf-16-be", "replace")
            else:
                start = int(rest[1:-1], 16)
                for i in range(lo, hi + 1):
                    out[i] = chr(start + i - lo)
    return out


def build_tounicode(mapping: dict[int, str]) -> bytes:
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    items = sorted(mapping.items())
    for k in range(0, len(items), 100):
        chunk = items[k:k + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for g, u in chunk:
            lines.append(f"<{g:04X}> <{u.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def parse_w(w) -> dict[int, float]:
    out, k, w = {}, 0, list(w or [])
    while k < len(w):
        c = int(w[k])
        nxt = w[k + 1]
        if isinstance(nxt, pikepdf.Array):
            for j, v in enumerate(nxt):
                out[c + j] = float(v)
            k += 2
        else:
            for g in range(c, int(nxt) + 1):
                out[g] = float(w[k + 2])
            k += 3
    return out


def _sig(tt: TTFont, gid: int):
    order = tt.getGlyphOrder()
    if gid >= len(order):
        return None
    name = order[gid]
    g = tt["glyf"][name]
    if g.numberOfContours == 0:
        return ("empty", tt["hmtx"][name][0])
    coords, ends, flags = g.getCoordinates(tt["glyf"])
    return (list(coords), list(ends), [f & 1 for f in flags], tt["hmtx"][name][0])


class FontWriter:
    """Encodes text for one Type0 Comfortaa font resource of the template."""

    def __init__(self, pdf: pikepdf.Pdf, font: pikepdf.Object):
        self.pdf = pdf
        self.font = font
        if font.get("/Subtype") != "/Type0" or str(font.get("/Encoding")) != "/Identity-H":
            raise FontError("Template font is not an Identity-H Type0 font.")
        self.cid = font.DescendantFonts[0]
        desc = self.cid.FontDescriptor
        self.emb_bytes = desc.FontFile2.read_bytes()
        self.emb = TTFont(io.BytesIO(self.emb_bytes))
        self.src, self.src_bytes = source_font()
        self.cmap = self.src.getBestCmap()
        self.widths = parse_w(self.cid.get("/W"))
        self.dw = float(self.cid.get("/DW", 1000))
        self.tounicode = parse_tounicode(font.ToUnicode.read_bytes()) if "/ToUnicode" in font else {}
        emb_order = self.emb.getGlyphOrder()
        self.has_outline = {i for i, n in enumerate(emb_order)
                            if self.emb["glyf"][n].numberOfContours != 0}
        # Is the bundled font identical for every glyph the template already draws?
        self.mismatch = [g for g in sorted(self.has_outline) if _sig(self.emb, g) != _sig(self.src, g)]
        self.new_gids: set[int] = set()
        self.swap_needed = False

    @property
    def compatible(self) -> bool:
        return not self.mismatch

    def encode(self, text: str) -> list[int]:
        missing = sorted({ch for ch in text if ord(ch) not in self.cmap})
        if missing:
            raise FontError("These characters are not in the Synergi font (Comfortaa): " +
                            " ".join(repr(c) for c in missing))
        gids = [self._gid(ord(ch)) for ch in text]
        for g, ch in zip(gids, text):
            if g not in self.has_outline and ch != " " and _sig(self.src, g)[0] != "empty":
                if not self.compatible:
                    raise FontError(f"Character {ch!r} is not in the template's font subset and the "
                                    "template font differs from the bundled Comfortaa, so it cannot be added.")
                self.swap_needed = True
            if g not in self.widths or g not in self.tounicode:
                self.new_gids.add(g)
                self.tounicode.setdefault(g, ch)
        return gids

    _gid_cache: dict = {}

    def _gid(self, cp: int) -> int:
        if cp not in self._gid_cache:
            self._gid_cache[cp] = self.src.getGlyphID(self.cmap[cp])
        return self._gid_cache[cp]

    def advance(self, gid: int) -> float:
        if gid in self.widths:
            return self.widths[gid]
        return float(self.src["hmtx"][self.src.getGlyphOrder()[gid]][0])

    def width(self, gids) -> float:
        """Advance of a string in text-space units for size 1."""
        return sum(self.advance(g) for g in gids) / 1000.0

    def text_width(self, text: str) -> float:
        return self.width(self.encode(text))

    def commit(self):
        """Write W/ToUnicode additions and, if needed, the complete font program."""
        if self.swap_needed:
            stream = pikepdf.Stream(self.pdf, self.src_bytes)
            stream.Length1 = len(self.src_bytes)
            self.cid.FontDescriptor.FontFile2 = stream
            self.swap_needed = False
        add = sorted(g for g in self.new_gids if g not in self.widths)
        if add:
            w = pikepdf.Array(list(self.cid.get("/W", pikepdf.Array())))
            for g in add:
                adv = self.advance(g)
                w.append(g)
                w.append(pikepdf.Array([int(round(adv))]))
                self.widths[g] = float(int(round(adv)))
            self.cid.W = w
        if self.new_gids:
            self.font.ToUnicode = pikepdf.Stream(self.pdf, build_tounicode(self.tounicode))
        self.new_gids.clear()


def hexcodes(gids) -> bytes:
    return b"".join(g.to_bytes(2, "big") for g in gids)
