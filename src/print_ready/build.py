"""Build print-ready PDFs: exact size, bleed, CMYK, crop marks, TrimBox/BleedBox."""
from __future__ import annotations

import io as _bio
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import __version__
from . import imageops as io
from .preflight import PT_TO_MM, _box, _font_problems, _size_mm, analyse_geometry, orient_spec
from .specs import MM_PER_INCH, PT_PER_MM, ProductSpec

SLUG_MM = 10.0  # space outside the bleed for crop marks
MARK_LEN_MM = 5.0
MARK_OFFSET_MM = 3.0  # gap between trim corner and start of the mark (outside bleed when bleed=3)


@dataclass
class BuildResult:
    output: str
    pages: int
    product: dict
    page_size_mm: str
    color: str
    crop_marks: bool
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    png_output: str | None = None

    def to_text(self) -> str:
        lines = [f"Print-ready file: {self.output}", f"  {self.pages} page(s), {self.page_size_mm}, colour: {self.color}, crop marks: {'yes' if self.crop_marks else 'no'}"]
        if self.png_output:
            lines.append(f"  Print PNG: {self.png_output}")
        lines += [f"  ✓ {a}" for a in self.actions]
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


def _expand_inputs(paths: list[str | Path]) -> list[tuple[Path, int | None]]:
    """Expand PDFs into (path, page_index) entries; images are (path, None)."""
    out: list[tuple[Path, int | None]] = []
    for p in map(Path, paths):
        if not p.exists():
            raise io.ArtworkError(f"File not found: {p}")
        ext = p.suffix.lower()
        if ext in io.PDF_EXT:
            out += [(p, i) for i in range(io.pdf_page_count(p))]
        elif ext in io.RASTER_EXT | io.RENDER_EXT:
            out.append((p, None))
        else:
            raise io.ArtworkError(f"Unsupported file type '{ext}' ({p.name}).")
    if not out:
        raise io.ArtworkError("No input files.")
    return out


def _pdf_crop_margins(path: Path, page: int, spec: ProductSpec) -> tuple[float, float, float, float] | None:
    """Margins (pt) that cut the MediaBox down to the artwork + bleed, dropping any old slug/crop marks.
    Returned in displayed orientation (pdfium applies crop after /Rotate)."""
    import pikepdf

    with pikepdf.open(path) as pdf:
        if page >= len(pdf.pages):
            return None
        pg = pdf.pages[page]
        media = _box(pg, "/MediaBox")
        crop = _box(pg, "/CropBox")
        trim = _box(pg, "/TrimBox")
        bleed = _box(pg, "/BleedBox")
        rot = int(pg.obj.get("/Rotate", 0)) % 360
    if not media:
        return None
    if crop:  # pdfium renders the visible CropBox (∩ MediaBox), so measure margins from that
        media = [max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]), min(media[3], crop[3])]
    if bleed:
        target = bleed
    elif trim:
        b = spec.bleed_mm / PT_TO_MM
        target = [max(media[0], trim[0] - b), max(media[1], trim[1] - b), min(media[2], trim[2] + b), min(media[3], trim[3] + b)]
    else:
        return None
    left, bottom = target[0] - media[0], target[1] - media[1]
    right, top = media[2] - target[2], media[3] - target[3]
    if rot == 90:
        return (bottom, right, top, left)
    if rot == 180:
        return (right, top, left, bottom)
    if rot == 270:
        return (top, left, bottom, right)
    return (left, bottom, right, top)


def _load_raster(path: Path, page: int | None, spec: ProductSpec) -> Image.Image:
    ext = path.suffix.lower()
    if ext in io.PDF_EXT:
        dpi = max(spec.min_dpi, 300 if spec.width_mm * spec.height_mm < 700 * 1000 else spec.min_dpi)
        return io.rasterize_pdf(path, dpi=dpi, page_index=page or 0, crop_pt=_pdf_crop_margins(path, page or 0, spec))
    if ext in io.RENDER_EXT:
        from .render import render_artwork

        return render_artwork(path, spec)
    return io.load_image(path)


def _pdf_vector_ok(path: Path, spec: ProductSpec) -> tuple[bool, ProductSpec, str]:
    """Can this PDF be passed through as vector (correct size + bleed, fonts embedded, no rotation)?"""
    import pikepdf

    with pikepdf.open(path) as pdf:
        if _font_problems(pdf):
            return False, spec, "fonts not embedded"
        first = pdf.pages[0]
        media = _box(first, "/MediaBox")
        trim = _box(first, "/TrimBox")
        spec2, _ = orient_spec(spec, *_size_mm(trim or media))
        for page in pdf.pages:
            if int(page.obj.get("/Rotate", 0)) % 360:
                return False, spec2, "rotated pages"
            media = _box(page, "/MediaBox")
            trim = _box(page, "/TrimBox")
            if trim:
                tw, th = _size_mm(trim)
                outer = _box(page, "/BleedBox") or media
                avail = min(trim[0] - outer[0], trim[1] - outer[1], outer[2] - trim[2], outer[3] - trim[3]) * PT_TO_MM
            else:
                mw, mh = _size_mm(media)
                tw, th = mw - 2 * spec2.bleed_mm, mh - 2 * spec2.bleed_mm
                avail = spec2.bleed_mm if (abs(mw - spec2.bleed_width_mm) <= 1 and abs(mh - spec2.bleed_height_mm) <= 1) else -1
            if abs(tw - spec2.width_mm) > 1 or abs(th - spec2.height_mm) > 1 or avail < spec2.bleed_mm - 0.3:
                return False, spec2, "size or bleed doesn't match"
    return True, spec2, ""


def _prepare_page_image(im: Image.Image, spec: ProductSpec, fit: str, bleed_mode: str, actions: list[str], warnings: list[str], label: str) -> Image.Image:
    geo = analyse_geometry(*im.size, spec)
    keep_alpha = spec.allows_transparency and io.has_alpha(im)
    work = im.convert("RGBA") if keep_alpha else (im if im.mode == "CMYK" else io.flatten(im))
    if io.has_alpha(im) and not keep_alpha:
        actions.append(f"{label}: flattened transparency onto white")

    if geo["has_bleed"]:
        target = spec.bleed_width_mm / spec.bleed_height_mm
        work, cropped = io.cover_fit(work, target)
        if spec.bleed_mm > 0:
            actions.append(f"{label}: artwork already includes bleed")
        return work

    target = spec.width_mm / spec.height_mm
    if geo["aspect_mismatch"] > 0.002:
        if fit == "contain":
            work = io.contain_fit(work, target)
            actions.append(f"{label}: padded with white to {spec.width_mm:g} × {spec.height_mm:g} mm proportions")
        else:
            work, cropped = io.cover_fit(work, target)
            if cropped > 0.002:
                actions.append(f"{label}: centre-cropped {cropped * 100:.1f}% to fit {spec.width_mm:g} × {spec.height_mm:g} mm")
            if cropped > 0.08:
                warnings.append(f"{label}: {cropped * 100:.0f}% of the artwork was cropped; check the proof (or use fit=contain).")
    if spec.bleed_mm > 0 and keep_alpha:
        bx = round(spec.bleed_mm * work.width / spec.width_mm)
        by = round(spec.bleed_mm * work.height / spec.height_mm)
        padded = Image.new("RGBA", (work.width + 2 * bx, work.height + 2 * by), (0, 0, 0, 0))
        padded.paste(work, (bx, by))
        actions.append(f"{label}: kept transparent background (die-cut / garment); bleed area left transparent")
        return padded
    if spec.bleed_mm > 0:
        px_per_mm_x = work.width / spec.width_mm
        px_per_mm_y = work.height / spec.height_mm
        mode = bleed_mode
        if mode == "auto":
            mode = "extend" if work.mode != "RGBA" and io.is_uniform_border(work) else "mirror"
        work = io.add_bleed(work, round(spec.bleed_mm * px_per_mm_x), round(spec.bleed_mm * px_per_mm_y), mode)
        actions.append(f"{label}: added {spec.bleed_mm:g} mm bleed ({mode})")
    return work


def _limit_resolution(im: Image.Image, spec: ProductSpec, actions: list[str], label: str) -> Image.Image:
    """Downsample absurdly high-res artwork to keep files reasonable (never upsample)."""
    target = max(spec.min_dpi, 300)
    dpi = im.width / (spec.bleed_width_mm / MM_PER_INCH)
    if dpi > target * 1.6:
        scale = (target * 1.25) / dpi
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        actions.append(f"{label}: downsampled from {dpi:.0f} to {target * 1.25:.0f} dpi to keep the file light")
    return im


def _crop_mark_ops(x0: float, y0: float, w: float, h: float, bleed: float) -> str:
    """PDF operators for corner crop marks around the trim rect (all in points)."""
    off = (max(MARK_OFFSET_MM, bleed) + 0.5) * PT_PER_MM  # never touch the bleed area
    ln = MARK_LEN_MM * PT_PER_MM
    x1, y1 = x0 + w, y0 + h
    segs = []
    for x, sx in ((x0, -1), (x1, 1)):
        for y, sy in ((y0, -1), (y1, 1)):
            segs.append((x + sx * off, y, x + sx * (off + ln), y))  # horizontal
            segs.append((x, y + sy * off, x, y + sy * (off + ln)))  # vertical
    body = " ".join(f"{a:.3f} {b:.3f} m {c:.3f} {d:.3f} l S" for a, b, c, d in segs)
    return f"q 0.25 w 1 1 1 1 K [] 0 d {body} Q\n"


def _raster_page(pdf, im: Image.Image, spec: ProductSpec, marks: bool, jpeg_quality: int | None):
    """Append a page holding ``im`` (already trim+bleed sized) to pikepdf ``pdf``."""
    import pikepdf

    slug = SLUG_MM * PT_PER_MM if marks else 0
    bw, bh = spec.bleed_width_mm * PT_PER_MM, spec.bleed_height_mm * PT_PER_MM
    pw, ph = bw + 2 * slug, bh + 2 * slug
    b = spec.bleed_mm * PT_PER_MM

    buf = _bio.BytesIO()
    if im.mode == "CMYK":
        if jpeg_quality:
            im.save(buf, "JPEG", quality=jpeg_quality, subsampling=0)
            # Pillow writes Adobe-style inverted CMYK JPEGs
            img_obj = pikepdf.Stream(pdf, buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                     Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceCMYK,
                                     BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode,
                                     Decode=pikepdf.Array([1, 0, 1, 0, 1, 0, 1, 0]))
        else:
            img_obj = pikepdf.Stream(pdf, zlib.compress(im.tobytes(), 6), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                     Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceCMYK,
                                     BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        smask = None
    else:
        rgb = im.convert("RGB")
        if jpeg_quality:
            rgb.save(buf, "JPEG", quality=jpeg_quality, subsampling=0)
            img_obj = pikepdf.Stream(pdf, buf.getvalue(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                     Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceRGB,
                                     BitsPerComponent=8, Filter=pikepdf.Name.DCTDecode)
        else:
            img_obj = pikepdf.Stream(pdf, zlib.compress(rgb.tobytes(), 6), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                     Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceRGB,
                                     BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode)
        smask = None
        if im.mode == "RGBA":
            alpha = im.getchannel("A")
            smask = pikepdf.Stream(pdf, zlib.compress(alpha.tobytes(), 6), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                                   Width=im.width, Height=im.height, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8,
                                   Filter=pikepdf.Name.FlateDecode)
            img_obj.SMask = smask

    content = f"q {bw:.4f} 0 0 {bh:.4f} {slug:.4f} {slug:.4f} cm /Im0 Do Q\n"
    if marks:
        content += _crop_mark_ops(slug + b, slug + b, spec.width_mm * PT_PER_MM, spec.height_mm * PT_PER_MM, spec.bleed_mm)
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, pw, ph],
        BleedBox=[slug, slug, slug + bw, slug + bh],
        TrimBox=[slug + b, slug + b, slug + bw - b, slug + bh - b],
        Resources=pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im0=img_obj),
        ),
        Contents=pikepdf.Stream(pdf, content.encode("latin-1")),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))


def _vector_page(pdf, src_page, spec: ProductSpec, marks: bool):
    """Place an existing (correct-size) PDF page as a form XObject, preserving vectors."""
    import pikepdf

    slug = SLUG_MM * PT_PER_MM if marks else 0
    bw, bh = spec.bleed_width_mm * PT_PER_MM, spec.bleed_height_mm * PT_PER_MM
    b = spec.bleed_mm * PT_PER_MM
    media = [float(x) for x in src_page.obj.MediaBox]
    trim = _box(src_page, "/TrimBox")
    if trim is None:
        trim = [media[0] + b, media[1] + b, media[2] - b, media[3] - b]
    fx = src_page.as_form_xobject()
    fx.BBox = pikepdf.Array(media)  # qpdf uses the TrimBox by default, which would cut off the bleed
    form = pdf.copy_foreign(fx)
    tx = slug + b - trim[0]
    ty = slug + b - trim[1]
    content = (
        f"q {slug:.4f} {slug:.4f} {bw:.4f} {bh:.4f} re W n "
        f"1 0 0 1 {tx:.4f} {ty:.4f} cm /Fx0 Do Q\n"
    )
    if marks:
        content += _crop_mark_ops(slug + b, slug + b, spec.width_mm * PT_PER_MM, spec.height_mm * PT_PER_MM, spec.bleed_mm)
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, bw + 2 * slug, bh + 2 * slug],
        BleedBox=[slug, slug, slug + bw, slug + bh],
        TrimBox=[slug + b, slug + b, slug + bw - b, slug + bh - b],
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fx0=form)),
        Contents=pikepdf.Stream(pdf, content.encode("latin-1")),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))


def make_print_ready(
    inputs: list[str | Path],
    spec: ProductSpec,
    output: str | Path | None = None,
    *,
    fit: str = "cover",
    bleed_mode: str = "auto",
    color: str = "cmyk",
    icc_path: str | None = None,
    intent: str = "perceptual",
    crop_marks: bool = True,
    keep_vector: bool = True,
    jpeg_quality: int | None = None,
) -> BuildResult:
    """Turn artwork (images, PDFs, SVG/HTML) into a print-ready PDF.

    Each input image becomes one page; each PDF page becomes one page (front/back = 2 inputs).
    """
    import pikepdf

    if fit not in ("cover", "contain"):
        raise ValueError("fit must be 'cover' or 'contain'")
    if bleed_mode not in ("auto", "mirror", "extend"):
        raise ValueError("bleed_mode must be auto, mirror or extend")
    if color not in ("cmyk", "rgb"):
        raise ValueError("color must be 'cmyk' or 'rgb'")

    items = _expand_inputs(inputs)
    first = items[0][0]
    out = Path(output) if output else first.with_name(f"{first.stem}-print-ready.pdf")
    if out.resolve() in {p.resolve() for p, _ in items}:
        raise io.ArtworkError("Output would overwrite an input file; choose another output path.")

    actions: list[str] = []
    warnings: list[str] = []
    pdf = pikepdf.new()
    color_method = "unchanged"
    used_spec: ProductSpec | None = None
    png_out = None

    # Whole-PDF vector passthrough when the PDF is already correct.
    if len(inputs) == 1 and first.suffix.lower() == ".pdf" and keep_vector and not icc_path:
        ok, spec_v, why = _pdf_vector_ok(first, spec)
        if ok:
            used_spec = spec_v
            with pikepdf.open(first) as src:
                for sp in src.pages:
                    _vector_page(pdf, sp, spec_v, crop_marks)
            actions.append("PDF already had the right size and bleed: kept it as vector (sharpest text), added TrimBox/BleedBox"
                           + (" and crop marks" if crop_marks else ""))
            if color == "cmyk":
                warnings.append("Vector pages keep their original colours; RGB elements will be converted by the printer's RIP. "
                                "Use --rasterize to force full CMYK conversion.")
            color_method = "original (vector)"
            items = []

    for n, (path, page_idx) in enumerate(items, 1):
        label = path.name + (f" p{page_idx + 1}" if page_idx is not None else "")
        im = _load_raster(path, page_idx, spec)
        if path.suffix.lower() == ".pdf":
            actions.append(f"{label}: rasterised at print resolution")
        page_spec, rotated = orient_spec(spec, *im.size)
        if used_spec is None:
            used_spec = page_spec
            if rotated:
                actions.append(f"Using {'landscape' if page_spec.width_mm > page_spec.height_mm else 'portrait'} orientation to match the artwork")
        elif (page_spec.width_mm, page_spec.height_mm) != (used_spec.width_mm, used_spec.height_mm):
            # e.g. a portrait back for a landscape front: rotate it to match page 1 rather than cropping
            im = im.rotate(90, expand=True)
            page_spec = used_spec
            actions.append(f"{label}: rotated 90° to match page 1's orientation (check which way is up on the proof)")
        work = _prepare_page_image(im, page_spec, fit, bleed_mode, actions, warnings, label)
        work = _limit_resolution(work, page_spec, actions, label)
        dpi = work.width / (page_spec.bleed_width_mm / MM_PER_INCH)
        if dpi < page_spec.acceptable_dpi:
            warnings.append(f"{label}: only {dpi:.0f} dpi at final size; it will look soft. Use a larger source if possible.")

        if page_spec.allows_transparency and work.mode == "RGBA":
            # garment / die-cut workflows want a transparent PNG, not a flattened PDF
            png_out = str(out.with_name(f"{out.stem}-print.png" if len(items) == 1 else f"{out.stem}-print-p{n}.png"))
            if Path(png_out).resolve() in {p.resolve() for p, _ in items}:
                raise io.ArtworkError("The print PNG would overwrite an input file; choose another output path.")
            work.save(png_out, dpi=(round(dpi), round(dpi)))
            actions.append(f"{label}: saved transparent print PNG at {dpi:.0f} dpi ({Path(png_out).name})")
            final = work
            color_method = "RGB + transparency (PNG for DTG/die-cut)"
        elif color == "cmyk":
            final, color_method = io.rgb_to_cmyk(work, icc_path, intent)
        else:
            final, color_method = io.flatten(work), "sRGB (printer converts)"
        _raster_page(pdf, final, page_spec, crop_marks, jpeg_quality)

    assert used_spec is not None
    if color_method.startswith("built-in"):
        warnings.append("Converted to CMYK without a press ICC profile. For exact colour, ask your printer for their profile "
                        "(often ISO Coated v2 / FOGRA39 or GRACoL) and pass --icc.")
    elif color_method.startswith("ICC"):
        _add_output_intent(pdf, icc_path)
        actions.append("Embedded the ICC profile as the PDF output intent")

    with pdf.open_metadata() as meta:
        meta["dc:title"] = f"{out.stem} — {used_spec.name}"
        meta["xmp:CreatorTool"] = f"print-ready {__version__}"
    pdf.docinfo["/Title"] = f"{out.stem} — {used_spec.name}"
    pdf.docinfo["/Creator"] = f"print-ready {__version__} (https://github.com/sharikwani/print-ready)"
    pdf.docinfo["/Producer"] = "print-ready / pikepdf"
    pdf.docinfo["/Trapped"] = pikepdf.Name.False_
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(out, compress_streams=True)
    pages = len(pdf.pages)
    pdf.close()
    if crop_marks:
        actions.append("Added crop marks and set TrimBox/BleedBox")
    page_w = used_spec.bleed_width_mm + (2 * SLUG_MM if crop_marks else 0)
    page_h = used_spec.bleed_height_mm + (2 * SLUG_MM if crop_marks else 0)
    return BuildResult(
        output=str(out),
        pages=pages,
        product=used_spec.to_dict(),
        page_size_mm=f"page {page_w:.1f} × {page_h:.1f} mm (trim {used_spec.width_mm:g} × {used_spec.height_mm:g} mm + {used_spec.bleed_mm:g} mm bleed)",
        color=color_method,
        crop_marks=crop_marks,
        actions=_dedupe(actions),
        warnings=_dedupe(warnings),
        png_output=png_out,
    )


def _dedupe(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _add_output_intent(pdf, icc_path: str | None) -> None:
    import pikepdf
    from PIL import ImageCms

    if not icc_path:
        return
    data = Path(icc_path).read_bytes()
    try:
        desc = ImageCms.getProfileDescription(ImageCms.getOpenProfile(icc_path)).strip() or Path(icc_path).stem
    except Exception:  # noqa: BLE001
        desc = Path(icc_path).stem
    profile = pikepdf.Stream(pdf, data)
    profile.N = 4
    intent = pikepdf.Dictionary(
        Type=pikepdf.Name.OutputIntent,
        S=pikepdf.Name("/GTS_PDFX"),
        OutputConditionIdentifier=pikepdf.String(desc),
        Info=pikepdf.String(desc),
        DestOutputProfile=profile,
    )
    pdf.Root.OutputIntents = pikepdf.Array([intent])
