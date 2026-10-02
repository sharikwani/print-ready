"""Preflight: find everything that would make a print shop reject (or misprint) a file."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from . import imageops as io
from .specs import MM_PER_INCH, ProductSpec

PT_TO_MM = MM_PER_INCH / 72

LEVELS = ("error", "warning", "info", "ok")


@dataclass
class Issue:
    level: str  # error | warning | info | ok
    code: str
    message: str
    fix: str = ""
    auto_fixable: bool = False


@dataclass
class Report:
    file: str
    kind: str
    product: dict
    details: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str, fix: str = "", auto_fixable: bool = False) -> None:
        self.issues.append(Issue(level, code, message, fix, auto_fixable))

    @property
    def verdict(self) -> str:
        problems = [i for i in self.issues if i.level in ("error", "warning")]
        if not problems:
            return "ready"
        if all(i.auto_fixable for i in problems):
            return "fixable"
        return "needs_attention"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        d["summary"] = self.summary()
        return d

    def summary(self) -> str:
        return {
            "ready": "Print-ready: no problems found.",
            "fixable": "Not print-ready yet, but every problem can be fixed automatically with make_print_ready.",
            "needs_attention": "Has problems that need a better source file or a design change (see errors/warnings).",
        }[self.verdict]

    def to_text(self) -> str:
        icon = {"error": "✗", "warning": "!", "info": "i", "ok": "✓"}
        lines = [
            f"Preflight: {self.file}",
            f"Product:   {self.product['name']} — trim {self.product['width_mm']:g} × {self.product['height_mm']:g} mm, "
            f"bleed {self.product['bleed_mm']:g} mm, safe zone {self.product['safe_mm']:g} mm",
        ]
        for k, v in self.details.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        order = {lvl: n for n, lvl in enumerate(LEVELS)}
        for i in sorted(self.issues, key=lambda x: order[x.level]):
            fix = f"\n      → {i.fix}" if i.fix else ""
            auto = " [auto-fix]" if i.auto_fixable and i.level in ("error", "warning") else ""
            lines.append(f" {icon[i.level]} {i.level.upper():7} {i.message}{auto}{fix}")
        lines.append("")
        lines.append(f"Verdict: {self.verdict.upper()} — {self.summary()}")
        return "\n".join(lines)


# ------------------------------------------------------------------ dispatch

def check_file(path: str | Path, spec: ProductSpec, page: int | None = None) -> Report:
    p = Path(path)
    ext = p.suffix.lower()
    if not p.exists():
        raise io.ArtworkError(f"File not found: {p}")
    if ext in io.PDF_EXT:
        return check_pdf(p, spec)
    if ext in io.RENDER_EXT:
        from .render import render_artwork

        im = render_artwork(p, spec)
        rep = check_image(im, spec, p.name)
        rep.kind = f"{ext[1:]} (rendered at {spec.min_dpi} dpi)"
        return rep
    if ext in io.RASTER_EXT:
        return check_image(io.load_image(p), spec, p.name)
    raise io.ArtworkError(f"Unsupported file type '{ext}'. Use PDF, PNG, JPG, WebP, TIFF, SVG or HTML.")


def orient_spec(spec: ProductSpec, w: float, h: float) -> tuple[ProductSpec, bool]:
    """Match the product orientation to the artwork orientation."""
    if spec.width_mm == spec.height_mm:
        return spec, False
    art_landscape = w > h
    spec_landscape = spec.width_mm > spec.height_mm
    if art_landscape != spec_landscape:
        return spec.rotated(), True
    return spec, False


def analyse_geometry(w_px: int, h_px: int, spec: ProductSpec) -> dict:
    """Work out whether pixel artwork already includes bleed and its effective resolution."""
    r_img = w_px / h_px
    r_trim = spec.width_mm / spec.height_mm
    r_bleed = spec.bleed_width_mm / spec.bleed_height_mm
    d_trim = abs(r_img - r_trim) / r_trim
    d_bleed = abs(r_img - r_bleed) / r_bleed
    has_bleed = spec.bleed_mm > 0 and d_bleed < d_trim and d_bleed < 0.012
    w_mm = spec.bleed_width_mm if has_bleed else spec.width_mm
    h_mm = spec.bleed_height_mm if has_bleed else spec.height_mm
    dpi_x = w_px / (w_mm / MM_PER_INCH)
    dpi_y = h_px / (h_mm / MM_PER_INCH)
    return {
        "has_bleed": has_bleed,
        "aspect_mismatch": min(d_trim, d_bleed),
        "effective_dpi": min(dpi_x, dpi_y),
        "matched_w_mm": w_mm,
        "matched_h_mm": h_mm,
    }


# ------------------------------------------------------------------ images

def check_image(im: Image.Image, spec: ProductSpec, name: str = "artwork") -> Report:
    spec, rotated = orient_spec(spec, *im.size)
    rep = Report(file=name, kind=f"image ({im.mode})", product=spec.to_dict())
    w, h = im.size
    geo = analyse_geometry(w, h, spec)
    need_w, need_h = spec.pixels()
    rep.details.update({
        "pixels": f"{w} × {h}",
        "effective_resolution": f"{geo['effective_dpi']:.0f} dpi at final size",
        "includes_bleed": "yes" if geo["has_bleed"] else "no",
        "ideal_pixels": f"{need_w} × {need_h} (with bleed, {spec.min_dpi} dpi)",
    })
    if rotated:
        rep.add("info", "ORIENTATION", "Artwork is in the other orientation, so the product was rotated to match.")

    # proportions
    mm = geo["aspect_mismatch"]
    if mm > 0.015:
        rep.add(
            "warning", "ASPECT",
            f"Artwork proportions are {mm * 100:.1f}% off the {spec.width_mm:g} × {spec.height_mm:g} mm product, "
            "so part of it will be cropped (or padded).",
            "Auto-fix centre-crops to size (fit=cover) or pads with white (fit=contain). "
            "For best results redesign at the exact size.",
            auto_fixable=True,
        )
    # bleed
    transparent_cut = spec.allows_transparency and io.has_alpha(im)
    if spec.bleed_mm > 0 and not geo["has_bleed"] and not transparent_cut:
        rep.add(
            "warning", "NO_BLEED",
            f"No bleed: the printer needs {spec.bleed_mm:g} mm of extra background on every side, "
            "otherwise thin white slivers can appear at the cut edges.",
            "Auto-fix extends the edges by mirroring or stretching the outer pixels.",
            auto_fixable=True,
        )
    elif spec.bleed_mm > 0:
        rep.add("ok", "BLEED", f"Includes {spec.bleed_mm:g} mm bleed.")

    # resolution
    dpi = geo["effective_dpi"]
    if dpi < spec.acceptable_dpi:
        rep.add(
            "error", "LOW_RES",
            f"Resolution is only {dpi:.0f} dpi at final size (needs {spec.min_dpi}); it will print blurry or pixelated.",
            f"Re-export or regenerate the design at {need_w} × {need_h} px or larger "
            "(or use an AI upscaler, then re-check).",
        )
    elif dpi < spec.min_dpi * 0.95:
        rep.add(
            "warning", "LOW_RES",
            f"Resolution is {dpi:.0f} dpi; {spec.min_dpi} dpi is recommended for sharp text.",
            f"Acceptable for photos. For crisp text export at {need_w} × {need_h} px.",
        )
    else:
        rep.add("ok", "RESOLUTION", f"Resolution {dpi:.0f} dpi is good.")

    # transparency
    if io.has_alpha(im):
        if spec.allows_transparency:
            rep.add("ok", "TRANSPARENCY", "Transparent background kept (expected for this product).")
        else:
            rep.add(
                "warning", "TRANSPARENCY",
                "Image has transparent areas; paper is not transparent, they will print white.",
                "Auto-fix flattens onto white. Add a background colour if you want something else.",
                auto_fixable=True,
            )

    # colour
    if im.mode == "CMYK":
        tac = io.total_area_coverage(im)
        rep.add("ok", "COLOR", "Already CMYK.")
        if tac > 320:
            rep.add(
                "warning", "INK_LIMIT",
                f"Ink coverage reaches {tac:.0f}% (most presses allow 300–320%); dark areas may smudge or dry slowly.",
                "Ask the designer to reduce rich blacks / re-convert with a press ICC profile.",
            )
    else:
        rep.add("info", "RGB", "Artwork is RGB (screen colours); it will be converted to CMYK for print.",
                "Auto-fix converts to CMYK. Pass an ICC profile from your printer for the most accurate colour.",
                auto_fixable=True)
        vivid = io.vivid_fraction(im)
        if vivid > 0.03:
            rep.add(
                "warning", "VIVID_COLORS",
                f"About {vivid * 100:.0f}% of the design uses neon/very bright screen colours that ink can't reproduce; "
                "they will print noticeably duller.",
                "Expected for RGB designs. Check the proof; choose slightly deeper colours if exact brand colour matters.",
                auto_fixable=True,
            )

    # safe zone (heuristic)
    px_per_mm = w / geo["matched_w_mm"]
    margin_px = int((spec.safe_mm + (spec.bleed_mm if geo["has_bleed"] else 0)) * px_per_mm)
    if spec.safe_mm > 0 and io.edge_detail_in_margin(im, margin_px) > 0.06:
        rep.add(
            "warning", "SAFE_ZONE",
            f"Detail (text, lines or logo edges) was detected within {spec.safe_mm:g} mm of the cut edge; "
            "cutting can drift ~1–2 mm, so it may be trimmed off.",
            "Check the proof: keep text and logos inside the dashed safe line. Ignore if it's a background photo.",
        )
    return rep


# ------------------------------------------------------------------ PDFs

def _box(page, name: str):
    import pikepdf

    v = page.obj.get(name)
    if v is None:
        return None
    try:
        return [float(x) for x in v]
    except (TypeError, pikepdf.PdfError):
        return None


def _size_mm(box) -> tuple[float, float]:
    return abs(box[2] - box[0]) * PT_TO_MM, abs(box[3] - box[1]) * PT_TO_MM


def _matmul(m1, m2):
    a, b, c, d, e, f = m1
    A, B, C, D, E, F = m2
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]


def _cs_name(cs, resources) -> str:
    """Normalise a colour space (name, array, or resource reference) to a short label."""
    import pikepdf

    if isinstance(cs, pikepdf.Name) and resources is not None:
        named = (resources.get("/ColorSpace") or {}).get(cs) if str(cs) not in (
            "/DeviceRGB", "/DeviceCMYK", "/DeviceGray", "/Pattern") else None
        if named is not None:
            cs = named
    if isinstance(cs, pikepdf.Array) and len(cs):
        head = str(cs[0])
        if head == "/ICCBased":
            try:
                return {1: "/ICC-Gray", 3: "/ICC-RGB", 4: "/ICC-CMYK"}.get(int(cs[1].get("/N", 0)), head)
            except Exception:  # noqa: BLE001
                return head
        return head
    return str(cs)


RGB_SPACES = ("/DeviceRGB", "/ICC-RGB", "/CalRGB", "/RGB")


def _image_entry(px_w, px_h, ctm, cs_label, soft_mask) -> dict | None:
    a, b, c, d = ctm[:4]
    w_pt, h_pt = math.hypot(a, b), math.hypot(c, d)
    if w_pt <= 1 or h_pt <= 1 or not px_w or not px_h:
        return None
    return {
        "pixels": f"{px_w}×{px_h}",
        "placed_mm": f"{w_pt * PT_TO_MM:.0f}×{h_pt * PT_TO_MM:.0f}",
        "dpi": round(min(px_w / (w_pt / 72), px_h / (h_pt / 72))),
        "colorspace": cs_label,
        "soft_mask": soft_mask,
    }


MAX_FORM_VISITS = 4000


def _walk_content(obj, resources, ctm, found_images, found_ops, depth=0, ancestors=(), budget=None):
    """Walk a content stream tracking the CTM to find image placements and RGB colour use.
    Forms are revisited per distinct placement scale (a reused logo can be scaled differently each
    time); ``ancestors`` guards against cycles and ``budget`` bounds total work on hostile files."""
    import pikepdf

    budget = budget if budget is not None else {"visits": 0, "seen": set()}
    if depth > 12 or budget["visits"] > MAX_FORM_VISITS:
        return
    try:
        instructions = pikepdf.parse_content_stream(obj)
    except Exception:  # noqa: BLE001
        return
    stack = []
    xobjects = resources.get("/XObject", {}) if resources is not None else {}
    for operands, op in instructions:
        op = str(op)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else ctm
        elif op == "cm" and len(operands) == 6:
            ctm = _matmul([float(x) for x in operands], ctm)
        elif op in ("rg", "RG"):
            found_ops.add("rgb")
        elif op in ("k", "K"):
            found_ops.add("cmyk")
        elif op in ("cs", "CS") and operands:
            if _cs_name(operands[0], resources) in RGB_SPACES:
                found_ops.add("rgb")
        elif op == "INLINE IMAGE" and operands:
            ii = operands[0]
            try:
                px_w, px_h = int(ii.width), int(ii.height)
                cs_label = _cs_name(ii.obj.get("/ColorSpace", ii.obj.get("/CS")), resources)
            except Exception:  # noqa: BLE001
                continue
            cs_label = {"/RGB": "/DeviceRGB", "/G": "/DeviceGray", "/CMYK": "/DeviceCMYK"}.get(cs_label, cs_label)
            entry = _image_entry(px_w, px_h, ctm, cs_label, False)
            if entry:
                found_images.append(entry)
        elif op == "Do" and operands:
            name = operands[0]
            xo = xobjects.get(name) if xobjects is not None else None
            if xo is None:
                continue
            subtype = xo.get("/Subtype")
            if subtype == "/Image":
                entry = _image_entry(int(xo.get("/Width", 0)), int(xo.get("/Height", 0)), ctm,
                                     _cs_name(xo.get("/ColorSpace"), resources), "/SMask" in xo)
                if entry:
                    found_images.append(entry)
            elif subtype == "/Form":
                key = xo.objgen
                if key != (0, 0) and key in ancestors:
                    continue
                m = [float(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                sub_ctm = _matmul(m, ctm)
                # same form at the same scale gives the same DPI: walk it once
                scale_key = (key, round(math.hypot(sub_ctm[0], sub_ctm[1]), 1), round(math.hypot(sub_ctm[2], sub_ctm[3]), 1))
                if scale_key in budget["seen"]:
                    continue
                budget["seen"].add(scale_key)
                budget["visits"] += 1
                _walk_content(xo, xo.get("/Resources", resources), sub_ctm, found_images, found_ops,
                              depth + 1, ancestors + (key,), budget)


STANDARD_14 = {
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique", "Helvetica", "Helvetica-Bold",
    "Helvetica-Oblique", "Helvetica-BoldOblique", "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Symbol", "ZapfDingbats",
}


def _font_embedded(f) -> bool:
    sub = f.get("/Subtype")
    if sub == "/Type3":
        return True
    descs = [df.get("/FontDescriptor") for df in f.get("/DescendantFonts", [])] if sub == "/Type0" else [f.get("/FontDescriptor")]
    return any(d is not None and any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")) for d in descs)


def _used_fonts(obj, resources, out: dict, depth=0, seen=None):
    """Collect fonts actually selected with Tf in content streams (recursing into forms)."""
    import pikepdf

    if depth > 8 or resources is None:
        return
    seen = seen if seen is not None else set()
    try:
        instructions = pikepdf.parse_content_stream(obj)
    except Exception:  # noqa: BLE001
        return
    fonts = resources.get("/Font") or {}
    xobjects = resources.get("/XObject") or {}
    current = None
    for operands, op in instructions:
        op = str(op)
        if op == "Tf" and operands:
            current = fonts.get(operands[0]) if fonts is not None else None
        elif op in ("Tj", "TJ", "'", '"') and current is not None:
            # only fonts that actually draw text matter (generators often select a default font and never use it)
            out[str(current.get("/BaseFont", "unknown")).lstrip("/")] = _font_embedded(current)
        elif op == "Do" and operands:
            xo = xobjects.get(operands[0]) if xobjects is not None else None
            if xo is not None and xo.get("/Subtype") == "/Form" and xo.objgen not in seen:
                seen.add(xo.objgen)
                _used_fonts(xo, xo.get("/Resources", resources), out, depth + 1, seen)


def _font_problems(pdf) -> list[str]:
    """Names of fonts used on any page that are not embedded."""
    used: dict[str, bool] = {}
    for page in pdf.pages:
        _used_fonts(page.obj, page.obj.get("/Resources"), used)
    return sorted(name for name, embedded in used.items() if not embedded)


def check_pdf(path: Path, spec_in: ProductSpec) -> Report:
    import pikepdf

    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        rep = Report(str(path.name), "pdf", spec_in.to_dict())
        rep.add("error", "ENCRYPTED", "PDF is password-protected; printers can't open it.", "Export it again without a password.")
        return rep

    first = pdf.pages[0]
    media = _box(first, "/MediaBox") or [0, 0, 612, 792]
    trim = _box(first, "/TrimBox")
    ref = trim or media
    rw, rh = _size_mm(ref)
    if int(first.obj.get("/Rotate", 0)) % 180:
        rw, rh = rh, rw
    spec, rotated = orient_spec(spec_in, rw, rh)
    rep = Report(file=path.name, kind=f"pdf ({len(pdf.pages)} page{'s' if len(pdf.pages) != 1 else ''})", product=spec.to_dict())
    if rotated:
        rep.add("info", "ORIENTATION", "Document is in the other orientation, so the product was rotated to match.")
    if len(pdf.pages) > 2:
        rep.add("info", "PAGES", f"{len(pdf.pages)} pages: make sure that's intended (cards/flyers are usually 1–2 pages).")

    worst_dpi = None
    any_rgb = False
    size_ok_all = True
    bleed_ok_all = True
    pages_checked = min(len(pdf.pages), 50)
    for idx in range(pages_checked):
        page = pdf.pages[idx]
        media = _box(page, "/MediaBox") or media
        trim = _box(page, "/TrimBox")
        bleed = _box(page, "/BleedBox")
        tw, th = _size_mm(trim or media)
        mw, mh = _size_mm(media)
        if idx == 0:
            rep.details["page_size"] = f"{mw:.1f} × {mh:.1f} mm"
            rep.details["trim_box"] = f"{tw:.1f} × {th:.1f} mm" if trim else "not set"
        if int(page.obj.get("/Rotate", 0)) % 180:
            tw, th = th, tw
            mw, mh = mh, mw

        if trim:
            size_match = abs(tw - spec.width_mm) <= 1.0 and abs(th - spec.height_mm) <= 1.0
            outer = bleed or media
            avail = min(trim[0] - outer[0], trim[1] - outer[1], outer[2] - trim[2], outer[3] - trim[3]) * PT_TO_MM
            has_bleed = avail >= spec.bleed_mm - 0.3
        else:
            match_trim = abs(mw - spec.width_mm) <= 1.0 and abs(mh - spec.height_mm) <= 1.0
            match_bleed = abs(mw - spec.bleed_width_mm) <= 1.0 and abs(mh - spec.bleed_height_mm) <= 1.0
            size_match = match_trim or match_bleed
            has_bleed = match_bleed and spec.bleed_mm > 0
        size_ok_all &= size_match
        bleed_ok_all &= has_bleed or spec.bleed_mm == 0

        imgs: list[dict] = []
        ops: set[str] = set()
        _walk_content(page.obj, page.obj.get("/Resources"), [1, 0, 0, 1, 0, 0], imgs, ops)
        for im in imgs:
            worst_dpi = im["dpi"] if worst_dpi is None else min(worst_dpi, im["dpi"])
            if im["colorspace"] in ("/DeviceRGB", "/ICC-RGB", "/CalRGB"):
                any_rgb = True
        any_rgb |= "rgb" in ops
        if idx == 0 and imgs:
            rep.details["images_on_page_1"] = "; ".join(
                f"{i['pixels']} px at {i['placed_mm']} mm = {i['dpi']} dpi ({i['colorspace'].lstrip('/')})" for i in imgs[:6]
            )

    if not size_ok_all:
        rep.add(
            "warning", "SIZE",
            f"Page size doesn't match {spec.width_mm:g} × {spec.height_mm:g} mm (trim) or "
            f"{spec.bleed_width_mm:g} × {spec.bleed_height_mm:g} mm (with bleed).",
            "Auto-fix rasterises and fits the page to the product size (centre crop). Better: export at the right size.",
            auto_fixable=True,
        )
    else:
        rep.add("ok", "SIZE", "Page size matches the product.")
    if spec.bleed_mm > 0 and not bleed_ok_all:
        rep.add(
            "warning", "NO_BLEED",
            f"No {spec.bleed_mm:g} mm bleed found (no TrimBox/BleedBox and page isn't oversized).",
            "Auto-fix adds bleed by extending the edges. Better: export from your design tool with bleed enabled.",
            auto_fixable=True,
        )
    elif spec.bleed_mm > 0:
        rep.add("ok", "BLEED", "Bleed present.")
    if not _box(first, "/TrimBox") and bleed_ok_all and spec.bleed_mm > 0:
        rep.add("info", "TRIMBOX", "Page includes bleed but has no TrimBox; make_print_ready will add one plus crop marks.", auto_fixable=True)

    missing = _font_problems(pdf)
    if missing:
        only_std = all(m.split("+")[-1] in STANDARD_14 for m in missing)
        rep.add(
            "warning" if only_std else "error", "FONTS",
            f"Fonts not embedded: {', '.join(missing[:8])}. The printer's computer will substitute them and text will change.",
            "Auto-fix rasterises the pages at print resolution, which bakes the fonts in. "
            "Better: re-export with 'embed all fonts'.",
            auto_fixable=True,
        )
    else:
        rep.add("ok", "FONTS", "All fonts embedded (or no fonts).")

    if worst_dpi is not None:
        rep.details["lowest_image_resolution"] = f"{worst_dpi} dpi"
        if worst_dpi < spec.acceptable_dpi:
            rep.add("error", "LOW_RES", f"An image is only {worst_dpi} dpi at its placed size; it will look blurry.",
                    f"Replace it with a higher-resolution image (≥ {spec.min_dpi} dpi at placed size).")
        elif worst_dpi < spec.min_dpi * 0.95:
            rep.add("warning", "LOW_RES", f"Lowest image resolution is {worst_dpi} dpi ({spec.min_dpi} recommended).",
                    "Usually fine for photos; replace for crisp graphics/text.")
        else:
            rep.add("ok", "RESOLUTION", f"Image resolution OK (lowest {worst_dpi} dpi).")

    if any_rgb:
        rep.add("info", "RGB", "Contains RGB colour; the printer (or make_print_ready with color=cmyk) will convert to CMYK.",
                "Auto-fix can convert everything to CMYK (rasterises the page).", auto_fixable=True)
    rep.details["pages_checked"] = str(pages_checked)
    pdf.close()
    return rep
