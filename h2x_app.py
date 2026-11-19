"""Synergi H2X Drawing Tool - puts H2X heat loss sheets into the Synergi A3 drawing template.

Run ``python h2x_app.py``.  Packaged as SynergiH2XDrawings.exe by GitHub Actions.

``--self-test H2X.pdf OUTDIR``   generate every sheet without the window (CI)
``--preload H2X.pdf``            open the window with an H2X PDF already loaded
"""
from __future__ import annotations

import datetime
import json
import multiprocessing
import os
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from h2x_template import worker
from h2x_template.compose import Job, SheetSpec
from h2x_template.template import REV_KEYS, bump_number, default_template

APP_TITLE = "Synergi H2X Drawing Tool"
STATUSES = [
    "S0 - Work in Progress",
    "S1 - Suitable for Coordination",
    "S2 - Suitable for Information",
    "S3 - Suitable for Comment / Review",
    "S4 - Suitable for Stage Approval",
    "A - Approved / Construction",
]
CROP_MODES = [("content", "Drawing only (trimmed to what H2X drew)"),
              ("frame", "Whole H2X drawing frame"),
              ("page", "Whole H2X sheet, including H2X's title column")]
SCALE_MODES = [("standard", "Standard scale (e.g. 1:125)"), ("fit", "Fit to sheet (NTS)")]


def today() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "SynergiH2XDrawings" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_settings(d: dict):
    try:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------------------ headless
def self_test(argv) -> int:
    h2x, outdir = argv[:2]
    result = {}
    try:
        info = worker.analyse(h2x, str(default_template()))
        d = info["defaults"]
        sheets = [SheetSpec(s["index"], s["title"], bump_number(d["document_number"], k))
                  for k, s in enumerate(info["sheets"])]
        rows = [dict(rev="P01", date=today(), description="Initial Issue", drn="WTS", chk="SH"),
                dict(rev="P02", date=today(), description="Self-test revision", drn="WTS", chk="SH")]
        job = Job(str(default_template()), h2x, sheets,
                  dict(project="SES-P-000 Self Test", client="Self Test Client\n1 Test Street",
                       status=STATUSES[3], drawn="CI", date=today(), rev="P02"),
                  rows, "Self-test note.", output_dir=outdir, combined=True)
        res = worker.generate(job)
        result = {"ok": not res["error"] and all(r["ok"] for r in res["results"]) and len(res["results"]) > 0,
                  **res}
        # the window does its work in a worker process - check that works in this build too
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(1, mp_context=ctx) as ex:
            chk = ex.submit(worker.check, job).result(timeout=600)
        result["worker_check"] = chk
        result["ok"] = result["ok"] and not chk["errors"]
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
    Path(outdir).mkdir(parents=True, exist_ok=True)
    Path(outdir, "self_test_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result.get("ok") else 1


# ------------------------------------------------------------------------------ GUI
def run_gui(preload=None, _mainloop=True):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk

    GREEN = "#28695f"
    GREEN_DARK = "#1d4f47"
    GREEN_LIGHT = "#e3efeb"
    PAGE = "#eef2f1"
    CARD = "#ffffff"
    BORDER = "#d8e2df"
    INK = "#1d2e2b"
    MUTED = "#687a76"
    RED = "#b3261e"
    FONT = ["Segoe UI"]

    def f(size=9, weight="normal"):
        return (FONT[0], size, weight)

    def load_fonts(root):
        from h2x_template.pdfutil import asset
        if sys.platform.startswith("win"):
            try:
                import ctypes
                for style in ("Regular", "Bold"):
                    ctypes.windll.gdi32.AddFontResourceExW(str(asset(f"ComfortaaUI-{style}.ttf")), 0x10, 0)
            except Exception:  # noqa: BLE001
                pass
        if "Comfortaa" in tkfont.families(root):
            FONT[0] = "Comfortaa"
        for name, size, weight in (("TkDefaultFont", 9, "normal"), ("TkTextFont", 9, "normal"),
                                   ("TkMenuFont", 9, "normal"), ("TkHeadingFont", 9, "bold"),
                                   ("TkFixedFont", 9, "normal")):
            try:
                tkfont.nametofont(name).configure(family=FONT[0], size=size, weight=weight)
            except tk.TclError:
                pass

    def apply_theme(root):
        load_fonts(root)
        st = ttk.Style(root)
        st.theme_use("clam")
        root.configure(background=PAGE)
        root.option_add("*TCombobox*Listbox.font", f(9))
        root.option_add("*TCombobox*Listbox.selectBackground", GREEN)
        st.configure(".", background=CARD, foreground=INK, font=f(9), bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, troughcolor=PAGE, focuscolor=GREEN,
                     selectbackground=GREEN, selectforeground="white")
        st.configure("TFrame", background=CARD)
        st.configure("Page.TFrame", background=PAGE)
        st.configure("TLabel", background=CARD, foreground=INK)
        st.configure("Page.TLabel", background=PAGE, foreground=MUTED, font=f(8))
        st.configure("Hint.TLabel", foreground=MUTED, font=f(8))
        st.configure("Error.TLabel", foreground=RED, font=f(8, "bold"))
        st.configure("Ok.TLabel", foreground=GREEN, font=f(8, "bold"))
        st.configure("Field.TLabel", foreground=MUTED, font=f(8, "bold"))
        st.configure("CardTitle.TLabel", foreground=INK, font=f(10, "bold"))
        st.configure("Head.TLabel", background=GREEN_LIGHT, foreground=GREEN_DARK, font=f(8, "bold"), padding=(6, 5))
        st.configure("Banner.TFrame", background=GREEN)
        st.configure("Banner.TLabel", background=GREEN, foreground="white", font=f(13, "bold"))
        st.configure("BannerSub.TLabel", background=GREEN, foreground="#d7ebe6", font=f(8))
        st.configure("TEntry", fieldbackground="white", padding=(5, 3))
        st.map("TEntry", bordercolor=[("focus", GREEN)])
        st.configure("TCombobox", fieldbackground="white", padding=(5, 3))
        st.configure("TCheckbutton", background=CARD)
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("TButton", padding=(10, 4), background=CARD)
        st.map("TButton", background=[("active", GREEN_LIGHT)])
        st.configure("Accent.TButton", background=GREEN, foreground="white", padding=(16, 6), font=f(10, "bold"),
                     bordercolor=GREEN)
        st.map("Accent.TButton", background=[("disabled", "#9bb8b2"), ("active", GREEN_DARK)])
        st.configure("Small.TButton", padding=(6, 1), font=f(8))

    def card(parent, number, title, subtitle=""):
        outer = ttk.Frame(parent, padding=(14, 10))
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text=f"{number}  {title}", style="CardTitle.TLabel").pack(side="left")
        if subtitle:
            ttk.Label(head, text=subtitle, style="Hint.TLabel").pack(side="left", padx=10)
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(8, 0))
        return outer, body

    def open_path(p):
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:  # noqa: BLE001
            pass

    class SheetRow:
        def __init__(self, app, s):
            self.app = app
            self.index = s["index"]
            self.level = s["level"] or f"Page {s['index'] + 1}"
            self.use = tk.BooleanVar(value=True)
            self.title = tk.StringVar(value=s["title"])
            self.doc = tk.StringVar()
            self.scale = tk.StringVar(value="")
            self.doc_typed = False
            self.use.trace_add("write", lambda *_: (app.renumber(), app.changed()))
            self.title.trace_add("write", lambda *_: app.changed())
            self.doc.trace_add("write", lambda *_: self._doc_changed())
            self.widgets = []

        def _doc_changed(self):
            if self.app._setting_doc:
                return
            self.doc_typed = True
            if self.app.rows and self.app.rows[0] is self:
                self.app.renumber()
            self.app.changed()

        def grid(self, parent, r):
            w = [ttk.Checkbutton(parent, variable=self.use),
                 ttk.Label(parent, text=f"{self.index + 1}. {self.level}"),
                 ttk.Entry(parent, textvariable=self.title, width=36),
                 ttk.Entry(parent, textvariable=self.doc, width=36),
                 ttk.Label(parent, textvariable=self.scale, style="Hint.TLabel", width=7),
                 ttk.Button(parent, text="Preview", style="Small.TButton", command=lambda: self.app.preview(self.index))]
            for c, x in enumerate(w):
                x.grid(row=r, column=c, sticky="w" if c in (0, 1, 4) else "ew", padx=4, pady=2)
            self.widgets = w

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            apply_theme(self)
            self.title(APP_TITLE)
            self.geometry("1180x860")
            self.minsize(1000, 700)
            self.settings = load_settings()
            self.template_path = self.settings.get("template") or str(default_template())
            if not Path(self.template_path).exists():
                self.template_path = str(default_template())
            self.h2x_path = ""
            self.rows: list[SheetRow] = []
            self.rev_count = 7
            self.busy = False
            self._setting_doc = False
            self._check_after = None
            self._check_id = 0
            self._pending = []
            self.tdefaults = {"defaults": {}, "rev_rows": []}
            ctx = multiprocessing.get_context("spawn")
            self.pool = ProcessPoolExecutor(1, mp_context=ctx)
            self.pool.submit(worker.warm)
            self.vars = {k: tk.StringVar() for k in ("project", "status", "drawn", "date")}
            self.crop = tk.StringVar(value=CROP_MODES[0][1])
            self.scale_mode = tk.StringVar(value=SCALE_MODES[0][1])
            self.same_layout = tk.BooleanVar(value=True)
            self.auto_number = tk.BooleanVar(value=True)
            self.combined = tk.BooleanVar(value=bool(self.settings.get("combined", True)))
            self.out_dir = tk.StringVar(value=self.settings.get("output_dir", ""))
            self.status_text = tk.StringVar(value="Choose an H2X PDF to start.")
            self.check_text = tk.StringVar(value="")
            self.h2x_text = tk.StringVar(value="No H2X PDF chosen")
            self.tpl_text = tk.StringVar()
            self._build()
            self._update_template_label()
            self._load_template_defaults()
            for v in list(self.vars.values()) + [self.crop, self.scale_mode, self.same_layout]:
                v.trace_add("write", lambda *_: self.changed())
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(100, self._drain)

        # ------------------------------------------------------------ layout
        def _build(self):
            banner = ttk.Frame(self, style="Banner.TFrame", padding=(16, 10))
            banner.pack(fill="x")
            ttk.Label(banner, text=APP_TITLE, style="Banner.TLabel").pack(side="left")
            ttk.Label(banner, text="H2X heat loss sheets → Synergi A3 drawing template. The H2X drawing is "
                                   "placed exactly as exported, only scaled.", style="BannerSub.TLabel").pack(side="left", padx=14)

            outer = ttk.Frame(self, style="Page.TFrame")
            outer.pack(fill="both", expand=True)
            canvas = tk.Canvas(outer, background=PAGE, highlightthickness=0)
            sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            body = ttk.Frame(canvas, style="Page.TFrame", padding=(12, 10))
            win = canvas.create_window((0, 0), window=body, anchor="nw")
            body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

            def wheel(e):
                if isinstance(e.widget, tk.Text):
                    return
                canvas.yview_scroll(int(-e.delta / 120) if e.delta else 0, "units")
            self.bind_all("<MouseWheel>", wheel)
            self.canvas = canvas

            # 1 files
            c, b = card(body, "1", "H2X export")
            c.pack(fill="x", pady=(0, 8))
            b.columnconfigure(1, weight=1)
            ttk.Label(b, text="H2X PDF", style="Field.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(b, textvariable=self.h2x_text).grid(row=0, column=1, sticky="w", padx=8)
            ttk.Button(b, text="Choose H2X PDF…", command=self.pick_h2x).grid(row=0, column=2, sticky="e")
            ttk.Label(b, text="Template", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
            ttk.Label(b, textvariable=self.tpl_text, style="Hint.TLabel").grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
            tb = ttk.Frame(b)
            tb.grid(row=1, column=2, sticky="e", pady=(6, 0))
            ttk.Button(tb, text="Built-in", style="Small.TButton", command=self.reset_template).pack(side="right", padx=(4, 0))
            ttk.Button(tb, text="Other template…", style="Small.TButton", command=self.pick_template).pack(side="right")

            # 2 sheets
            c, b = card(body, "2", "Sheets", "one Synergi sheet per H2X page")
            c.pack(fill="x", pady=(0, 8))
            self.sheet_frame = b
            opts = ttk.Frame(b)
            opts.pack(fill="x")
            ttk.Label(opts, text="Show", style="Field.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Combobox(opts, textvariable=self.crop, values=[m[1] for m in CROP_MODES], state="readonly",
                         width=44).grid(row=0, column=1, sticky="w", padx=6)
            ttk.Label(opts, text="Scale", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
            ttk.Combobox(opts, textvariable=self.scale_mode, values=[m[1] for m in SCALE_MODES], state="readonly",
                         width=28).grid(row=0, column=3, sticky="w", padx=6)
            ttk.Checkbutton(opts, text="Same scale and position on every sheet (plans line up between levels)",
                            variable=self.same_layout).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
            ttk.Checkbutton(opts, text="Number documents up from row 1", variable=self.auto_number,
                            command=self.renumber).grid(row=2, column=0, columnspan=4, sticky="w")
            self.table = ttk.Frame(b)
            self.table.pack(fill="x", pady=(8, 0))
            self._table_head()

            # 3 title block
            c, b = card(body, "3", "Title block", "the same on every sheet")
            c.pack(fill="x", pady=(0, 8))
            for col in (1, 3):
                b.columnconfigure(col, weight=1)
            ttk.Label(b, text="Project details", style="Field.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Entry(b, textvariable=self.vars["project"]).grid(row=0, column=1, sticky="ew", padx=6, pady=2)
            ttk.Label(b, text="Drawing status", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
            ttk.Combobox(b, textvariable=self.vars["status"], values=STATUSES).grid(row=0, column=3, sticky="ew", padx=6)
            ttk.Label(b, text="Client\n(name, address)", style="Field.TLabel").grid(row=1, column=0, sticky="nw", pady=(4, 0))
            self.client = tk.Text(b, height=4, width=40, font=f(9), relief="solid", borderwidth=1,
                                  highlightthickness=0, wrap="word")
            self.client.grid(row=1, column=1, rowspan=3, sticky="nsew", padx=6, pady=(4, 2))
            self.client.bind("<<Modified>>", self._text_modified)
            ttk.Label(b, text="Drawn", style="Field.TLabel").grid(row=1, column=2, sticky="w", padx=(12, 0))
            ttk.Entry(b, textvariable=self.vars["drawn"]).grid(row=1, column=3, sticky="ew", padx=6, pady=2)
            ttk.Label(b, text="Date", style="Field.TLabel").grid(row=2, column=2, sticky="w", padx=(12, 0))
            df = ttk.Frame(b)
            df.grid(row=2, column=3, sticky="ew", padx=6, pady=2)
            ttk.Entry(df, textvariable=self.vars["date"], width=14).pack(side="left")
            ttk.Button(df, text="Today", style="Small.TButton",
                       command=lambda: self.vars["date"].set(today())).pack(side="left", padx=6)
            ttk.Label(df, text="YYYY-MM-DD", style="Hint.TLabel").pack(side="left")
            ttk.Label(b, text="Revision", style="Field.TLabel").grid(row=3, column=2, sticky="w", padx=(12, 0))
            self.rev_label = ttk.Label(b, text="", style="Hint.TLabel")
            self.rev_label.grid(row=3, column=3, sticky="w", padx=6)

            # 4 revisions & notes
            c, b = card(body, "4", "Revision table & notes",
                        "row 1 is the bottom row of the table (first issue); the newest filled row is the sheet revision")
            c.pack(fill="x", pady=(0, 8))
            rt = ttk.Frame(b)
            rt.pack(side="left", anchor="n")
            heads = ["Row", "Rev", "Date", "Description", "DRN", "CHK"]
            widths = [4, 6, 12, 34, 6, 6]
            for i, h in enumerate(heads):
                ttk.Label(rt, text=h, style="Head.TLabel").grid(row=0, column=i, sticky="ew", padx=1)
            self.rev_vars = []
            for r in range(self.rev_count):
                row = {}
                ttk.Label(rt, text=str(r + 1), style="Hint.TLabel").grid(row=r + 1, column=0)
                for i, k in enumerate(REV_KEYS):
                    v = tk.StringVar()
                    v.trace_add("write", lambda *_: self.changed())
                    ttk.Entry(rt, textvariable=v, width=widths[i + 1]).grid(row=r + 1, column=i + 1, sticky="ew", padx=1, pady=1)
                    row[k] = v
                self.rev_vars.append(row)
            nf = ttk.Frame(b)
            nf.pack(side="left", fill="both", expand=True, padx=(16, 0))
            ttk.Label(nf, text="Notes / comments (optional)", style="Field.TLabel").pack(anchor="w")
            self.notes = tk.Text(nf, height=7, width=40, font=f(9), relief="solid", borderwidth=1,
                                 highlightthickness=0, wrap="word")
            self.notes.pack(fill="both", expand=True, pady=(4, 0))
            self.notes.bind("<<Modified>>", self._text_modified)

            # 5 output
            c, b = card(body, "5", "Save")
            c.pack(fill="x", pady=(0, 8))
            b.columnconfigure(1, weight=1)
            ttk.Label(b, text="Output folder", style="Field.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Entry(b, textvariable=self.out_dir).grid(row=0, column=1, sticky="ew", padx=6)
            ttk.Button(b, text="Choose…", command=self.pick_output).grid(row=0, column=2)
            ttk.Checkbutton(b, text="Also save all sheets together in one PDF", variable=self.combined).grid(
                row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
            self.check_label = ttk.Label(b, textvariable=self.check_text, style="Hint.TLabel", wraplength=900,
                                         justify="left")
            self.check_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

            foot = ttk.Frame(self, padding=(14, 8))
            foot.pack(fill="x", side="bottom")
            ttk.Label(foot, textvariable=self.status_text, style="Hint.TLabel").pack(side="left")
            self.gen_btn = ttk.Button(foot, text="Generate drawings", style="Accent.TButton", command=self.generate)
            self.gen_btn.pack(side="right")
            self.prev_btn = ttk.Button(foot, text="Preview first sheet", command=lambda: self.preview(None))
            self.prev_btn.pack(side="right", padx=8)

        def _table_head(self):
            for w in self.table.winfo_children():
                w.destroy()
            for i, h in enumerate(["Use", "H2X page", "Drawing title", "Document number", "Scale", ""]):
                ttk.Label(self.table, text=h, style="Head.TLabel").grid(row=0, column=i, sticky="ew", padx=1)
            self.table.columnconfigure(2, weight=1)
            self.table.columnconfigure(3, weight=1)

        # ------------------------------------------------------------ worker plumbing
        def _submit(self, fn, *args, done=None):
            fut = self.pool.submit(fn, *args)
            self._pending.append((fut, done))
            return fut

        def _drain(self):
            keep = []
            for fut, done in self._pending:
                if fut.done():
                    try:
                        res, err = fut.result(), None
                    except Exception as exc:  # noqa: BLE001
                        res, err = None, exc
                    if done:
                        try:
                            done(res, err)
                        except Exception:  # noqa: BLE001
                            traceback.print_exc()
                else:
                    keep.append((fut, done))
            self._pending = keep
            self.after(80, self._drain)

        def destroy(self):
            self._remember()
            try:
                self.pool.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass
            super().destroy()

        def _remember(self):
            d = dict(self.settings)
            d.update(drawn=self.vars["drawn"].get(), status=self.vars["status"].get(),
                     output_dir=self.out_dir.get(), combined=bool(self.combined.get()))
            if self.template_path != str(default_template()):
                d["template"] = self.template_path
            else:
                d.pop("template", None)
            chk = [r["chk"].get() for r in self.rev_vars if r["chk"].get()]
            if chk:
                d["chk"] = chk[-1]
            save_settings(d)

        def _set_busy(self, busy, text=""):
            self.busy = busy
            state = "disabled" if busy else "normal"
            self.gen_btn.configure(state=state)
            self.prev_btn.configure(state=state)
            if text:
                self.status_text.set(text)
            self.configure(cursor="watch" if busy else "")

        # ------------------------------------------------------------ files
        def _update_template_label(self):
            if self.template_path == str(default_template()):
                self.tpl_text.set("Synergi A3 drawing template (built in)")
            else:
                self.tpl_text.set(self.template_path)

        def pick_template(self):
            p = filedialog.askopenfilename(title="Choose the Synergi drawing template PDF",
                                           filetypes=[("PDF", "*.pdf")])
            if p:
                self.template_path = p
                self._update_template_label()
                self._load_template_defaults(reload_h2x=True)

        def reset_template(self):
            self.template_path = str(default_template())
            self._update_template_label()
            self._load_template_defaults(reload_h2x=True)

        def _load_template_defaults(self, reload_h2x=False):
            def done(res, err):
                if err:
                    messagebox.showerror(APP_TITLE, f"This template cannot be used:\n\n{err}")
                    self.reset_template() if self.template_path != str(default_template()) else None
                    return
                self.tdefaults = res
                d = res["defaults"]
                s = self.settings
                self.vars["status"].set(s.get("status") or d.get("status", STATUSES[3]))
                self.vars["drawn"].set(s.get("drawn") or d.get("drawn", ""))
                self.vars["date"].set(today())
                if not self.vars["project"].get():
                    self.vars["project"].set(d.get("project", ""))
                rows = res["rev_rows"]
                first = rows[0] if rows else {}
                for r, row in enumerate(self.rev_vars):
                    for k in REV_KEYS:
                        row[k].set("")
                if first:
                    self.rev_vars[0]["rev"].set(first.get("rev") or "P01")
                    self.rev_vars[0]["date"].set(today())
                    self.rev_vars[0]["description"].set(first.get("description") or "Initial Issue")
                    self.rev_vars[0]["drn"].set(self._initials(self.vars["drawn"].get()) or first.get("drn", ""))
                    self.rev_vars[0]["chk"].set(s.get("chk") or first.get("chk", ""))
                if reload_h2x and self.h2x_path:
                    self.load_h2x(self.h2x_path)
                self.changed()
            self._submit(worker.template_info, self.template_path, done=done)

        @staticmethod
        def _initials(name):
            parts = [p for p in name.replace("-", " ").split() if p[:1].isalpha()]
            return "".join(p[0].upper() for p in parts)[:4]

        def pick_h2x(self):
            p = filedialog.askopenfilename(title="Choose the H2X PDF export", filetypes=[("PDF", "*.pdf")])
            if p:
                self.load_h2x(p)

        def load_h2x(self, p, then=None):
            self._set_busy(True, "Reading the H2X PDF…")

            def done(res, err):
                self._set_busy(False, "")
                if err:
                    self.status_text.set("Could not read that H2X PDF.")
                    messagebox.showerror(APP_TITLE, f"Could not read the H2X PDF:\n\n{err}")
                    return
                self.h2x_path = p
                sh = res["sheets"]
                self.h2x_text.set(Path(p).name)
                sc = sh[0]["scale"] if sh else None
                levels = ", ".join(s["level"] or f"page {s['index'] + 1}" for s in sh)
                self.status_text.set(f"{len(sh)} sheet{'s' if len(sh) != 1 else ''} found: {levels}"
                                     + (f"  ·  H2X scale 1:{sc}" + (f" @ {sh[0]['paper']}" if sh[0]['paper'] else "") if sc else ""))
                proj = next((s["project"] for s in sh if s["project"]), "")
                cur = self.vars["project"].get()
                if proj and (not cur or cur == self.tdefaults["defaults"].get("project")):
                    self.vars["project"].set(f"SES-P-XXX {self._title(proj)}")
                if not self.out_dir.get():
                    self.out_dir.set(str(Path(p).parent))
                self._table_head()
                self.rows = []
                for r, s in enumerate(sh):
                    row = SheetRow(self, s)
                    row.grid(self.table, r + 1)
                    self.rows.append(row)
                self._setting_doc = True
                if self.rows:
                    self.rows[0].doc.set(self.tdefaults["defaults"].get("document_number", ""))
                self._setting_doc = False
                self.renumber()
                self.changed()
                if then:
                    then()
            self._submit(worker.analyse, p, self.template_path, done=done)

        @staticmethod
        def _title(s):
            return " ".join(w if any(c.isdigit() for c in w) else w.capitalize() for w in s.split())

        def pick_output(self):
            p = filedialog.askdirectory(title="Choose the output folder", initialdir=self.out_dir.get() or None)
            if p:
                self.out_dir.set(p)

        # ------------------------------------------------------------ values
        def renumber(self):
            if not self.auto_number.get() or not self.rows:
                return
            first = self.rows[0].doc.get()
            self._setting_doc = True
            k = 0
            for row in self.rows:
                if not row.use.get():
                    continue
                if row is not self.rows[0]:
                    row.doc.set(bump_number(first, k))
                k += 1
            self._setting_doc = False

        def _text_modified(self, e):
            e.widget.edit_modified(False)
            self.changed()

        def revisions(self):
            return [{k: v.get().strip() for k, v in row.items()} for row in self.rev_vars]

        def latest_rev(self):
            filled = [r for r in self.revisions() if r.get("rev")]
            return filled[-1]["rev"] if filled else ""

        def job(self) -> Job:
            crop = next(k for k, t in CROP_MODES if t == self.crop.get())
            scale = next(k for k, t in SCALE_MODES if t == self.scale_mode.get())
            rows = [r for r in self.rows if r.use.get()]
            sheets = [SheetSpec(r.index, r.title.get().strip(), r.doc.get().strip()) for r in rows]
            shared = {k: v.get().strip() for k, v in self.vars.items()}
            shared["client"] = self.client.get("1.0", "end").strip()
            shared["rev"] = self.latest_rev()
            return Job(self.template_path, self.h2x_path, sheets, shared, self.revisions(),
                       self.notes.get("1.0", "end").strip(), crop, scale, bool(self.same_layout.get()),
                       self.out_dir.get().strip(), bool(self.combined.get()))

        def changed(self):
            self.rev_label.configure(text=(self.latest_rev() or "—") + "   (from the revision table)")
            if self._check_after:
                self.after_cancel(self._check_after)
            self._check_after = self.after(500, self._run_check)

        def _run_check(self):
            self._check_after = None
            if not self.h2x_path or not any(r.use.get() for r in self.rows):
                return
            self._check_id += 1
            cid = self._check_id

            def done(res, err):
                if cid != self._check_id:
                    return
                if err:
                    self.check_text.set(str(err))
                    self.check_label.configure(style="Error.TLabel")
                    return
                for row in self.rows:
                    row.scale.set(res["scales"].get(row.index, ""))
                if res["errors"]:
                    self.check_text.set("Fix before generating:\n" + "\n".join(f"• {m}" for m in res["errors"].values()))
                    self.check_label.configure(style="Error.TLabel")
                else:
                    self.check_text.set("✓ Everything fits the title block.")
                    self.check_label.configure(style="Ok.TLabel")
            self._submit(worker.check, self.job(), done=done)

        # ------------------------------------------------------------ actions
        def _ready(self, need_output=True):
            if not self.h2x_path:
                messagebox.showinfo(APP_TITLE, "Choose an H2X PDF first.")
                return False
            if not any(r.use.get() for r in self.rows):
                messagebox.showinfo(APP_TITLE, "Tick at least one sheet.")
                return False
            if need_output and not self.out_dir.get().strip():
                messagebox.showinfo(APP_TITLE, "Choose an output folder.")
                return False
            return True

        def preview(self, index):
            if self.busy or not self._ready(False):
                return
            if index is None:
                index = next(r.index for r in self.rows if r.use.get())
            self._set_busy(True, f"Building a preview of sheet {index + 1}…")

            def done(res, err):
                self._set_busy(False, "")
                if err or res.get("error"):
                    self.status_text.set("Preview failed.")
                    messagebox.showerror(APP_TITLE, str(err or res["error"]))
                    return
                self.status_text.set(f"Preview of sheet {index + 1} ready.")
                self._show_preview(res, index)
            job = self.job()
            if index not in [s.index for s in job.sheets]:
                row = next(r for r in self.rows if r.index == index)
                job.sheets.append(SheetSpec(index, row.title.get().strip(), row.doc.get().strip()))
            self._submit(worker.preview, job, index, done=done)

        def _show_preview(self, res, index):
            from PIL import Image, ImageTk
            top = tk.Toplevel(self)
            top.title(f"Preview - sheet {index + 1}")
            top.configure(background=PAGE)
            im = Image.open(res["png"])
            sw, sh = self.winfo_screenwidth() * 0.9, self.winfo_screenheight() * 0.85
            k = min(1.0, sw / im.width, sh / im.height)
            if k < 1:
                im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im)
            lab = tk.Label(top, image=ph, background=PAGE)
            lab.image = ph
            lab.pack(padx=8, pady=8)
            bar = ttk.Frame(top, style="Page.TFrame")
            bar.pack(fill="x", padx=8, pady=(0, 8))
            ttk.Button(bar, text="Open the preview PDF (zoom in to check)",
                       command=lambda: open_path(res["pdf"])).pack(side="left")
            ttk.Button(bar, text="Close", command=top.destroy).pack(side="right")

        def generate(self):
            if self.busy or not self._ready():
                return
            job = self.job()
            from h2x_template.compose import filename
            out = Path(job.output_dir)
            clash = [filename(s, job.shared.get("rev", "")) for s in job.sheets
                     if (out / filename(s, job.shared.get("rev", ""))).exists()]
            if clash and not messagebox.askyesno(APP_TITLE, "These files already exist and will be replaced:\n\n"
                                                 + "\n".join(clash[:10]) + "\n\nContinue?"):
                return
            self._remember()
            self._set_busy(True, f"Generating {len(job.sheets)} sheet(s) and checking each one…")

            def done(res, err):
                self._set_busy(False, "")
                if err or res.get("error"):
                    self.status_text.set("Nothing was generated.")
                    messagebox.showerror(APP_TITLE, str(err or res["error"]))
                    return
                ok = [r for r in res["results"] if r["ok"]]
                bad = [r for r in res["results"] if not r["ok"]]
                msg = f"Saved {len(ok)} file(s) to\n{job.output_dir}\n\n" + "\n".join(
                    "✓ " + Path(r["path"]).name for r in ok)
                if bad:
                    msg += "\n\nFailed:\n" + "\n\n".join(f"✗ {r['title']}: {r['error']}" for r in bad)
                notes = [n for r in ok for n in r.get("notes", [])]
                if notes:
                    msg += "\n\nPlease check: " + "; ".join(sorted(set(notes)))
                self.status_text.set(f"Saved {len(ok)} file(s)." + (f" {len(bad)} failed." if bad else ""))
                if bad:
                    messagebox.showwarning(APP_TITLE, msg)
                else:
                    if messagebox.askyesno(APP_TITLE, msg + "\n\nOpen the folder?"):
                        open_path(job.output_dir)
            self._submit(worker.generate, job, done=done)

    app = App()
    if preload:
        app.after(400, lambda: app.load_h2x(preload[0]))
    if _mainloop:
        app.mainloop()
    return app


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--self-test"]:
        return self_test(argv[1:])
    if argv[:1] == ["--preload"]:
        run_gui(argv[1:])
        return 0
    run_gui()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
