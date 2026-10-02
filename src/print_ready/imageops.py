"""Raster helpers: loading artwork, fitting to size, generating bleed, colour conversion."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000  # large-format artwork is legitimately huge

RASTER_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
PDF_EXT = {".pdf"}
RENDER_EXT = {".svg", ".html", ".htm"}


class ArtworkError(ValueError):
    pass


def load_image(path: str | Path) -> Image.Image:
    """Open a raster image with EXIF orientation applied."""
    p = Path(path)
    if not p.exists():
        raise ArtworkError(f"File not found: {p}")
    try:
        im = Image.open(p)
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ArtworkError(f"Cannot read image {p.name}: {e}") from e
    im = ImageOps.exif_transpose(im)
    return _to_8bit(im)


def _to_8bit(im: Image.Image) -> Image.Image:
    """16-bit / 32-bit greyscale (I;16, I, F) → 8-bit L without clipping everything to white."""
    if im.mode not in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        return im
    import numpy as np

    a = np.asarray(im, dtype=np.float64)
    if im.mode == "F" and a.max(initial=0) <= 1.0:
        a = a * 255.0
    elif a.max(initial=0) > 255:
        a = a / 257.0
    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8), mode="L")


def rasterize_pdf(path: str | Path, dpi: int = 300, page_index: int = 0,
                  crop_pt: tuple[float, float, float, float] | None = None) -> Image.Image:
    """Render one PDF page. ``crop_pt`` = (left, bottom, right, top) margins in points to cut
    off the MediaBox (in the page's displayed orientation), e.g. to drop an old slug/crop marks."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    if page_index >= len(pdf) or page_index < 0:
        n = len(pdf)
        pdf.close()
        raise ArtworkError(f"{Path(path).name} has {n} page(s); page {page_index + 1} doesn't exist.")
    page = pdf[page_index]
    bitmap = page.render(scale=dpi / 72, may_draw_forms=True, crop=tuple(max(0.0, c) for c in crop_pt) if crop_pt else (0, 0, 0, 0))
    im = bitmap.to_pil().convert("RGB")
    pdf.close()
    return im


def pdf_page_count(path: str | Path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    n = len(pdf)
    pdf.close()
    return n


def has_alpha(im: Image.Image) -> bool:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        alpha = im.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255
    return False


def flatten(im: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Return an RGB image, compositing any transparency onto ``background``."""
    if im.mode == "CMYK":
        return im
    if has_alpha(im):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, background + (255,))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return im.convert("RGB")


def cover_fit(im: Image.Image, target_ratio: float) -> tuple[Image.Image, float]:
    """Centre-crop ``im`` to ``target_ratio`` (w/h). Returns (image, fraction cropped)."""
    w, h = im.size
    ratio = w / h
    if abs(ratio - target_ratio) / target_ratio < 0.002:
        return im, 0.0
    if ratio > target_ratio:  # too wide
        nw = round(h * target_ratio)
        left = (w - nw) // 2
        return im.crop((left, 0, left + nw, h)), 1 - nw / w
    nh = round(w / target_ratio)
    top = (h - nh) // 2
    return im.crop((0, top, w, top + nh)), 1 - nh / h


def contain_fit(im: Image.Image, target_ratio: float, background=(255, 255, 255)) -> Image.Image:
    """Pad ``im`` with ``background`` to reach ``target_ratio`` without cropping."""
    w, h = im.size
    ratio = w / h
    if abs(ratio - target_ratio) / target_ratio < 0.002:
        return im
    fill = {
        "RGB": background,
        "RGBA": (0, 0, 0, 0),
        "CMYK": (0, 0, 0, 0),  # no ink = paper white
        "L": 255,
        "LA": (255, 0),
    }.get(im.mode)
    if fill is None:
        im, fill = im.convert("RGB"), background
    if ratio > target_ratio:
        nh = round(w / target_ratio)
        canvas = Image.new(im.mode, (w, nh), fill)
        canvas.paste(im, (0, (nh - h) // 2))
    else:
        nw = round(h * target_ratio)
        canvas = Image.new(im.mode, (nw, h), fill)
        canvas.paste(im, ((nw - w) // 2, 0))
    return canvas


def add_bleed(im: Image.Image, bleed_px_x: int, bleed_px_y: int, mode: str = "mirror") -> Image.Image:
    """Grow the canvas by bleed on every side.

    mirror  – reflect the outer strip (invisible after trimming; best for photos/patterns)
    extend  – repeat the edge pixels outward (best for flat colour backgrounds)
    """
    if bleed_px_x <= 0 and bleed_px_y <= 0:
        return im
    w, h = im.size
    bx, by = max(0, bleed_px_x), max(0, bleed_px_y)
    bx, by = min(bx, w - 1), min(by, h - 1)
    out = Image.new(im.mode, (w + 2 * bx, h + 2 * by))
    out.paste(im, (bx, by))
    if mode == "extend":
        left = im.crop((0, 0, 1, h)).resize((bx, h)) if bx else None
        right = im.crop((w - 1, 0, w, h)).resize((bx, h)) if bx else None
    else:  # mirror
        left = ImageOps.mirror(im.crop((0, 0, bx, h))) if bx else None
        right = ImageOps.mirror(im.crop((w - bx, 0, w, h))) if bx else None
    if bx:
        out.paste(left, (0, by))
        out.paste(right, (bx + w, by))
    # top/bottom from the already widened rows (fills the corners too)
    full_w = w + 2 * bx
    if by:
        band_top = out.crop((0, by, full_w, by + (1 if mode == "extend" else by)))
        band_bot = out.crop((0, by + h - (1 if mode == "extend" else by), full_w, by + h))
        if mode == "extend":
            top, bot = band_top.resize((full_w, by)), band_bot.resize((full_w, by))
        else:
            top, bot = ImageOps.flip(band_top), ImageOps.flip(band_bot)
        out.paste(top, (0, 0))
        out.paste(bot, (0, by + h))
    return out


# ---------------------------------------------------------------- colour

def rgb_to_cmyk(im: Image.Image, icc_path: str | None = None, intent: str = "perceptual") -> tuple[Image.Image, str]:
    """Convert to CMYK. With an ICC output profile (e.g. ISO Coated v2 / FOGRA39, GRACoL)
    this is a proper colour-managed conversion. Without one we use a neutral GCR formula
    (black comes from the K channel) which is safe but less accurate than a press profile.
    Returns (image, method_description).
    """
    rgb = flatten(im)
    if rgb.mode == "CMYK":
        return rgb, "already CMYK"
    if icc_path:
        p = Path(icc_path)
        if not p.exists():
            raise ArtworkError(f"ICC profile not found: {icc_path}")
        intents = {
            "perceptual": ImageCms.Intent.PERCEPTUAL,
            "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
            "saturation": ImageCms.Intent.SATURATION,
        }
        src = ImageCms.createProfile("sRGB")
        dst = ImageCms.getOpenProfile(str(p))
        if dst.profile.xcolor_space.strip() != "CMYK":
            raise ArtworkError("The ICC profile is not a CMYK output profile.")
        out = ImageCms.profileToProfile(rgb, src, dst, renderingIntent=intents.get(intent, ImageCms.Intent.PERCEPTUAL), outputMode="CMYK")
        return out, f"ICC ({p.name}, {intent})"
    return _gcr_cmyk(rgb), "built-in GCR conversion (no ICC profile)"


def _gcr_cmyk(rgb: Image.Image) -> Image.Image:
    """Full grey-component replacement: K = 1 - max(R,G,B)."""
    import numpy as np

    a = np.asarray(rgb, dtype=np.float32) / 255.0
    k = 1.0 - a.max(axis=2)
    denom = np.where(k < 1.0, 1.0 - k, 1.0)
    c = (1.0 - a[..., 0] - k) / denom
    m = (1.0 - a[..., 1] - k) / denom
    y = (1.0 - a[..., 2] - k) / denom
    cmyk = np.stack([c, m, y, k], axis=2)
    cmyk = np.clip(np.rint(cmyk * 255), 0, 255).astype(np.uint8)
    return Image.fromarray(cmyk, mode="CMYK")


def total_area_coverage(cmyk: Image.Image, percentile: float = 99.5) -> float:
    """Ink coverage (C+M+Y+K, in %) at the given percentile of pixels."""
    import numpy as np

    a = np.asarray(cmyk.convert("CMYK"), dtype=np.float32).sum(axis=2) / 255.0 * 100
    return float(np.percentile(a, percentile))


def vivid_fraction(rgb: Image.Image) -> float:
    """Share of pixels that are very saturated & bright (neon RGB that CMYK can't reproduce)."""
    import numpy as np

    small = flatten(rgb).copy()
    small.thumbnail((600, 600))
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    s, v = hsv[..., 1], hsv[..., 2]
    h = hsv[..., 0] * 360
    # Reds, oranges and yellows reproduce well in CMYK. Saturated greens, cyans, blues,
    # violets and hot pinks are the colours that visibly dull.
    greens_cyans = (h >= 75) & (h < 200) & (s > 0.7) & (v > 0.75)
    blues = (h >= 200) & (h < 255) & (s > 0.65) & (v > 0.7)
    violets_pinks = (h >= 255) & (h < 335) & (s > 0.6) & (v > 0.7)
    risky = greens_cyans | blues | violets_pinks
    return float(risky.mean())


def edge_detail_in_margin(rgb: Image.Image, margin_px: int) -> float:
    """Heuristic for "content too close to the edge".

    Splits the outer ``margin_px`` band into short segments and returns the highest
    density of sharp detail (text strokes, thin lines, logo edges) found in any one
    segment. Clean margins and smooth gradients score ~0; text running into the margin
    scores high. Busy full-bleed photos can also score, so callers should phrase the
    result as "check the proof", not as a hard error.
    """
    import numpy as np
    from PIL import ImageFilter

    if margin_px < 2:
        return 0.0
    g = flatten(rgb).convert("L")
    scale = 1.0
    if max(g.size) > 1600:
        scale = 1600 / max(g.size)
        g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))))
    m = max(2, int(margin_px * scale))
    edges = np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    edges[[0, -1], :] = 0
    edges[:, [0, -1]] = 0
    strong = edges > 60
    h, w = strong.shape
    if h <= 3 * m or w <= 3 * m:
        return 0.0
    seg = max(8, 4 * m)
    best = 0.0
    strips = [strong[:m, :], strong[-m:, :], strong[:, :m].T, strong[:, -m:].T]
    for strip in strips:
        for x in range(0, strip.shape[1] - seg // 2, seg // 2):
            best = max(best, float(strip[:, x:x + seg].mean()))
    return best


def is_uniform_border(rgb: Image.Image, px: int = 4, tolerance: float = 10.0) -> bool:
    """True if the outer ``px`` pixels are (nearly) one flat colour, e.g. a plain background."""
    import numpy as np

    a = np.asarray(flatten(rgb), dtype=np.float32)
    if a.ndim == 2:
        a = a[..., None]
    ch = a.shape[2]
    px = max(1, min(px, a.shape[0] // 4, a.shape[1] // 4))
    border = np.concatenate([
        a[:px].reshape(-1, ch), a[-px:].reshape(-1, ch),
        a[:, :px].reshape(-1, ch), a[:, -px:].reshape(-1, ch),
    ])
    return float(border.std(axis=0).max()) < tolerance
