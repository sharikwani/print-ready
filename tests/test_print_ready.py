from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from print_ready import imageops as io
from print_ready.build import make_print_ready
from print_ready.cli import main as cli
from print_ready.impose import impose
from print_ready.preflight import check_file
from print_ready.proof import make_proof
from print_ready.specs import PT_PER_MM, SpecError, get_product

from . import samples


@pytest.fixture(scope="module")
def art(tmp_path_factory):
    return samples.make_all(tmp_path_factory.mktemp("art"))


def codes(rep, level=None):
    return {i.code for i in rep.issues if level is None or i.level == level}


def boxes_mm(pdf_path, page=0):
    with pikepdf.open(pdf_path) as pdf:
        p = pdf.pages[page]
        return {k: [float(v) / PT_PER_MM for v in p.obj[k]] for k in ("/MediaBox", "/TrimBox", "/BleedBox")}


# ---------------------------------------------------------------- specs

def test_products_and_aliases():
    bc = get_product("visiting card")
    assert bc.id == "business_card"
    assert bc.pixels() == (1121, 671)
    assert get_product("A5").id == "flyer_a5"
    custom = get_product(width_mm=100, height_mm=50, bleed_mm=2)
    assert (custom.width_mm, custom.height_mm, custom.bleed_mm) == (100, 50, 2)
    with pytest.raises(SpecError):
        get_product("spaceship")


# ---------------------------------------------------------------- preflight

def test_ai_card_needs_bleed_and_warns_safe_zone(art):
    rep = check_file(art["card_png"], get_product("business_card"))
    assert {"NO_BLEED", "SAFE_ZONE", "VIVID_COLORS"} <= codes(rep, "warning")
    assert "LOW_RES" not in codes(rep)
    assert rep.verdict == "needs_attention"  # safe zone needs a human/design decision


def test_low_res_is_an_error(art):
    rep = check_file(art["flyer_jpg"], get_product("flyer_a5"))
    assert "LOW_RES" in codes(rep, "error")
    assert rep.verdict == "needs_attention"


def test_pdf_without_bleed_and_unembedded_standard_font(art):
    rep = check_file(art["pdf_nobleed"], get_product("business_card"))
    assert "NO_BLEED" in codes(rep, "warning")
    assert "FONTS" in codes(rep, "warning")  # standard-14 fonts → warning, not error
    assert rep.verdict == "fixable"


def test_good_pdf_is_ready(art):
    rep = check_file(art["pdf_good"], get_product("business_card"))
    assert rep.verdict == "ready", rep.to_text()


def test_transparent_sticker_needs_no_bleed(art):
    rep = check_file(art["sticker_png"], get_product("sticker_2in"))
    assert rep.verdict == "ready", rep.to_text()


def test_orientation_auto_rotates(tmp_path):
    p = tmp_path / "portrait.png"
    Image.new("RGB", (671, 1121), "white").save(p)
    rep = check_file(p, get_product("business_card"))
    assert "ORIENTATION" in codes(rep)
    assert "ASPECT" not in codes(rep)


def test_report_json_roundtrip(art):
    d = check_file(art["card_png"], get_product("business_card")).to_dict()
    assert json.loads(json.dumps(d))["verdict"] == "needs_attention"


# ---------------------------------------------------------------- build

def test_fix_image_produces_correct_boxes_and_cmyk(art, tmp_path):
    out = tmp_path / "card.pdf"
    res = make_print_ready([art["card_png"]], get_product("business_card"), out)
    b = boxes_mm(out)
    trim_w = b["/TrimBox"][2] - b["/TrimBox"][0]
    trim_h = b["/TrimBox"][3] - b["/TrimBox"][1]
    assert abs(trim_w - 88.9) < 0.05 and abs(trim_h - 50.8) < 0.05
    assert abs((b["/TrimBox"][0] - b["/BleedBox"][0]) - 3) < 0.05
    assert res.crop_marks
    with pikepdf.open(out) as pdf:
        img = pdf.pages[0].obj.Resources.XObject.Im0
        assert img.ColorSpace == "/DeviceCMYK"
    # the fixed file passes preflight
    assert check_file(out, get_product("business_card")).verdict == "ready"


def test_fixed_file_preserves_colour_roughly(art, tmp_path):
    """Render the CMYK output and make sure it still looks like the original (no inverted CMYK etc.)."""
    out = tmp_path / "c.pdf"
    make_print_ready([art["card_png"]], get_product("business_card"), out, crop_marks=False)
    doc = pdfium.PdfDocument(str(out))
    rendered = doc[0].render(scale=2).to_pil().convert("RGB")
    doc.close()
    src = Image.open(art["card_png"]).convert("RGB")
    # sample the centre-left (dark navy background) in both
    a = src.getpixel((30, src.height // 2))
    bw, bh = rendered.size
    b = rendered.getpixel((int(bw * 0.06), bh // 2))
    assert sum(abs(x - y) for x, y in zip(a, b, strict=True)) < 90, (a, b)


@pytest.mark.parametrize("jpeg", [None, 90])
def test_jpeg_cmyk_not_inverted(art, tmp_path, jpeg):
    out = tmp_path / f"j{jpeg}.pdf"
    make_print_ready([art["flyer_jpg"]], get_product("flyer_a5"), out, crop_marks=False, jpeg_quality=jpeg)
    doc = pdfium.PdfDocument(str(out))
    im = doc[0].render(scale=1).to_pil().convert("RGB")
    doc.close()
    top = im.getpixel((im.width // 2, int(im.height * 0.15)))  # red header
    assert top[0] > 150 and top[1] < 120, top


def test_vector_passthrough_keeps_text_vector(art, tmp_path):
    out = tmp_path / "v.pdf"
    res = make_print_ready([art["pdf_good"]], get_product("business_card"), out)
    assert "vector" in res.color
    with pikepdf.open(out) as pdf:
        assert '/Im0' not in pdf.pages[0].obj.Resources.get('/XObject', {})  # nothing rasterised
    # bleed must survive (qpdf form xobjects default to the TrimBox)
    doc = pdfium.PdfDocument(str(out))
    im = doc[0].render(scale=3).to_pil().convert("RGB")
    doc.close()
    b = boxes_mm(out)
    x = int((b["/BleedBox"][0] + 1) * PT_PER_MM * 3)
    y = im.height // 2
    assert im.getpixel((x, y)) != (255, 255, 255)


def test_unembedded_fonts_get_rasterised(art, tmp_path):
    out = tmp_path / "r.pdf"
    res = make_print_ready([art["pdf_nobleed"]], get_product("business_card"), out)
    assert any("rasterised" in a for a in res.actions)
    assert check_file(out, get_product("business_card")).verdict == "ready"


def test_front_back(art, tmp_path):
    out = tmp_path / "duplex.pdf"
    res = make_print_ready([art["card_png"], art["pdf_good"]], get_product("business_card"), out)
    assert res.pages == 2


def test_contain_fit_pads(tmp_path):
    p = tmp_path / "square.png"
    Image.new("RGB", (900, 900), (255, 0, 0)).save(p)
    res = make_print_ready([p], get_product("business_card"), tmp_path / "o.pdf", fit="contain")
    assert any("padded" in a for a in res.actions)


def test_transparent_sticker_png_output(art, tmp_path):
    res = make_print_ready([art["sticker_png"]], get_product("sticker_2in"), tmp_path / "s.pdf")
    assert res.png_output and Path(res.png_output).exists()
    assert io.has_alpha(Image.open(res.png_output))


def test_refuses_to_overwrite_input(art):
    with pytest.raises(io.ArtworkError):
        make_print_ready([art["pdf_good"]], get_product("business_card"), art["pdf_good"])


def test_icc_profile_conversion_and_output_intent(art, tmp_path):
    from PIL import ImageCms

    import os

    candidates = [Path(os.environ["PRINT_READY_TEST_ICC"])] if os.environ.get("PRINT_READY_TEST_ICC") else []
    for root in ("/usr/share/color/icc", "/usr/share/texlive/texmf-dist/tex/generic/colorprofiles"):
        if Path(root).exists():
            candidates += [c for c in Path(root).rglob("*") if c.suffix.lower() in (".icc", ".icm")]
    cmyk = []
    for c in candidates:
        try:
            if ImageCms.getOpenProfile(str(c)).profile.xcolor_space.strip() == "CMYK":
                cmyk.append(c)
        except Exception:  # noqa: BLE001
            pass
    if not cmyk:
        pytest.skip("no CMYK ICC profile installed on this machine")
    out = tmp_path / "icc.pdf"
    res = make_print_ready([art["card_png"]], get_product("business_card"), out, icc_path=str(cmyk[0]))
    assert res.color.startswith("ICC")
    with pikepdf.open(out) as pdf:
        assert "/OutputIntents" in pdf.Root


# ---------------------------------------------------------------- bleed helpers

def test_mirror_bleed_dimensions():
    im = Image.new("RGB", (100, 50), (10, 20, 30))
    out = io.add_bleed(im, 7, 5, "mirror")
    assert out.size == (114, 60)
    assert out.getpixel((0, 0)) == (10, 20, 30)
    ext = io.add_bleed(im, 7, 5, "extend")
    assert ext.size == (114, 60)


def test_gcr_black_goes_to_k():
    im = Image.new("RGB", (4, 4), (0, 0, 0))
    c, m, y, k = io.rgb_to_cmyk(im)[0].getpixel((0, 0))
    assert (c, m, y, k) == (0, 0, 0, 255)


# ---------------------------------------------------------------- proof & impose

def test_proof_png(art, tmp_path):
    pdf = tmp_path / "p.pdf"
    make_print_ready([art["card_png"]], get_product("business_card"), pdf)
    png = make_proof(pdf, get_product("business_card"))
    im = Image.open(png)
    assert im.width > 1000
    assert im.getpixel((2, 2))[0] > im.getpixel((2, 2))[2] - 40  # red-tinted bleed corner


def test_impose_sra3(art, tmp_path):
    pdf = tmp_path / "card.pdf"
    make_print_ready([art["card_png"]], get_product("business_card"), pdf)
    res = impose(pdf, "sra3")
    assert res.per_sheet == 21
    with pikepdf.open(res.output) as out:
        w = float(out.pages[0].obj.MediaBox[2]) / PT_PER_MM
        h = float(out.pages[0].obj.MediaBox[3]) / PT_PER_MM
        assert {round(w), round(h)} == {320, 450}


def test_impose_duplex_two_sheets(art, tmp_path):
    pdf = tmp_path / "d.pdf"
    make_print_ready([art["card_png"], art["pdf_good"]], get_product("business_card"), pdf)
    assert impose(pdf, "a4").sheets_out == 2


# ---------------------------------------------------------------- CLI

def test_cli_end_to_end(art, tmp_path, capsys):
    assert cli(["products", "--category", "cards"]) == 0
    assert cli(["check", str(art["pdf_good"]), "-p", "business_card"]) == 0
    assert cli(["check", str(art["flyer_jpg"]), "-p", "a5"]) == 1
    capsys.readouterr()
    out = tmp_path / "cli.pdf"
    assert cli(["fix", str(art["card_png"]), "-p", "business_card", "-o", str(out), "--proof", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pages"] == 1 and data["proof"].endswith("cli-proof.png")
    assert out.exists() and out.with_name("cli-proof.png").exists()
    assert cli(["impose", str(out), "--sheet", "a4"]) == 0
    assert cli(["check", "nope.png", "-p", "business_card"]) == 2


def test_quote_without_config(monkeypatch, capsys):
    monkeypatch.delenv("PRINTQUOTE_MCP_URL", raising=False)
    assert cli(["quote", "-p", "business_card", "-q", "500"]) == 2
    assert "PRINTQUOTE_MCP_URL" in capsys.readouterr().err


# ---------------------------------------------------------------- MCP

def test_mcp_tools_registered():
    import asyncio

    from print_ready.mcp_server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"list_print_products", "design_spec", "preflight_check", "make_print_ready", "make_proof",
            "impose_sheet", "get_print_quote"} <= tools


def test_mcp_tool_calls(art, tmp_path):
    from print_ready import mcp_server as m

    assert "business_card" in m.list_print_products("cards")
    assert m.design_spec("business_card")["design_pixels_with_bleed"].startswith("1121 x 671")
    assert "Verdict" in m.preflight_check(str(art["card_png"]), "business_card")
    out = tmp_path / "m.pdf"
    assert "Print-ready file" in m.make_print_ready([str(art["card_png"])], "business_card", output_path=str(out))
    proof = m.make_proof(str(out))
    assert "Proof saved" in proof[0]
    assert "ERROR" in m.preflight_check("missing.png", "business_card")


def test_render_html_design(tmp_path):
    """HTML is what AI assistants usually produce; runs only where Playwright + Chromium are available."""
    pytest.importorskip("playwright")
    import os

    exe = os.environ.get("PRINT_READY_CHROMIUM")
    if not exe:
        for cand in ("/opt/pw-browsers/chromium",):
            if Path(cand).exists():
                os.environ["PRINT_READY_CHROMIUM"] = cand
                break
    html = tmp_path / "card.html"
    html.write_text("<html><body style='margin:0;background:#123;color:#fff;font:30px serif;padding:40px'>Hi</body></html>")
    try:
        rep = check_file(html, get_product("business_card"))
    except Exception as e:  # noqa: BLE001 - browser not installed
        pytest.skip(f"no browser: {e}")
    assert rep.details["pixels"] == "1050 × 600"
    res = make_print_ready([html], get_product("business_card"), tmp_path / "h.pdf")
    assert res.pages == 1


# ---------------------------------------------------------------- regressions (from code review)

def test_cmyk_input_builds(tmp_path):
    p = tmp_path / "c.tif"
    Image.new("CMYK", (1000, 700), (10, 80, 90, 0)).save(p)
    res = make_print_ready([p], get_product("business_card_eu"), tmp_path / "c.pdf")
    assert res.pages == 1


def test_rasterizing_print_pdf_drops_old_slug(art, tmp_path):
    first = tmp_path / "first.pdf"
    make_print_ready([art["pdf_good"]], get_product("business_card"), first)  # vector, with marks + slug
    again = tmp_path / "again.pdf"
    res = make_print_ready([first], get_product("business_card"), again, keep_vector=False)
    assert not any("cropped" in a for a in res.actions), res.actions
    doc = pdfium.PdfDocument(str(again))
    im = doc[0].render(scale=3).to_pil().convert("RGB")
    doc.close()
    b = boxes_mm(again)
    x = int((b["/TrimBox"][0] + 2) * PT_PER_MM * 3)
    assert im.getpixel((x, im.height // 2)) != (255, 255, 255)


def test_sticker_png_never_overwrites_input(tmp_path):
    src = samples.sticker_with_alpha(tmp_path / "sticker.png")
    before = Image.open(src).size
    res = make_print_ready([src], get_product("sticker_3in"), tmp_path / "sticker.pdf")
    assert Image.open(src).size == before
    assert res.png_output.endswith("sticker-print.png")


def test_16bit_grey_png(tmp_path):
    import numpy as np

    p = tmp_path / "g16.png"
    Image.fromarray(np.full((600, 1050), 32768, dtype=np.uint16)).save(p)
    out = tmp_path / "g.pdf"
    make_print_ready([p], get_product("business_card"), out, crop_marks=False)
    doc = pdfium.PdfDocument(str(out))
    px = doc[0].render(scale=1).to_pil().convert("RGB").getpixel((50, 50))
    doc.close()
    assert 90 < px[0] < 170, px


def test_rotated_pdf_page_preflight(tmp_path):
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    p = tmp_path / "rot.pdf"
    c = canvas.Canvas(str(p), pagesize=(61 * mm, 91 * mm))
    c.rect(0, 0, 61 * mm, 91 * mm, fill=1)
    c.showPage()
    c.save()
    with pikepdf.open(p, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(p)
    rep = check_file(p, get_product("business_card_eu"))
    assert not {"SIZE", "NO_BLEED"} & codes(rep, "warning"), rep.to_text()


def test_reused_form_and_inline_image_resolution(tmp_path):
    p = tmp_path / "forms.pdf"
    pdf = pikepdf.new()
    img = pikepdf.Stream(pdf, bytes([128] * 30 * 30), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                         Width=30, Height=30, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8)
    form = pikepdf.Stream(pdf, b"q 1 0 0 1 0 0 cm /Im0 Do Q", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                          BBox=[0, 0, 1, 1], Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)))
    content = b"q 8 0 0 8 0 0 cm /Fx Do Q q 200 0 0 150 10 10 cm /Fx Do Q"
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 252, 144],
                              Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fx=form)),
                              Contents=pikepdf.Stream(pdf, content))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(p)
    rep = check_file(p, get_product("business_card"))
    assert "LOW_RES" in codes(rep, "error"), rep.to_text()

    p2 = tmp_path / "inline.pdf"
    pdf2 = pikepdf.new()
    inline = b"q 252 0 0 144 0 0 cm BI /W 10 /H 10 /CS /RGB /BPC 8 ID " + bytes([200] * 300) + b" EI Q"
    page2 = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 252, 144], Contents=pikepdf.Stream(pdf2, inline))
    pdf2.pages.append(pikepdf.Page(pdf2.make_indirect(page2)))
    pdf2.save(p2)
    rep2 = check_file(p2, get_product("business_card"))
    assert "LOW_RES" in codes(rep2, "error"), rep2.to_text()


def test_rgb_via_cs_operator_detected(tmp_path):
    p = tmp_path / "cs.pdf"
    pdf = pikepdf.new()
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 252, 144],
                              Contents=pikepdf.Stream(pdf, b"/DeviceRGB cs 1 0 0 sc 0 0 252 144 re f"))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(p)
    assert "RGB" in codes(check_file(p, get_product("business_card")))


def test_portrait_back_is_rotated_not_cropped(tmp_path):
    front, back = tmp_path / "f.png", tmp_path / "b.png"
    Image.new("RGB", (1004, 650), "navy").save(front)
    Image.new("RGB", (650, 1004), "orange").save(back)
    res = make_print_ready([front, back], get_product("business_card_eu"), tmp_path / "fb.pdf")
    assert any("rotated 90" in a for a in res.actions)
    assert not any("cropped" in a for a in res.actions), res.actions


def test_impose_rejects_bad_copies_and_rotated_duplex(tmp_path):
    from print_ready.specs import SpecError as SE

    f, b = tmp_path / "f.png", tmp_path / "b.png"
    Image.new("RGB", (1100, 1700), "red").save(f)
    Image.new("RGB", (1100, 1700), "blue").save(b)
    pdf = tmp_path / "tall.pdf"
    make_print_ready([f, b], get_product(width_mm=90, height_mm=140), pdf)
    res = impose(pdf, "sra3")
    assert res.item_rotated and res.sheets_out == 2
    with pytest.raises(SE):
        impose(pdf, "sra3", copies=-1)


def test_width_without_height_is_an_error():
    with pytest.raises(SpecError):
        get_product("business_card", width_mm=100)
    assert get_product("card", width_mm=100, height_mm=60).safe_mm == get_product("business_card").safe_mm


def test_cli_friendly_errors(tmp_path, capsys):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert cli(["check", str(bad), "-p", "business_card"]) == 2
    assert cli(["impose", str(tmp_path / "missing.pdf")]) == 2
    assert cli(["proof", str(bad), "--page", "0"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_nested_form_bomb_is_fast(tmp_path):
    import time

    pdf = pikepdf.new()
    img = pikepdf.Stream(pdf, bytes([1] * 4), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
                         Width=2, Height=2, ColorSpace=pikepdf.Name.DeviceGray, BitsPerComponent=8)
    child = pikepdf.Stream(pdf, b"/Im0 Do", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 1, 1],
                           Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)))
    for _ in range(8):
        body = b" ".join(f"q 1 0 0 1 {i} 0 cm /C Do Q".encode() for i in range(10))
        child = pikepdf.Stream(pdf, body, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 1, 1],
                               Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(C=child)))
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 252, 144],
                              Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(C=child)),
                              Contents=pikepdf.Stream(pdf, b"/C Do"))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    p = tmp_path / "bomb.pdf"
    pdf.save(p)
    t = time.time()
    check_file(p, get_product("business_card"))
    assert time.time() - t < 5


def test_cropbox_does_not_eat_bleed(art, tmp_path):
    src = tmp_path / "cropped.pdf"
    make_print_ready([art["pdf_good"]], get_product("business_card"), src)  # has slug + marks
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        mb = [float(v) for v in pdf.pages[0].obj.MediaBox]
        inset = 2 * PT_PER_MM
        pdf.pages[0].obj.CropBox = [mb[0] + inset, mb[1] + inset, mb[2] - inset, mb[3] - inset]
        pdf.save(src)
    out = tmp_path / "re.pdf"
    res = make_print_ready([src], get_product("business_card"), out, keep_vector=False)
    assert any("already includes bleed" in a for a in res.actions), res.actions
