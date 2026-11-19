"""Put H2X sheets into the Synergi template.

The H2X page is inserted as a Form XObject whose content stream is the H2X page content byte for
byte and whose resources are exact copies.  It is only ever scaled uniformly and moved; nothing in
it is redrawn, re-encoded or edited.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

from . import h2x as h2xmod
from . import template as tpl
from .pdfutil import content_list

STANDARD_SCALES = [1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750,
                   1000, 1250, 1500, 2000, 2500, 5000]
MARGIN = 8.0          # clearance between the drawing and the sheet border / title block (points)


class ComposeError(ValueError):
    pass


@dataclass
class Placement:
    crop: tuple            # region of the H2X page shown (H2X page points, y up)
    matrix: tuple          # H2X page -> template page
    scale: float
    denominator: int | None
    scale_text: str        # for the title block, e.g. '1:125' or 'NTS'

    @property
    def bbox(self):
        s, tx, ty = self.matrix[0], self.matrix[4], self.matrix[5]
        x0, y0, x1, y1 = self.crop
        return (x0 * s + tx, y0 * s + ty, x1 * s + tx, y1 * s + ty)


@dataclass
class SheetSpec:
    index: int                      # H2X page index
    title: str
    document_number: str


@dataclass
class Job:
    template: str
    h2x: str
    sheets: list                    # [SheetSpec]
    shared: dict                    # project, client, status, drawn, date, rev, size, scale (optional override)
    revisions: list = field(default_factory=list)
    notes: str = ""
    crop_mode: str = "content"      # content | frame | page
    scale_mode: str = "standard"    # standard | fit
    same_layout: bool = True        # one crop + scale for every sheet so plans line up between levels
    output_dir: str = "."
    combined: bool = False


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def drawing_area(info: tpl.TemplateInfo, margin=MARGIN):
    x0, y0, x1, y1 = info.drawing_area
    return (x0 + margin, y0 + margin, x1 - margin, y1 - margin)


def plan(info: tpl.TemplateInfo, sheets: list, indices: list, crop_mode="content", scale_mode="standard",
         same_layout=True) -> dict:
    """{sheet index: Placement}."""
    by = {s.index: s for s in sheets}
    sel = [by[i] for i in indices]
    if not sel:
        return {}
    ax0, ay0, ax1, ay1 = drawing_area(info)
    aw, ah = ax1 - ax0, ay1 - ay0
    crops = {s.index: s.crop(crop_mode) for s in sel}
    if same_layout:
        u = _union(list(crops.values()))
        crops = {k: u for k in crops}
    fits = {k: min(aw / (c[2] - c[0]), ah / (c[3] - c[1])) for k, c in crops.items()}
    out = {}
    common = min(fits.values())
    for s in sel:
        crop = crops[s.index]
        fit = common if same_layout else fits[s.index]
        src = s.scale_denominator
        if scale_mode == "standard" and src:
            need = src / fit
            denom = next((d for d in STANDARD_SCALES if d >= need - 1e-9), None)
            if denom is None:
                denom = int(need) + 1
            scale = src / denom
            text = f"1:{denom}"
        else:
            scale, denom = fit, None
            text = "NTS"
        cw, ch = (crop[2] - crop[0]) * scale, (crop[3] - crop[1]) * scale
        tx = ax0 + (aw - cw) / 2 - crop[0] * scale
        ty = ay0 + (ah - ch) / 2 - crop[1] * scale
        out[s.index] = Placement(tuple(float(v) for v in crop),
                                 (round(scale, 6), 0.0, 0.0, round(scale, 6), round(tx, 4), round(ty, 4)),
                                 scale, denom, text)
    return out


def source_content(page) -> bytes:
    return b"\n".join(s.read_bytes() for s in content_list(page))


def _foreign(dst: pikepdf.Pdf, src: pikepdf.Pdf, obj):
    if not obj.is_indirect:
        obj = src.make_indirect(obj)
    return dst.copy_foreign(obj)


def make_form(dst: pikepdf.Pdf, src: pikepdf.Pdf, src_page, crop) -> pikepdf.Object:
    form = pikepdf.Stream(dst, source_content(src_page))
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = pikepdf.Array([round(float(v), 4) for v in crop])
    res = src_page.obj.get("/Resources")
    form.Resources = _foreign(dst, src, res) if res is not None else pikepdf.Dictionary()
    if "/Group" in src_page.obj:
        form.Group = _foreign(dst, src, src_page.obj.Group)
    return dst.make_indirect(form)


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(s: str) -> str:
    s = _BAD.sub("-", s).strip().rstrip(".")
    return re.sub(r"\s+", " ", s)[:180] or "drawing"


def filename(spec: SheetSpec, rev: str) -> str:
    base = spec.document_number or "drawing"
    if rev:
        base += f"_{rev}"
    if spec.title:
        base += f" - {spec.title}"
    return safe_name(base) + ".pdf"


def sheet_values(job: Job, spec: SheetSpec, placement: Placement, info: tpl.TemplateInfo) -> dict:
    v = {k: job.shared.get(k, "") for k in ("project", "client", "status", "drawn", "date", "rev")}
    v["title"] = spec.title
    v["document_number"] = spec.document_number
    v["size"] = job.shared.get("size") or info.size_name or info.fields["size"].value.text
    sc = job.shared.get("scale") or ""
    v["scale"] = sc if sc and sc.lower() != "auto" else placement.scale_text
    return v


@dataclass
class Result:
    spec: SheetSpec
    path: Path | None
    ok: bool
    error: str = ""
    report: object = None


def preflight(job: Job, info=None, sheets=None) -> tuple[dict, dict]:
    """Check every value fits before anything is written.  Returns (errors, placements)."""
    info = info or tpl.analyse(job.template)
    sheets = sheets or h2xmod.analyse(job.h2x)
    n = len(sheets)
    errs = {}
    for spec in job.sheets:
        if not 0 <= spec.index < n:
            errs[f"sheet{spec.index}"] = f"The H2X PDF has no page {spec.index + 1}."
    if errs:
        return errs, {}
    places = plan(info, sheets, [s.index for s in job.sheets], job.crop_mode, job.scale_mode, job.same_layout)
    ed = tpl.Editor(info)
    try:
        for spec in job.sheets:
            vals = sheet_values(job, spec, places[spec.index], info)
            for k, msg in ed.check(vals, job.revisions, job.notes).items():
                key = k if k not in ("title", "document_number") else f"{k}{spec.index}"
                errs[key] = msg if k not in ("title", "document_number") else f"Sheet {spec.index + 1}: {msg}"
    finally:
        ed.pdf.close()
    return errs, places


def build_sheet(job: Job, spec: SheetSpec, info, sheet, placement, out_path: Path) -> object:
    from . import validate
    pdf = pikepdf.open(job.template)
    src = pikepdf.open(job.h2x)
    try:
        ed = tpl.Editor(info, pdf)
        vals = sheet_values(job, spec, placement, info)
        for k, v in vals.items():
            ed.set_field(k, v)
        ed.set_revisions(job.revisions)
        ed.set_notes(job.notes)
        form = make_form(pdf, src, src.pages[spec.index], placement.crop)
        ed.add_drawing(form, placement.matrix)
        ed.finish()
        pdf.docinfo["/Title"] = f"{vals['document_number']} - {vals['title']}"
        pdf.docinfo["/Producer"] = "Synergi H2X Template Tool (pikepdf)"
        pdf.docinfo["/ModDate"] = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")
        tmp = out_path.with_name(out_path.stem + ".tmp.pdf")
        pdf.save(tmp)
    finally:
        pdf.close()
        src.close()
    report = validate.validate(job, info, sheet, placement, ed, tmp)
    if not report.passed:
        tmp.unlink(missing_ok=True)
        raise validate.ValidationFailed(report)
    tmp.replace(out_path)
    return report


def combine(paths: list, out: Path):
    """One PDF holding every sheet (layers kept)."""
    pdf = pikepdf.new()
    opened = []
    try:
        for p in paths:
            src = pikepdf.open(p)
            opened.append(src)
            if len(opened) == 1 and "/OCProperties" in src.Root:
                pdf.Root.OCProperties = pdf.copy_foreign(src.make_indirect(src.Root.OCProperties))
            pdf.pages.extend(src.pages)
        if "/OCProperties" in pdf.Root:
            ocgs = pdf.Root.OCProperties.OCGs
            known = {o.objgen for o in ocgs}
            for page in pdf.pages:
                for _, o in page.Resources.get("/Properties", {}).items():
                    if o.get("/Type") == "/OCG" and o.objgen not in known:
                        ocgs.append(o)
                        known.add(o.objgen)
        pdf.save(out)
    finally:
        for s in opened:
            s.close()


def run(job: Job, progress=None) -> list[Result]:
    info = tpl.analyse(job.template)
    sheets = h2xmod.analyse(job.h2x)
    errs, places = preflight(job, info, sheets)
    if errs:
        raise ComposeError("Please fix these first:\n" + "\n".join(f"• {m}" for m in errs.values()))
    out_dir = Path(job.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by = {s.index: s for s in sheets}
    results = []
    rev = job.shared.get("rev", "")
    for n, spec in enumerate(job.sheets):
        if progress:
            progress(n, len(job.sheets), spec)
        path = out_dir / filename(spec, rev)
        try:
            rep = build_sheet(job, spec, info, by[spec.index], places[spec.index], path)
            results.append(Result(spec, path, True, "", rep))
        except Exception as exc:  # noqa: BLE001 - reported per sheet
            rep = getattr(exc, "report", None)
            results.append(Result(spec, None, False, str(exc), rep))
    if job.combined:
        good = [r.path for r in results if r.ok]
        if len(good) > 1:
            first = job.sheets[0]
            name = safe_name(f"{first.document_number}_{rev} - All sheets ({len(good)})") + ".pdf"
            combine(good, out_dir / name)
            results.append(Result(SheetSpec(-1, "All sheets", ""), out_dir / name, True))
    return results
