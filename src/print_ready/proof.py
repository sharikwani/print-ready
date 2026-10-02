"""Visual proofs: show exactly what gets trimmed and where the safe zone is."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .preflight import _box
from .specs import PT_PER_MM, ProductSpec

BLEED_TINT = (220, 38, 38, 90)
TRIM_COLOR = (17, 17, 17, 255)
SAFE_COLOR = (14, 165, 233, 255)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _dashed_rect(d: ImageDraw.ImageDraw, box, color, width=2, dash=10):
    x0, y0, x1, y1 = box
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        length = max(abs(bx - ax), abs(by - ay))
        steps = max(1, int(length // dash))
        for i in range(0, steps, 2):
            t0, t1 = i / steps, min(1, (i + 1) / steps)
            d.line((ax + (bx - ax) * t0, ay + (by - ay) * t0, ax + (bx - ax) * t1, ay + (by - ay) * t1), fill=color, width=width)


def make_proof(print_pdf: str | Path, spec: ProductSpec, output: str | Path | None = None, page: int = 0,
               max_px: int = 1400) -> str:
    """Render a page of a print-ready PDF with bleed (red), trim (black) and safe zone (blue dashed)."""
    import pikepdf
    import pypdfium2 as pdfium

    pdf_path = Path(print_pdf)
    with pikepdf.open(pdf_path) as pk:
        pg = pk.pages[page]
        media = _box(pg, "/MediaBox")
        trim = _box(pg, "/TrimBox") or media
        bleed = _box(pg, "/BleedBox") or media
    view = bleed  # show the bleed area, hide the slug/crop marks
    vw, vh = view[2] - view[0], view[3] - view[1]
    scale = max_px / max(vw, vh)

    doc = pdfium.PdfDocument(str(pdf_path))
    full = doc[page].render(scale=scale).to_pil().convert("RGBA")
    doc.close()
    mh = media[3] - media[1]

    def to_px(x, y):
        return (x - media[0]) * scale, (mh - (y - media[1])) * scale

    left, t = to_px(view[0], view[3])
    r, b = to_px(view[2], view[1])
    img = full.crop((round(left), round(t), round(r), round(b)))

    def rel(x, y):
        px, py = to_px(x, y)
        return px - left, py - t

    tx0, ty0 = rel(trim[0], trim[3])
    tx1, ty1 = rel(trim[2], trim[1])
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    W, H = img.size
    for box in ((0, 0, W, ty0), (0, ty1, W, H), (0, ty0, tx0, ty1), (tx1, ty0, W, ty1)):
        if box[2] > box[0] and box[3] > box[1]:
            d.rectangle(box, fill=BLEED_TINT)
    d.rectangle((tx0, ty0, tx1, ty1), outline=TRIM_COLOR, width=2)
    s = spec.safe_mm * PT_PER_MM * scale
    if s > 1:
        _dashed_rect(d, (tx0 + s, ty0 + s, tx1 - s, ty1 - s), SAFE_COLOR, width=2)
    img = Image.alpha_composite(img, overlay)

    # legend strip
    legend_h = 54
    canvas = Image.new("RGB", (W, H + legend_h), (255, 255, 255))
    canvas.paste(img.convert("RGB"), (0, 0))
    ld = ImageDraw.Draw(canvas)
    f = _font(15)
    x = 12
    for color, label in ((BLEED_TINT[:3], "Bleed (trimmed off)"), (TRIM_COLOR[:3], "Cut line"), (SAFE_COLOR[:3], "Safe zone: keep text inside")):
        ld.rectangle((x, H + 18, x + 18, H + 36), fill=color)
        ld.text((x + 26, H + 18), label, fill=(30, 30, 30), font=f)
        x += 26 + int(ld.textlength(label, font=f)) + 24
    out = Path(output) if output else pdf_path.with_name(f"{pdf_path.stem}-proof.png")
    canvas.save(out)
    return str(out)
