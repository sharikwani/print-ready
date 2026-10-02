"""Render HTML/SVG designs (what AI assistants usually produce) to print-resolution images.

Uses Playwright + Chromium (optional dependency: ``pip install 'print-ready[render]'``
then ``playwright install chromium``). The design is laid out at the product's trim
size in CSS pixels (96 per inch) and captured at the product's target DPI.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from .imageops import ArtworkError
from .specs import MM_PER_INCH, ProductSpec


def render_artwork(path: str | Path, spec: ProductSpec, dpi: int | None = None, include_bleed: bool = False) -> Image.Image:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ArtworkError(
            "Rendering HTML/SVG needs Playwright: pip install 'print-ready[render]' && playwright install chromium. "
            "Or export the design as PNG/PDF first."
        ) from e

    p = Path(path).resolve()
    dpi = dpi or max(spec.min_dpi, 150)
    w_mm = spec.bleed_width_mm if include_bleed else spec.width_mm
    h_mm = spec.bleed_height_mm if include_bleed else spec.height_mm
    css_w = round(w_mm / MM_PER_INCH * 96)
    css_h = round(h_mm / MM_PER_INCH * 96)
    scale = dpi / 96
    launch_kwargs = {}
    exe = os.environ.get("PRINT_READY_CHROMIUM")
    if exe:
        launch_kwargs["executable_path"] = exe
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": css_w, "height": css_h}, device_scale_factor=scale)
        if p.suffix.lower() == ".svg":
            # Stretch the SVG to the page so its viewBox maps onto the trim size.
            page.set_content(
                f"<html><body style='margin:0'><img src='{p.as_uri()}' "
                f"style='width:{css_w}px;height:{css_h}px;display:block;object-fit:fill'></body></html>"
            )
        else:
            page.goto(p.as_uri())
        page.wait_for_load_state("networkidle")
        png = page.screenshot(clip={"x": 0, "y": 0, "width": css_w, "height": css_h}, omit_background=spec.allows_transparency)
        browser.close()
    import io

    return Image.open(io.BytesIO(png)).copy()
