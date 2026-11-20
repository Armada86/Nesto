"""The Nesto logo (the puzzle piece), the page header and the browser-tab icon."""
from __future__ import annotations

import html

LOGO = "🧩"
PAGE_ICON = LOGO

CSS = """
<style>
.nesto-head { display: flex; align-items: center; gap: 0.8rem; margin: 0.2rem 0 1.4rem; }
.nesto-head .nesto-logo { flex: 0 0 auto; font-size: 3.7rem; line-height: 1; }
.nesto-head .nesto-title { font-size: 2.7rem; font-weight: 800; line-height: 1.02; letter-spacing: -0.02em; }
.nesto-head .nesto-sub { font-size: 1.15rem; font-weight: 500; line-height: 1.25; opacity: .62; margin-top: .25rem;
                          overflow-wrap: anywhere; }
.nesto-head.small { gap: .5rem; margin: 0 0 .4rem; }
.nesto-head.small .nesto-logo { font-size: 1.9rem; }
.nesto-head.small .nesto-title { font-size: 1.45rem; }
.nesto-head.small .nesto-sub { font-size: .8rem; margin-top: .05rem; }
</style>
"""


def header_html(subtitle: str = "", size: str = "large") -> str:
    """The puzzle-piece logo and 'Nesto' in big letters, and below it `subtitle` (the project name) in a smaller,
    lighter font. The subtitle is escaped: it comes from the user."""
    small = size == "small"
    sub = f'<div class="nesto-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return (f'<div class="nesto-head{" small" if small else ""}"><span class="nesto-logo" role="img" '
            f'aria-label="Nesto logo">{LOGO}</span><div><div class="nesto-title">Nesto</div>{sub}</div></div>')
