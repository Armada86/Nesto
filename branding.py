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
/* the colours of the logo: green and light blue */
:root { --nesto-green: #2b8a3e; --nesto-green-dark: #237032; --nesto-blue: #7cc4ee; --nesto-blue-light: #e6f3fb; }
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stBaseButton-secondaryFormSubmit"]:hover {
    border-color: var(--nesto-green) !important; color: var(--nesto-green) !important; background: var(--nesto-blue-light) !important; }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { background-color: var(--nesto-green-dark) !important;
    border-color: var(--nesto-green-dark) !important; }
/* the Save buttons of the NestoCAM steps (Geometry, Tools, Data, Levels): exactly the same size and colour */
.st-key-camming_save_thickness, .st-key-tools_save, .st-key-data_save, .st-key-levels_save { width: 9rem !important; max-width: 9rem !important; }
.st-key-camming_save_thickness button, .st-key-tools_save button, .st-key-data_save button, .st-key-levels_save button {
    width: 9rem !important; min-height: 2.6rem !important; }
</style>
"""


def header_html(subtitle: str = "", size: str = "large") -> str:
    """The puzzle-piece logo and 'Nesto' in big letters, and below it `subtitle` (the project name) in a smaller,
    lighter font. The subtitle is escaped: it comes from the user."""
    small = size == "small"
    sub = f'<div class="nesto-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return (f'<div class="nesto-head{" small" if small else ""}"><span class="nesto-logo" role="img" '
            f'aria-label="Nesto logo">{LOGO}</span><div><div class="nesto-title">Nesto</div>{sub}</div></div>')
