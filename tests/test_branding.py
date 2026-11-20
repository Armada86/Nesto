import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import branding


def test_header_has_logo_title_and_project_name_below():
    h = branding.header_html("2416-Cladding Above Canopy")
    assert "🧩" in h and ">Nesto<" in h and "2416-Cladding Above Canopy" in h
    assert h.index("nesto-title") < h.index("nesto-sub") < h.index("2416-Cladding")      # title first, project name below


def test_project_name_is_escaped_because_it_comes_from_the_user():
    h = branding.header_html('<script>alert(1)</script> & "x"')
    assert "<script>" not in h and "&lt;script&gt;" in h and "&amp;" in h


def test_header_without_subtitle_and_small_variant():
    assert "nesto-sub" not in branding.header_html("")
    assert "nesto-head small" in branding.header_html("2D nesting", size="small")


def test_the_logo_is_the_puzzle_piece_also_used_as_tab_icon():
    assert branding.LOGO == "🧩" and branding.PAGE_ICON == "🧩"
    h = branding.header_html("P")
    assert "🧩" in h and "<svg" not in h and h.index("🧩") < h.index("nesto-title")      # logo first, then the title
