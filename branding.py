"""The Nesto logo (parts nested on a sheet), the page header and the browser-tab icon."""
from __future__ import annotations

import html

GREEN = "#16a34a"

# viewBox 64 x 64: a green rounded tile with a white sheet outline and four parts nested inside it
_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{s}" height="{s}" role="img" aria-label="Nesto logo">'
    f'<rect width="64" height="64" rx="15" fill="{GREEN}"/>'
    '<rect x="9" y="9" width="46" height="46" rx="3" fill="none" stroke="#fff" stroke-width="2.4"/>'
    '<rect x="15" y="15" width="21" height="13" rx="1.5" fill="#fff"/>'
    '<circle cx="46" cy="22" r="7.5" fill="#fff"/>'
    '<polygon points="15,49 33,49 15,32" fill="#fff"/>'
    '<polygon points="49,49 49,33 36,49" fill="#fff"/>'
    "</svg>"
)

CSS = """
<style>
.nesto-head { display: flex; align-items: center; gap: 0.9rem; margin: 0.2rem 0 1.4rem; }
.nesto-head svg { flex: 0 0 auto; filter: drop-shadow(0 2px 4px rgba(0, 0, 0, .18)); }
.nesto-head .nesto-title { font-size: 2.7rem; font-weight: 800; line-height: 1.02; letter-spacing: -0.02em; }
.nesto-head .nesto-sub { font-size: 1.15rem; font-weight: 500; line-height: 1.25; opacity: .62; margin-top: .25rem;
                          overflow-wrap: anywhere; }
.nesto-head.small { gap: .6rem; margin: 0 0 .4rem; }
.nesto-head.small .nesto-title { font-size: 1.45rem; }
.nesto-head.small .nesto-sub { font-size: .8rem; margin-top: .05rem; }
</style>
"""


def logo_svg(size: int = 56) -> str:
    return _LOGO.format(s=size)


def header_html(subtitle: str = "", size: str = "large") -> str:
    """Logo + 'Nesto' in big letters, and below it `subtitle` (the project name) in a smaller, lighter font.
    The subtitle is escaped: it comes from the user."""
    small = size == "small"
    sub = f'<div class="nesto-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return (f'<div class="nesto-head{" small" if small else ""}">{logo_svg(30 if small else 62)}'
            f'<div><div class="nesto-title">Nesto</div>{sub}</div></div>')


def favicon():
    """The logo as a PIL image for the browser tab."""
    from PIL import Image, ImageDraw
    k = 4                                            # draw at 4x, then shrink: smooth edges
    img = Image.new("RGBA", (64 * k, 64 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 64 * k - 1, 64 * k - 1], radius=15 * k, fill=GREEN)
    d.rounded_rectangle([9 * k, 9 * k, 55 * k, 55 * k], radius=3 * k, outline="white", width=int(2.4 * k))
    d.rounded_rectangle([15 * k, 15 * k, 36 * k, 28 * k], radius=int(1.5 * k), fill="white")
    d.ellipse([(46 - 7.5) * k, (22 - 7.5) * k, (46 + 7.5) * k, (22 + 7.5) * k], fill="white")
    d.polygon([(15 * k, 49 * k), (33 * k, 49 * k), (15 * k, 32 * k)], fill="white")
    d.polygon([(49 * k, 49 * k), (49 * k, 33 * k), (36 * k, 49 * k)], fill="white")
    return img.resize((64, 64), Image.LANCZOS)
