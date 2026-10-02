"""Command line interface: ``printready <command> ...``"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .imageops import ArtworkError
from .specs import SHEETS, SpecError, get_product, list_products


def _spec_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-p", "--product", help="product id, e.g. business_card, flyer_a5, poster_a3 (see `printready products`)")
    p.add_argument("--width", type=float, help="custom trim width in mm")
    p.add_argument("--height", type=float, help="custom trim height in mm")
    p.add_argument("--bleed", type=float, help="override bleed in mm")
    p.add_argument("--safe", type=float, help="override safe margin in mm")
    p.add_argument("--dpi", type=int, help="override target resolution")


def _spec(a):
    return get_product(a.product, a.width, a.height, a.bleed, a.safe, a.dpi)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="printready", description="Make any design print-ready: preflight, bleed, CMYK, crop marks, proofs, imposition.")
    ap.add_argument("--version", action="version", version=f"print-ready {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("products", help="list supported products and their specs")
    p.add_argument("--category")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="preflight a file and explain every problem")
    p.add_argument("file")
    _spec_args(p)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fix", help="create a print-ready PDF (bleed, CMYK, crop marks, boxes)")
    p.add_argument("files", nargs="+", help="artwork files; 2 files = front & back")
    _spec_args(p)
    p.add_argument("-o", "--output")
    p.add_argument("--fit", choices=["cover", "contain"], default="cover")
    p.add_argument("--bleed-mode", choices=["auto", "mirror", "extend"], default="auto")
    p.add_argument("--color", choices=["cmyk", "rgb"], default="cmyk")
    p.add_argument("--icc", help="CMYK output ICC profile from your printer (e.g. ISOcoated_v2_eci.icc)")
    p.add_argument("--intent", choices=["perceptual", "relative", "saturation"], default="perceptual")
    p.add_argument("--no-marks", action="store_true", help="no crop marks (many online printers prefer bleed only)")
    p.add_argument("--rasterize", action="store_true", help="always rasterise PDFs (forces full CMYK conversion)")
    p.add_argument("--jpeg", type=int, metavar="QUALITY", help="JPEG-compress images (smaller files), e.g. 92")
    p.add_argument("--proof", action="store_true", help="also write a proof PNG")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("proof", help="render a proof PNG of a print-ready PDF (bleed/trim/safe overlays)")
    p.add_argument("pdf")
    _spec_args(p)
    p.add_argument("-o", "--output")
    p.add_argument("--page", type=int, default=1)

    p = sub.add_parser("impose", help="n-up a print-ready PDF onto a press sheet")
    p.add_argument("pdf")
    p.add_argument("--sheet", default="sra3", choices=list(SHEETS))
    p.add_argument("-o", "--output")
    p.add_argument("--copies", type=int, help="max copies per sheet")
    p.add_argument("--no-marks", action="store_true")

    p = sub.add_parser("quote", help="instant price quote (needs PRINTQUOTE_MCP_URL)")
    _spec_args(p)
    p.add_argument("-q", "--quantity", type=int, required=True)
    p.add_argument("--double-sided", action="store_true")
    p.add_argument("--paper")
    p.add_argument("--finish", action="append")
    p.add_argument("--rush", action="store_true")

    sub.add_parser("mcp", help="run the MCP server (stdio) for Claude, ChatGPT, Gemini CLI, Cursor…")

    a = ap.parse_args(argv)
    if getattr(a, "page", 1) < 1:
        print("error: --page starts at 1", file=sys.stderr)
        return 2
    try:
        return _run(a)
    except (SpecError, ArtworkError, ValueError, OSError, IndexError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - pikepdf/pdfium errors on broken files
        if type(e).__module__.split(".")[0] in ("pikepdf", "pypdfium2"):
            print(f"error: can't read the file ({type(e).__name__}: {e})", file=sys.stderr)
            return 2
        raise


def _run(a) -> int:
    if a.cmd == "products":
        items = list_products(a.category)
        if a.json:
            print(json.dumps([p.to_dict() for p in items], indent=2))
        else:
            cat = None
            for p in items:
                if p.category != cat:
                    cat = p.category
                    print(f"\n[{cat}]")
                w, h = p.pixels()
                print(f"  {p.id:22} {p.width_mm:7.1f} × {p.height_mm:<7.1f} mm  bleed {p.bleed_mm:g}  design at {w}×{h}px  — {p.name}")
        return 0

    if a.cmd == "check":
        from .preflight import check_file

        rep = check_file(a.file, _spec(a))
        print(json.dumps(rep.to_dict(), indent=2) if a.json else rep.to_text())
        return 0 if rep.verdict == "ready" else 1

    if a.cmd == "fix":
        from .build import make_print_ready

        spec = _spec(a)
        res = make_print_ready(a.files, spec, a.output, fit=a.fit, bleed_mode=a.bleed_mode, color=a.color,
                               icc_path=a.icc, intent=a.intent, crop_marks=not a.no_marks,
                               keep_vector=not a.rasterize, jpeg_quality=a.jpeg)
        proof_path = None
        if a.proof:
            from .proof import make_proof

            proof_path = make_proof(res.output, get_product(**_spec_kwargs(res.product)))
        if a.json:
            d = res.__dict__ | {"proof": proof_path}
            print(json.dumps(d, indent=2))
        else:
            print(res.to_text())
            if proof_path:
                print(f"  Proof: {proof_path}")
        return 0

    if a.cmd == "proof":
        from .proof import make_proof

        spec = _spec(a) if (a.product or a.width) else get_product("custom", 1, 1, safe_mm=a.safe or 3)
        print(make_proof(a.pdf, spec, a.output, page=a.page - 1))
        return 0

    if a.cmd == "impose":
        from .impose import impose

        print(impose(a.pdf, a.sheet, a.output, crop_marks=not a.no_marks, copies=a.copies).to_text())
        return 0

    if a.cmd == "quote":
        from .quote import QuoteUnavailable, get_quote

        try:
            q = get_quote(_spec(a), a.quantity, double_sided=a.double_sided, paper=a.paper, finishes=a.finish, rush=a.rush)
        except QuoteUnavailable as e:
            print(str(e), file=sys.stderr)
            return 2
        print(q["text"])
        return 1 if q["is_error"] else 0

    if a.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main([])
        return 0
    return 1


def _spec_kwargs(product: dict) -> dict:
    return {
        "product": product["id"] if product["id"] != "custom" else None,
        "width_mm": product["width_mm"],
        "height_mm": product["height_mm"],
        "bleed_mm": product["bleed_mm"],
        "safe_mm": product["safe_mm"],
    }


if __name__ == "__main__":
    sys.exit(main())
