"""Generate realistic sample artwork for tests and the README demo."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "Arial Bold.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def ai_business_card(path: Path, size=(1024, 585)) -> Path:
    """Looks like a typical AI-generated card: right proportions-ish, no bleed, neon colours, text near the edge."""
    w, h = size
    im = Image.new("RGB", size, (8, 12, 40))
    d = ImageDraw.Draw(im)
    for i in range(h):  # neon gradient band
        t = i / h
        d.line([(0, i), (w, i)], fill=(int(10 + 20 * t), int(20 + 40 * t), int(60 + 120 * t)))
    d.ellipse((w * 0.62, -h * 0.3, w * 1.25, h * 0.75), fill=(0, 200, 255))
    d.ellipse((w * 0.70, h * 0.35, w * 1.1, h * 1.2), fill=(255, 0, 200))
    d.text((60, 150), "Mahmood", font=_font(int(h * 0.14)), fill=(255, 255, 255))
    d.text((60, 250), "Founder · iClick Prints", font=_font(int(h * 0.06)), fill=(0, 255, 180))
    d.text((60, 440), "+91 70063 00665", font=_font(int(h * 0.05)), fill=(230, 230, 230))
    d.text((w - 330, h - 34), "www.yoursite.com", font=_font(int(h * 0.05)), fill=(255, 255, 255))  # too close to edge
    im.save(path)
    return path


def low_res_flyer(path: Path) -> Path:
    im = Image.new("RGB", (500, 707), (250, 245, 235))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 500, 260), fill=(220, 60, 40))
    d.text((40, 300), "GRAND OPENING", font=_font(44), fill=(30, 30, 30))
    d.text((40, 380), "Saturday 10am", font=_font(28), fill=(60, 60, 60))
    im.save(path, quality=88)
    return path


def sticker_with_alpha(path: Path) -> Path:
    im = Image.new("RGBA", (700, 700), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((40, 40, 660, 660), fill=(255, 196, 0, 255))
    d.text((150, 300), "HELLO", font=_font(110), fill=(20, 20, 20, 255))
    im.save(path)
    return path


def pdf_card(path: Path, *, with_bleed: bool, embed_font: bool) -> Path:
    from reportlab.lib.units import inch, mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    b = 3 * mm if with_bleed else 0
    W, H = 3.5 * inch + 2 * b, 2 * inch + 2 * b
    c = canvas.Canvas(str(path), pagesize=(W, H))
    if with_bleed:
        c.setTrimBox((b, b, W - b, H - b))
        c.setBleedBox((0, 0, W, H))
    c.setFillColorCMYK(0.9, 0.2, 0, 0.1)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    font = "Helvetica-Bold"
    if embed_font:
        import PIL

        dejavu = Path(PIL.__file__).parent / "fonts" / "DejaVuSans.ttf"
        candidates = [
            dejavu,
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
        ]
        for f in candidates:
            if f.exists():
                pdfmetrics.registerFont(TTFont("DejaVu", str(f)))
                font = "DejaVu"
                break
    c.setFillColorCMYK(0, 0, 0, 0)
    c.setFont(font, 16)
    c.drawString(b + 6 * mm, b + 30 * mm, "iClick Prints")
    c.setFont(font, 8)
    c.drawString(b + 6 * mm, b + 22 * mm, "Business cards · Flyers · Banners")
    c.showPage()
    c.save()
    return path


def make_all(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "card_png": ai_business_card(folder / "ai-business-card.png"),
        "flyer_jpg": low_res_flyer(folder / "low-res-flyer.jpg"),
        "sticker_png": sticker_with_alpha(folder / "sticker.png"),
        "pdf_nobleed": pdf_card(folder / "card-no-bleed.pdf", with_bleed=False, embed_font=False),
        "pdf_good": pdf_card(folder / "card-good.pdf", with_bleed=True, embed_font=True),
    }


if __name__ == "__main__":
    import sys

    for k, v in make_all(Path(sys.argv[1] if len(sys.argv) > 1 else "samples")).items():
        print(k, v)
