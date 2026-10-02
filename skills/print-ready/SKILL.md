---
name: print-ready
description: Make designs printable. Use whenever the user wants to print something (business/visiting cards, flyers, posters, stickers, banners, invitations, letterheads, T-shirts, mugs), asks for a "print-ready" or "press-ready" file, mentions bleed, crop marks, CMYK, DPI, trim or safe zone, or wants to send an AI-generated design to a printer. Preflights files, adds bleed, converts to CMYK, adds crop marks, makes a visual proof, imposes n-up sheets and can fetch a price quote.
license: MIT
---

# print-ready

AI-made designs usually get rejected by print shops: no bleed, wrong size, RGB colour,
low resolution, text too close to the edge, fonts not embedded. This skill fixes that.

## Tools

Use the MCP tools if the `print-ready` server is connected (`preflight_check`,
`make_print_ready`, `make_proof`, `impose_sheet`, `design_spec`, `list_print_products`,
`get_print_quote`). Otherwise use the CLI:

```bash
# one-off, no install (needs uv: https://docs.astral.sh/uv/)
uvx --from git+https://github.com/sharikwani/print-ready printready <command>
# or: pip install "git+https://github.com/sharikwani/print-ready"  then  printready <command>
```

| Task | CLI |
|---|---|
| List products & sizes | `printready products` |
| Check a file | `printready check design.png -p business_card` |
| Make print-ready PDF (+ proof) | `printready fix design.png -p business_card --proof` |
| Front + back | `printready fix front.png back.png -p business_card --proof` |
| Custom size (mm) | `printready fix art.png --width 100 --height 210 --proof` |
| Printer's colour profile | add `--icc ISOcoated_v2_eci.icc` |
| No crop marks (online printers) | add `--no-marks` |
| N-up press sheet | `printready impose card-print-ready.pdf --sheet sra3` |
| Price quote | `printready quote -p business_card -q 500 --double-sided` (needs `PRINTQUOTE_MCP_URL`) |

Exit code of `check`: 0 = ready, 1 = has issues, 2 = error.

## Workflow

1. **Pin down the product.** Ask what it is and the size if unclear (`business_card` is
   3.5×2 in, used in India and the US; `business_card_eu` is 85×55 mm). Use
   `printready products` / `list_print_products` for ids, or width/height in mm.
2. **If you are creating the design**, get the numbers first (`design_spec` or the table
   below) and follow the design rules. For HTML designs, size the page to the trim in CSS
   pixels and export PNG at the product's pixel size, or pass the .html file directly
   (needs the `render` extra).
3. **Preflight** the file and explain the result in plain language: what's wrong, why it
   matters on paper, and whether it's auto-fixable.
4. **Fix**: run `fix` / `make_print_ready`. Read the actions and warnings it returns.
5. **Show the proof** PNG to the user (red = trimmed off, black = cut line, blue dashed =
   safe zone) and point out anything outside the safe zone or cropped.
6. **Deliver** the PDF path. Offer imposition (print shops) or a quote.

Never tell the user a file is print-ready unless `check` says READY or you just produced it
with `fix` and reviewed the warnings.

## Design rules (tell the user / follow them when designing)

- **Bleed**: backgrounds and images run 3 mm (⅛ in) past every edge.
- **Safe zone**: text and logos stay ≥ 3–5 mm inside the cut line (product-specific).
- **Resolution**: 300 dpi at final size for cards/flyers; 150 dpi is fine for big posters
  and banners. Upscaling a small image doesn't add detail.
- **Colour**: print is CMYK. Neon RGB blues, greens and pinks print duller; warn the user.
  Brand-critical colour → ask the printer for their ICC profile and pass `--icc`.
- **Text**: ≥ 6 pt, lines ≥ 0.25 pt; embed fonts (or let `fix` rasterise).
- **Black**: large black areas look richer as "rich black", but keep total ink ≤ 300%.

Common sizes at 300 dpi including bleed:

| Product | Trim | Design at (px) |
|---|---|---|
| Business card 3.5×2 in | 88.9×50.8 mm | 1121×671 |
| Business card EU | 85×55 mm | 1075×720 |
| Flyer A5 | 148×210 mm | 1819×2551 |
| Flyer A4 | 210×297 mm | 2551×3579 |
| Poster A3 | 297×420 mm | 3579×5031 |
| Sticker 3×3 in | 76.2×76.2 mm | 947×947 |

## Limits (be honest with users)

- Without a printer ICC profile, CMYK conversion uses a neutral formula: safe, not
  colour-exact.
- The safe-zone check is a heuristic; always look at the proof.
- PDFs with unembedded fonts or wrong size are rasterised at print resolution (text stays
  sharp at 300 dpi but is no longer vector).
- Spot colours/Pantone, varnish/foil layers and die-cut lines aren't generated; ask the
  printer how they want those supplied.

See `references/print-basics.md` for plain-language explanations to share with users.
