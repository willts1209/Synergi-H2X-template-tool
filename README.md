# Synergi H2X Drawing Tool

Puts an H2X heat loss export into the Synergi A3 drawing template as three sheets:

1. **Plan & Isometric:** the H2X drawing as exported
2. **Plan:** floor plan with room and building heat loss figures
3. **Isometric:** the 3D view

## How to use

1. Choose the H2X PDF.
2. Check the drawing titles. Enter the first document number; the other two count up from it.
3. Fill in the title block (project, client, status, drawn, date), the revision table and notes.
4. Preview any sheet if you want, then **Generate 3 sheets**.

Scale is set automatically: the largest standard scale that fits (e.g. 1:100 @ A1 → 1:100 on A3
for the plan). The isometric is marked NTS.

If the H2X PDF has more than one page, a page picker appears. It defaults to the first page that
has an isometric.

## The H2X drawing is never modified

The H2X page is placed into the template as-is: its content and resources are copied byte for
byte, and it is only scaled uniformly, moved and cropped. Every sheet is checked before it's saved:

- the drawing content and resources are identical to the H2X page
- every character (all heat loss figures) is present at exactly its scaled position
- nothing in the template covers the drawing (pixel comparison against the H2X page on its own)
- the template is unchanged apart from the filled-in values
- every filled-in value reads back correctly

If any check fails, the sheet isn't saved.

The isometric is found from the 3D image H2X embeds; the plan is everything else on the page. If
the two overlap, the tool says so rather than cutting into either.

## Running

- Colleagues: download `SynergiH2XDrawings.exe` from the latest GitHub Actions run (Artifacts).
  No Python needed.
- Developers: `pip install -r requirements.txt`, then `python h2x_app.py`.
- Tests: `python -m pytest -q tests` (uses a synthetic H2X export, so no client drawings in the repo).
