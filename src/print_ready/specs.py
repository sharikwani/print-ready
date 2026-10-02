"""Print product specifications: trim size, bleed, safe zone and resolution targets.

All dimensions are in millimetres. Values follow common print-shop defaults; every
printer has its own spec sheet, so treat these as sensible starting points and let
users override them (``custom`` product or explicit width/height/bleed).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

MM_PER_INCH = 25.4
PT_PER_MM = 72 / MM_PER_INCH


def inch(v: float) -> float:
    return round(v * MM_PER_INCH, 3)


@dataclass(frozen=True)
class ProductSpec:
    id: str
    name: str
    width_mm: float  # trim width (finished size)
    height_mm: float  # trim height
    bleed_mm: float = 3.0
    safe_mm: float = 3.0  # keep text/logos this far inside the trim edge
    min_dpi: int = 300  # target effective resolution at final size
    acceptable_dpi: int = 200  # below this is a hard fail
    category: str = "general"
    allows_transparency: bool = False  # e.g. DTG garments, die-cut stickers
    notes: str = ""

    @property
    def bleed_width_mm(self) -> float:
        return self.width_mm + 2 * self.bleed_mm

    @property
    def bleed_height_mm(self) -> float:
        return self.height_mm + 2 * self.bleed_mm

    def pixels(self, dpi: int | None = None, with_bleed: bool = True) -> tuple[int, int]:
        """Pixel dimensions needed at ``dpi`` (default: the product's target)."""
        d = dpi or self.min_dpi
        w = self.bleed_width_mm if with_bleed else self.width_mm
        h = self.bleed_height_mm if with_bleed else self.height_mm
        return round(w / MM_PER_INCH * d), round(h / MM_PER_INCH * d)

    def rotated(self) -> "ProductSpec":
        return replace(self, width_mm=self.height_mm, height_mm=self.width_mm)

    def to_dict(self) -> dict:
        d = asdict(self)
        w, h = self.pixels()
        d["design_pixels_with_bleed"] = f"{w} x {h} px @ {self.min_dpi} dpi"
        tw, th = self.pixels(with_bleed=False)
        d["trim_pixels"] = f"{tw} x {th} px @ {self.min_dpi} dpi"
        return d


_P = ProductSpec
PRODUCTS: dict[str, ProductSpec] = {
    p.id: p
    for p in [
        # Cards
        _P("business_card", "Business / visiting card (3.5 x 2 in, US & India)", inch(3.5), inch(2), 3, 4, category="cards"),
        _P("business_card_eu", "Business card (85 x 55 mm, Europe)", 85, 55, 3, 4, category="cards"),
        _P("business_card_square", "Square business card (2.5 x 2.5 in)", inch(2.5), inch(2.5), 3, 4, category="cards"),
        _P("postcard_4x6", "Postcard 4 x 6 in", inch(6), inch(4), 3, 4, category="cards"),
        _P("postcard_a6", "Postcard A6 (148 x 105 mm)", 148, 105, 3, 4, category="cards"),
        _P("invitation_5x7", "Invitation / greeting card 5 x 7 in", inch(5), inch(7), 3, 5, category="cards"),
        # Paper
        _P("flyer_a6", "Flyer A6 (105 x 148 mm)", 105, 148, 3, 4, category="flyers"),
        _P("flyer_a5", "Flyer A5 (148 x 210 mm)", 148, 210, 3, 5, category="flyers"),
        _P("flyer_a4", "Flyer A4 (210 x 297 mm)", 210, 297, 3, 5, category="flyers"),
        _P("flyer_letter", "Flyer US Letter (8.5 x 11 in)", inch(8.5), inch(11), 3.175, 6, category="flyers"),
        _P("letterhead_a4", "Letterhead A4", 210, 297, 3, 10, category="stationery"),
        _P("envelope_dl", "Envelope DL (220 x 110 mm)", 220, 110, 3, 10, category="stationery"),
        _P("brochure_a4_trifold", "Tri-fold brochure, A4 flat (297 x 210 mm)", 297, 210, 3, 5, category="flyers",
           notes="Flat size. Panels are ~100/100/97 mm; keep text away from the folds."),
        # Posters & large format
        _P("poster_a3", "Poster A3 (297 x 420 mm)", 297, 420, 3, 8, 300, 150, category="posters"),
        _P("poster_a2", "Poster A2 (420 x 594 mm)", 420, 594, 3, 10, 200, 120, category="posters"),
        _P("poster_a1", "Poster A1 (594 x 841 mm)", 594, 841, 3, 15, 150, 100, category="posters"),
        _P("poster_18x24", "Poster 18 x 24 in", inch(18), inch(24), 3.175, 12, 200, 120, category="posters"),
        _P("poster_24x36", "Poster 24 x 36 in", inch(24), inch(36), 3.175, 15, 150, 100, category="posters"),
        _P("rollup_banner", "Roll-up / standee banner (850 x 2000 mm)", 850, 2000, 5, 40, 120, 72, category="large_format",
           notes="Bottom ~100 mm usually goes into the stand; keep it free of key content."),
        _P("flex_banner_6x3ft", "Flex banner 6 x 3 ft", 1828.8, 914.4, 0, 50, 100, 50, category="large_format",
           notes="Flex is usually printed edge-to-edge with no bleed; eyelets need ~50 mm clear margin."),
        # Stickers & merch
        _P("sticker_2in", "Sticker 2 x 2 in", inch(2), inch(2), 2, 3, category="stickers", allows_transparency=True),
        _P("sticker_3in", "Sticker 3 x 3 in", inch(3), inch(3), 2, 3, category="stickers", allows_transparency=True),
        _P("label_a4_sheet", "Label sheet A4", 210, 297, 0, 5, category="stickers"),
        _P("tshirt_dtg", "T-shirt front print (12 x 16 in, DTG)", inch(12), inch(16), 0, 0, 300, 150, category="apparel",
           allows_transparency=True, notes="Transparent background is expected; no bleed."),
        _P("mug_wrap", "11 oz mug wrap (8.5 x 3.5 in)", inch(8.5), inch(3.5), 2, 6, category="merch"),
        # Social-to-print favourites
        _P("photo_4x6", "Photo print 4 x 6 in", inch(6), inch(4), 2, 3, category="photo"),
        _P("photo_8x10", "Photo print 8 x 10 in", inch(10), inch(8), 2, 5, category="photo"),
    ]
}

ALIASES = {
    "visiting_card": "business_card",
    "card": "business_card",
    "bc": "business_card",
    "a4": "flyer_a4",
    "a5": "flyer_a5",
    "a6": "flyer_a6",
    "a3": "poster_a3",
    "letter": "flyer_letter",
    "tshirt": "tshirt_dtg",
    "t_shirt": "tshirt_dtg",
    "standee": "rollup_banner",
    "banner": "flex_banner_6x3ft",
}


class SpecError(ValueError):
    pass


def get_product(
    product: str | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    bleed_mm: float | None = None,
    safe_mm: float | None = None,
    dpi: int | None = None,
) -> ProductSpec:
    """Resolve a product id/alias, or build a custom spec from explicit dimensions."""
    if bool(width_mm) != bool(height_mm):
        raise SpecError("Give both width_mm and height_mm for a custom size (or neither).")
    if width_mm and height_mm:
        if width_mm <= 0 or height_mm <= 0 or width_mm > 20000 or height_mm > 20000:
            raise SpecError("width_mm and height_mm must be between 0 and 20000.")
        base = None
        if product and _norm(product) != "custom":
            key = ALIASES.get(_norm(product), _norm(product))
            base = PRODUCTS.get(key)
        spec = base or _P("custom", f"Custom {width_mm:g} x {height_mm:g} mm", width_mm, height_mm)
        spec = replace(spec, width_mm=float(width_mm), height_mm=float(height_mm))
    else:
        if not product:
            raise SpecError("Give a product id (see list_products) or width_mm and height_mm.")
        key = _norm(product)
        key = ALIASES.get(key, key)
        if key not in PRODUCTS:
            close = [k for k in PRODUCTS if key in k or k in key]
            hint = f" Did you mean: {', '.join(close[:5])}?" if close else ""
            raise SpecError(f"Unknown product '{product}'.{hint} Use list_products to see all ids.")
        spec = PRODUCTS[key]
    if bleed_mm is not None:
        spec = replace(spec, bleed_mm=float(bleed_mm))
    if safe_mm is not None:
        spec = replace(spec, safe_mm=float(safe_mm))
    if dpi:
        spec = replace(spec, min_dpi=int(dpi), acceptable_dpi=min(spec.acceptable_dpi, int(dpi)))
    return spec


def _norm(s: str) -> str:
    return s.strip().lower().replace("-", "_").replace(" ", "_")


def list_products(category: str | None = None) -> list[ProductSpec]:
    items = PRODUCTS.values()
    if category:
        items = [p for p in items if p.category == _norm(category)]
    return list(items)


# Common press sheets for imposition (width x height, mm)
SHEETS: dict[str, tuple[float, float]] = {
    "a4": (210, 297),
    "a3": (297, 420),
    "sra3": (320, 450),
    "12x18": (inch(12), inch(18)),
    "13x19": (inch(13), inch(19)),
    "letter": (inch(8.5), inch(11)),
    "tabloid": (inch(11), inch(17)),
}
