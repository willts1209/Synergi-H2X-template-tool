# Synergi H2X Drawing Tool

Puts H2X heat loss exports (floor plans, isometrics, room heat loss figures) into the Synergi A3
drawing template and fills in the title block.

## How it works

1. Open the H2X PDF. Each page becomes one sheet. The level name (Ground, Level 1…) and scale
   are read from H2X's own title column.
2. Fill in the shared details once (project, client, status, drawn by, date, revision rows,
   notes). Drawing titles and document numbers are per sheet; numbers auto-increment.
3. Preview, then Generate. You get one PDF per sheet, plus a combined PDF if you tick that option.

## The H2X drawing is never modified

The H2X page is placed into the template as-is: its content and resources are copied byte for
byte, and it is only scaled uniformly and moved. Every sheet is checked before it's saved:

- the drawing content and resources are identical to the H2X page
- every character (all heat loss figures) is present at exactly its scaled position
- nothing in the template covers the drawing (pixel comparison against the H2X page on its own)
- the template is unchanged apart from the filled-in values
- every filled-in value reads back correctly

If any check fails, the sheet isn't saved.

Scale is picked automatically as the largest standard scale that fits (e.g. 1:100 @ A1 → 1:125
on A3), or "Fit to sheet (NTS)".

## Running

- Colleagues: download `SynergiH2XDrawings.exe` from the latest GitHub Actions run (Artifacts).
  No Python needed.
- Developers: `pip install -r requirements.txt`, then `python h2x_app.py`.
- Tests: `python -m pytest -q tests` (uses a synthetic H2X export, so no client drawings in the repo).
