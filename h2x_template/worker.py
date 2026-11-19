"""Work done for the window in a separate process, so the window never freezes.

Every function takes and returns plain picklable values.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def warm():
    from . import compose, h2x, template, validate  # noqa: F401
    import pdfplumber  # noqa: F401
    import pypdfium2  # noqa: F401
    return os.getpid()


def analyse(h2x_path: str, template_path: str) -> dict:
    from . import h2x, template
    info = template.analyse(template_path)
    sheets = h2x.analyse(h2x_path)
    return {
        "sheets": [dict(index=s.index, level=s.level, subtitle=s.subtitle, project=s.project,
                        scale=s.scale_denominator, paper=s.paper, title=s.default_title) for s in sheets],
        "defaults": info.defaults(),
        "rev_rows": info.rev_defaults(),
        "rev_count": len(info.rev.rows) if info.rev else 0,
        "size": info.size_name,
        "has_notes": info.notes is not None,
    }


def template_info(template_path: str) -> dict:
    from . import template
    info = template.analyse(template_path)
    return {"defaults": info.defaults(), "rev_rows": info.rev_defaults(),
            "rev_count": len(info.rev.rows) if info.rev else 0, "size": info.size_name,
            "has_notes": info.notes is not None}


def check(job) -> dict:
    """{'errors': {...}, 'scales': {index: '1:125'}}"""
    from . import compose
    try:
        errs, places = compose.preflight(job)
    except Exception as exc:  # noqa: BLE001
        return {"errors": {"job": str(exc)}, "scales": {}}
    return {"errors": errs, "scales": {k: p.scale_text for k, p in places.items()}}


def preview(job, sheet_index: int, dpi: int = 110) -> dict:
    """Build one sheet into a temporary file and render it to PNG."""
    from . import compose, h2x, template
    from .pdfutil import render, save_png
    try:
        info = template.analyse(job.template)
        sheets = {s.index: s for s in h2x.analyse(job.h2x)}
        errs, places = compose.preflight(job, info, list(sheets.values()))
        if errs:
            return {"error": "\n".join(errs.values())}
        spec = next(s for s in job.sheets if s.index == sheet_index)
        d = Path(tempfile.mkdtemp(prefix="h2x-preview-"))
        out = d / "preview.pdf"
        compose.build_sheet(job, spec, info, sheets[sheet_index], places[sheet_index], out)
        png = d / "preview.png"
        save_png(render(out, 0, dpi), png)
        return {"png": str(png), "pdf": str(out)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def generate(job) -> dict:
    from . import compose
    try:
        res = compose.run(job)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "results": []}
    out = []
    for r in res:
        out.append({"title": r.spec.title, "index": r.spec.index, "path": str(r.path) if r.path else "",
                    "ok": r.ok, "error": r.error,
                    "notes": [c.name + (f" - {c.detail}" if c.detail else "") for c in (r.report.warnings() if r.report else [])]})
    return {"error": "", "results": out}
