"""Make a synthetic H2X-style export (no client data) for tests and CI.

Layout copies a real H2X A1 export: drawing frame on the left, H2X title column on the right with
the level name, project, and '1:100 @ A1'; rooms with heat loss figures and a raster 3D view.
"""
from __future__ import annotations

import io
import sys

from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

W, H = 2383.937008, 1683.779528          # A1 landscape, as exported by H2X
FRAME = (42.52, 42.52, 2031.51, 1641.26)  # x0, top, x1, bottom (top-down like the real file)
COL = (2031.51, 2341.42)


def _iso_png() -> bytes:
    im = Image.new("RGB", (600, 400), "white")
    d = ImageDraw.Draw(im)
    for k in range(6):
        d.line([(50 + k * 80, 300), (150 + k * 80, 120)], fill=(40, 160, 160), width=3)
        d.rectangle([60 + k * 80, 200, 90 + k * 80, 260], fill=(80, 150, 60))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make(path, levels=("GROUND", "LEVEL 1")):
    c = canvas.Canvas(str(path), pagesize=(W, H))
    iso = ImageReader(io.BytesIO(_iso_png()))
    for n, level in enumerate(levels):
        x0, top, x1, bot = FRAME
        c.setLineWidth(1.5)
        c.rect(x0, H - bot, x1 - x0, bot - top)
        c.rect(COL[0], H - bot, COL[1] - COL[0], bot - top)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(2080, H - 90, "Designed using h2x")
        c.setFont("Helvetica", 20)
        c.drawString(2041, H - 850, "CLIENT:")
        c.drawString(2041, H - 1003, "PROJECT:")
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(2186, H - 1060, "SAMPLE STREET")
        c.drawCentredString(2186, H - 1090, "NO. 1")
        c.setFont("Helvetica-Bold", 37)
        c.drawCentredString(2186, H - 1195, level)
        c.setFont("Helvetica", 28)
        c.drawCentredString(2186, H - 1250, "MECHANICAL")
        c.drawCentredString(2186, H - 1283, "SERVICES")
        c.setFont("Helvetica-Bold", 20)
        c.drawString(2038, H - 1315, "PROJECT NO.:")
        c.drawString(2196, H - 1315, "NA")
        c.drawString(2038, H - 1493, "SCALE:")
        c.drawString(2196, H - 1493, "1:100 @ A1")
        # rooms with heat loss figures
        c.setStrokeColorRGB(0.1, 0.8, 0.8)
        c.setLineWidth(6)
        rooms = [(300, 600, 260, 220, "Kitchen"), (560, 600, 300, 220, "Living Room"), (300, 820, 560, 180, "Bedroom 1")]
        for k, (rx, ry, rw, rh, name) in enumerate(rooms):
            c.rect(rx, H - ry - rh, rw, rh)
            c.setFont("Helvetica", 4)
            c.setFillColorRGB(0, 0, 0)
            ty = H - ry - rh / 2
            for j, line in enumerate([f"{name} {21 - k} °C", f"Heat Loss: {1.31 + k + n:.2f} kW ({109.14 + k:.2f} W/m²)",
                                      f"Infiltration: {1.67 + k:.2f} L/s (0.20 ACH)", "Cooling Supplied: 0.00 kW"]):
                c.drawCentredString(rx + rw / 2, ty - j * 5, line)
        c.setStrokeColorRGB(0.9, 0.1, 0.7)
        c.line(560, H - 600, 560, H - 820)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(580, H - 1040, f"Building Heat Loss: {38280.50 + n:.2f} W (114.30 W/m²)")
        c.drawCentredString(580, H - 1052, "Floor Heat Loss: 15239.14 W")
        if n == 0:
            c.drawImage(iso, 1000, H - 1040, 700, 470)
        c.showPage()
    c.save()


if __name__ == "__main__":
    make(sys.argv[1])
