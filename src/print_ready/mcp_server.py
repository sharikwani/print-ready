"""MCP server: lets Claude, ChatGPT (desktop/dev mode), Gemini CLI, Cursor, Codex… make files print-ready.

Run:  printready mcp            (stdio, for local clients)
      printready-mcp --http     (streamable HTTP on :8000/mcp, for remote clients)
"""
from __future__ import annotations

import argparse
from pathlib import Path

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server

    MCP_V2 = True
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image

    MCP_V2 = False

from . import __version__
from .specs import SHEETS, SpecError, get_product, list_products

INSTRUCTIONS = """print-ready turns any design (PNG/JPG/PDF/SVG/HTML, including AI-generated art) into a
file a print shop will accept: exact size, bleed, CMYK, crop marks, TrimBox/BleedBox, plus a visual proof.

Typical flow when a user wants something printed:
1. list_print_products → pick the product (or use width_mm/height_mm for custom sizes).
2. If you are designing it: design at the product's trim size, keep text inside the safe zone,
   extend backgrounds to the edges. design_spec tells you exact pixel sizes.
3. preflight_check the artwork and explain problems in plain language.
4. make_print_ready → creates the PDF. Then make_proof and SHOW the proof image to the user.
5. Optionally impose_sheet (for print shops) or get_print_quote (prices).
Never claim a file is print-ready without running preflight_check or make_print_ready.
All file paths are on the machine running this server."""

mcp = _Server("print-ready", instructions=INSTRUCTIONS)


def _spec(product, width_mm, height_mm, bleed_mm=None, safe_mm=None, dpi=None):
    return get_product(product, width_mm, height_mm, bleed_mm, safe_mm, dpi)


def _err(e: Exception) -> str:
    return f"ERROR: {e}"


@mcp.tool()
def list_print_products(category: str | None = None) -> str:
    """List supported print products with trim size, bleed, safe zone and the pixel size to design at.
    Categories: cards, flyers, stationery, posters, large_format, stickers, apparel, merch, photo."""
    lines = []
    for p in list_products(category):
        w, h = p.pixels()
        lines.append(f"{p.id}: {p.name} | trim {p.width_mm:g}×{p.height_mm:g} mm | bleed {p.bleed_mm:g} mm | "
                     f"safe {p.safe_mm:g} mm | design at {w}×{h}px ({p.min_dpi} dpi, incl. bleed)")
    return "\n".join(lines) or "No products in that category."


@mcp.tool()
def design_spec(product: str | None = None, width_mm: float | None = None, height_mm: float | None = None) -> dict:
    """Exact numbers for designing a print piece: trim/bleed sizes in mm, pixels, safe zone, and
    CSS sizes for HTML designs. Call this BEFORE generating artwork for print."""
    try:
        s = _spec(product, width_mm, height_mm)
    except SpecError as e:
        return {"error": str(e)}
    css = lambda mm: round(mm / 25.4 * 96)  # noqa: E731
    d = s.to_dict()
    d["html_css_size_trim"] = f"{css(s.width_mm)}px × {css(s.height_mm)}px (96 css px per inch)"
    d["guidance"] = [
        f"Background/photos must extend {s.bleed_mm:g} mm past every edge (or design at trim size and let make_print_ready extend it).",
        f"Keep text, logos and important details at least {s.safe_mm:g} mm inside the trim edge.",
        f"Raster art needs ≥ {s.min_dpi} dpi at final size: {d['design_pixels_with_bleed']}.",
        "Avoid hairlines thinner than 0.25 pt and text smaller than 6 pt.",
        "Very bright RGB colours (neon blue/green/pink) print duller in CMYK.",
    ]
    return d


@mcp.tool()
def preflight_check(file_path: str, product: str | None = None, width_mm: float | None = None,
                    height_mm: float | None = None, bleed_mm: float | None = None) -> str:
    """Check a design file (PDF, PNG, JPG, WebP, TIFF, SVG, HTML) against a print product and list every
    problem a print shop would reject it for: size, bleed, resolution, fonts, colour, transparency, safe zone."""
    from .preflight import check_file

    try:
        return check_file(file_path, _spec(product, width_mm, height_mm, bleed_mm)).to_text()
    except Exception as e:  # noqa: BLE001 - always answer the model with a readable message
        return _err(e)


@mcp.tool()
def make_print_ready(file_paths: list[str], product: str | None = None, width_mm: float | None = None,
                     height_mm: float | None = None, output_path: str | None = None, fit: str = "cover",
                     bleed_mode: str = "auto", color: str = "cmyk", icc_path: str | None = None,
                     crop_marks: bool = True, bleed_mm: float | None = None) -> str:
    """Create a print-ready PDF from one or more files (2 files = front and back).
    Adds bleed (auto/mirror/extend), fits to exact size (cover=crop, contain=pad), converts to CMYK
    (optionally with the printer's ICC profile), adds crop marks and TrimBox/BleedBox.
    Returns the output path and what was changed. Follow with make_proof to show the user."""
    from .build import make_print_ready as build

    try:
        res = build(file_paths, _spec(product, width_mm, height_mm, bleed_mm), output_path, fit=fit,
                    bleed_mode=bleed_mode, color=color, icc_path=icc_path, crop_marks=crop_marks)
        return res.to_text()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def make_proof(pdf_path: str, page: int = 1, safe_mm: float = 3.0, output_path: str | None = None) -> list:
    """Render a proof image of a print-ready PDF: red = bleed that gets trimmed, black = cut line,
    blue dashed = safe zone. Returns the image so you can show it to the user, plus its file path."""
    from .proof import make_proof as proof

    try:
        spec = get_product("custom", 1, 1, safe_mm=safe_mm)
        path = proof(pdf_path, spec, output_path, page=page - 1)
    except Exception as e:  # noqa: BLE001
        return [_err(e)]
    return [f"Proof saved to {path}", Image(path=path)]


@mcp.tool()
def impose_sheet(pdf_path: str, sheet: str = "sra3", output_path: str | None = None, crop_marks: bool = True) -> str:
    """Lay out as many copies as fit on a press sheet (n-up) with cut marks — what print shops do before
    printing cards, stickers and flyers. Sheets: a4, a3, sra3, 12x18, 13x19, letter, tabloid.
    A 2-page PDF (front/back) gives mirrored backs for work-and-turn duplex."""
    from .impose import impose

    try:
        return impose(pdf_path, sheet, output_path, crop_marks=crop_marks).to_text()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool()
def get_print_quote(product: str, quantity: int, double_sided: bool = False, paper: str | None = None,
                    finishes: list[str] | None = None, rush: bool = False, customer_name: str | None = None) -> str:
    """Instant price quote for printing (via PrintQuote; needs PRINTQUOTE_MCP_URL in the server's environment).
    Returns an itemised quote and a shareable quote link."""
    from .quote import QuoteUnavailable, get_quote

    try:
        q = get_quote(get_product(product), quantity, double_sided=double_sided, paper=paper, finishes=finishes,
                      rush=rush, customer_name=customer_name)
        return q["text"]
    except (QuoteUnavailable, SpecError) as e:
        return str(e)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="printready-mcp", description=f"print-ready MCP server {__version__}")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    if a.http:
        if MCP_V2:
            mcp.run(transport="streamable-http", host=a.host, port=a.port)
        else:
            mcp.settings.host = a.host
            mcp.settings.port = a.port
            mcp.run(transport="streamable-http")
    else:
        mcp.run()


__all__ = ["mcp", "main", "SHEETS", "Path"]

if __name__ == "__main__":
    main()
