# Print basics, in plain language

Use these explanations when a user asks "why?".

**Bleed.** Printers print on big sheets and cut them down. The blade can drift by a
millimetre or two. If your background stops exactly at the edge, a drift shows a thin
white line. So the background continues 3 mm past the edge ("bleed") and gets cut off.

**Trim / cut line.** The final size of the product, e.g. 3.5 × 2 in for a visiting card.

**Safe zone.** Because the cut can drift inward too, keep text and logos at least
3–5 mm inside the trim. Anything closer may get sliced.

**Crop marks.** Small lines outside the design that show the cutter where to cut. Many
online printers don't want them (they only want bleed); local print shops usually do.

**DPI (resolution).** How many pixels land on each inch of paper. 300 dpi looks sharp
at arm's length. A 1024 px wide image printed 3.5 in wide is ~290 dpi (fine); the same
image printed as an A4 poster is ~120 dpi (blurry).

**RGB vs CMYK.** Screens mix light (RGB) and can show very bright neon colours. Printers
mix ink (CMYK: cyan, magenta, yellow, black) and can't reach those. Bright blues, greens
and pinks print a little duller. That's normal; the proof gives an idea.

**ICC profile.** A file describing a specific press and paper, used for accurate colour
conversion. Ask your printer which one they use (common: ISO Coated v2 / FOGRA39 in
Europe and India, GRACoL / SWOP in the US).

**Embedded fonts.** If fonts aren't inside the PDF, the printer's computer swaps them
for others and the text changes. Exporting with "embed fonts" (or rasterising) fixes it.

**Imposition / n-up.** Print shops place many copies on one big sheet (e.g. 21 visiting
cards on SRA3) and cut them apart. `printready impose` does this layout.

**Total ink coverage.** Too much ink in dark areas (over ~300%) dries slowly and can
smudge. Use rich black sparingly.
