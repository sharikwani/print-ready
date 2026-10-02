"""Imposition: lay out many copies of a print-ready page on a press sheet (n-up)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .preflight import PT_TO_MM, _box
from .specs import PT_PER_MM, SHEETS, SpecError

MARGIN_MM = 10.0


@dataclass
class ImposeResult:
    output: str
    sheet: str
    per_sheet: int
    columns: int
    rows: int
    item_rotated: bool
    sheet_rotated: bool
    usage_percent: float
    sheets_out: int

    def to_text(self) -> str:
        return (
            f"Imposed file: {self.output}\n"
            f"  {self.per_sheet}-up on {self.sheet.upper()} ({self.columns} × {self.rows}"
            f"{', item rotated' if self.item_rotated else ''}{', landscape sheet' if self.sheet_rotated else ''}), "
            f"{self.usage_percent:.0f}% of the sheet used, {self.sheets_out} sheet side(s)"
        )


def _fit(sheet_w, sheet_h, item_w, item_h, margin):
    cols = int((sheet_w - 2 * margin) // item_w)
    rows = int((sheet_h - 2 * margin) // item_h)
    return max(cols, 0), max(rows, 0)


def impose(print_pdf: str | Path, sheet: str = "sra3", output: str | Path | None = None,
           crop_marks: bool = True, copies: int | None = None) -> ImposeResult:
    """Step-and-repeat each page of a print-ready PDF across a press sheet.

    Every copy keeps its own bleed (bleed boxes touch), so each item gets two clean cuts.
    Page 2 (if any) is laid out mirrored left-to-right so fronts and backs line up for
    work-and-turn duplex printing.
    """
    import pikepdf

    key = sheet.lower().replace(" ", "")
    if key not in SHEETS:
        raise SpecError(f"Unknown sheet '{sheet}'. Options: {', '.join(SHEETS)}")
    sw, sh = (v * PT_PER_MM for v in SHEETS[key])
    src_path = Path(print_pdf)
    src = pikepdf.open(src_path)
    first = src.pages[0]
    media = _box(first, "/MediaBox")
    bleed = _box(first, "/BleedBox") or media
    trim = _box(first, "/TrimBox") or bleed
    iw, ih = bleed[2] - bleed[0], bleed[3] - bleed[1]
    margin = MARGIN_MM * PT_PER_MM

    best = None
    for sheet_rot in (False, True):
        W, H = (sh, sw) if sheet_rot else (sw, sh)
        for item_rot in (False, True):
            w, h = (ih, iw) if item_rot else (iw, ih)
            c, r = _fit(W, H, w, h, margin)
            n = c * r
            if best is None or n > best[0]:
                best = (n, c, r, item_rot, sheet_rot, W, H, w, h)
    n, cols, rows, item_rot, sheet_rot, W, H, w, h = best
    if n == 0:
        src.close()
        raise SpecError(f"The item ({iw * PT_TO_MM:.0f} × {ih * PT_TO_MM:.0f} mm) doesn't fit on {sheet.upper()}.")
    if copies is not None and copies < 1:
        src.close()
        raise SpecError("copies must be at least 1.")
    n_eff = min(n, copies) if copies else n

    out_pdf = pikepdf.new()
    gx = (W - cols * w) / 2
    gy = (H - rows * h) / 2
    for pidx, sp in enumerate(src.pages):
        fx = sp.as_form_xobject()
        fx.BBox = pikepdf.Array([float(v) for v in sp.obj.MediaBox])  # keep the bleed (qpdf defaults to TrimBox)
        form = out_pdf.copy_foreign(fx)
        ops = []
        placed = 0
        for r in range(rows):
            for c in range(cols):
                if placed >= n_eff:
                    break
                cc = (cols - 1 - c) if pidx % 2 == 1 else c
                x = gx + cc * w
                y = gy + (rows - 1 - r) * h
                # clip to this cell, then map the source bleed box onto the cell
                ops.append(f"q {x:.3f} {y:.3f} {w:.3f} {h:.3f} re W n")
                if item_rot and pidx % 2 == 1:
                    # back side of a work-and-turn sheet: rotate clockwise so the head stays aligned after turning
                    ops.append(f"0 -1 1 0 {x - bleed[1]:.3f} {y + bleed[2]:.3f} cm")
                elif item_rot:
                    # rotate 90° counter-clockwise: source (bx,by) -> cell
                    ops.append(f"0 1 -1 0 {x + w + bleed[1]:.3f} {y - bleed[0]:.3f} cm")
                else:
                    ops.append(f"1 0 0 1 {x - bleed[0]:.3f} {y - bleed[1]:.3f} cm")
                ops.append("/Fx0 Do Q")
                placed += 1
        if crop_marks:
            ops.append(_grid_marks(gx, gy, cols, rows, w, h, item_rot, trim, bleed, clockwise=pidx % 2 == 1))
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, W, H],
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fx0=form)),
            Contents=pikepdf.Stream(out_pdf, "\n".join(ops).encode()),
        )
        out_pdf.pages.append(pikepdf.Page(out_pdf.make_indirect(page)))
    out = Path(output) if output else src_path.with_name(f"{src_path.stem}-{key}-{n_eff}up.pdf")
    out_pdf.docinfo["/Creator"] = "print-ready impose"
    out_pdf.save(out)
    sheets = len(out_pdf.pages)
    out_pdf.close()
    src.close()
    tw, th = trim[2] - trim[0], trim[3] - trim[1]
    usage = n_eff * tw * th / (W * H) * 100
    return ImposeResult(str(out), key, n_eff, cols, rows, item_rot, sheet_rot, usage, sheets)


def _grid_marks(gx, gy, cols, rows, w, h, item_rot, trim, bleed, clockwise=False) -> str:
    """Cut marks along the sheet edges at every trim line."""
    # trim offsets inside a cell
    if item_rot and clockwise:
        left_off = trim[1] - bleed[1]
        bottom_off = bleed[2] - trim[2]
        tw, th = trim[3] - trim[1], trim[2] - trim[0]
    elif item_rot:
        left_off = bleed[3] - trim[3]
        bottom_off = trim[0] - bleed[0]
        tw, th = trim[3] - trim[1], trim[2] - trim[0]
    else:
        left_off = trim[0] - bleed[0]
        bottom_off = trim[1] - bleed[1]
        tw, th = trim[2] - trim[0], trim[3] - trim[1]
    xs = sorted({round(gx + c * w + left_off, 3) for c in range(cols)} | {round(gx + c * w + left_off + tw, 3) for c in range(cols)})
    ys = sorted({round(gy + r * h + bottom_off, 3) for r in range(rows)} | {round(gy + r * h + bottom_off + th, 3) for r in range(rows)})
    top, bottom = gy + rows * h, gy
    left, right = gx, gx + cols * w
    gap, ln = 2 * PT_PER_MM, 6 * PT_PER_MM
    segs = []
    for x in xs:
        segs.append((x, bottom - gap, x, bottom - gap - ln))
        segs.append((x, top + gap, x, top + gap + ln))
    for y in ys:
        segs.append((left - gap, y, left - gap - ln, y))
        segs.append((right + gap, y, right + gap + ln, y))
    body = " ".join(f"{a:.3f} {b:.3f} m {c:.3f} {d:.3f} l S" for a, b, c, d in segs)
    return f"q 0.25 w 1 1 1 1 K {body} Q"
