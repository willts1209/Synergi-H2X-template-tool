"""End-to-end tests on a synthetic H2X export (no client data in the repo)."""
from __future__ import annotations

import sys
from pathlib import Path

import pikepdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_sample_h2x  # noqa: E402
from h2x_template import compose, h2x, template, validate  # noqa: E402
from h2x_template.compose import Job, SheetSpec  # noqa: E402
from h2x_template.pdfutil import content_list  # noqa: E402
from h2x_template.template import bump_number, default_template  # noqa: E402


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    p = tmp_path_factory.mktemp("h2x") / "sample_h2x.pdf"
    make_sample_h2x.make(p)
    return p


def _job(sample, out, **kw):
    info = template.analyse(default_template())
    d = info.defaults()
    sheets = [SheetSpec(s.index, s.default_title, bump_number(d["document_number"], k))
              for k, s in enumerate(h2x.analyse(sample))]
    rows = [dict(rev="P01", date="2026-11-19", description="Initial Issue", drn="WTS", chk="SH")]
    shared = dict(project="SES-P-000 Test", client="Test Client", status="S2 - Suitable for Information",
                  drawn="WTS", date="2026-11-19", rev="P01")
    return Job(str(default_template()), str(sample), sheets, shared, rows, "Test note.",
               output_dir=str(out), **kw)


def test_reads_h2x_sheets(sample):
    sheets = h2x.analyse(sample)
    assert [s.level for s in sheets] == ["GROUND", "LEVEL 1"]
    assert all(s.scale_denominator == 100 for s in sheets)
    assert sheets[0].default_title == "Ground - Heat Loss Layout"


def test_generates_and_validates_every_sheet(sample, tmp_path):
    res = compose.run(_job(sample, tmp_path, combined=True))
    sheets = [r for r in res if r.spec.index >= 0]
    assert len(sheets) == 2
    for r in sheets:
        assert r.ok, r.error
        assert r.report.passed, r.report.summary()
    assert any(r.spec.index == -1 and r.ok for r in res)        # combined PDF


def test_drawing_is_byte_identical(sample, tmp_path):
    res = compose.run(_job(sample, tmp_path))
    src = pikepdf.open(sample)
    for r in res:
        out = pikepdf.open(r.path)
        xo = out.pages[0].Resources.XObject
        form = next(xo[k] for k in xo.keys() if str(k).startswith("/H2XDrawing"))
        want = b"\n".join(s.read_bytes() for s in content_list(src.pages[r.spec.index]))
        assert form.read_bytes() == want
        assert validate.same_object(form.Resources, src.pages[r.spec.index].Resources)
        out.close()
    src.close()


def test_overlong_value_is_rejected_before_writing(sample, tmp_path):
    job = _job(sample, tmp_path)
    job.shared["project"] = "X" * 400
    errs, _ = compose.preflight(job)
    assert errs
    with pytest.raises(compose.ComposeError):
        compose.run(job)
    assert not list(tmp_path.glob("*.pdf"))
