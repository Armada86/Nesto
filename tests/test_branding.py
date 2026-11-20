import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import branding


def test_header_has_logo_title_and_project_name_below():
    h = branding.header_html("2416-Cladding Above Canopy")
    assert "<svg" in h and ">Nesto<" in h and "2416-Cladding Above Canopy" in h
    assert h.index("nesto-title") < h.index("nesto-sub") < h.index("2416-Cladding")      # title first, project name below


def test_project_name_is_escaped_because_it_comes_from_the_user():
    h = branding.header_html('<script>alert(1)</script> & "x"')
    assert "<script>" not in h and "&lt;script&gt;" in h and "&amp;" in h


def test_header_without_subtitle_and_small_variant():
    assert "nesto-sub" not in branding.header_html("")
    assert "nesto-head small" in branding.header_html("2D nesting", size="small")


def test_favicon_is_a_64px_image_with_the_brand_colour():
    img = branding.favicon()
    assert img.size == (64, 64)
    assert img.getpixel((32, 3))[:3] == (22, 163, 74)           # the green tile (top edge, between the white parts)
    assert img.getpixel((0, 0))[3] == 0                         # rounded corner: transparent
    assert img.getpixel((20, 20))[:3] == (255, 255, 255)        # a white nested part
