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
.st-key-camming_save, .st-key-tools_save, .st-key-data_save, .st-key-levels_save { width: 9rem !important; max-width: 9rem !important; }
.st-key-camming_save button, .st-key-tools_save button, .st-key-data_save button, .st-key-levels_save button {
    width: 9rem !important; min-height: 2.6rem !important; }
/* the tool picture stays beside the tool parameters while the page is scrolled */
.st-key-tools_guide { position: sticky; top: 4rem; }
/* on a phone Streamlit stacks columns; these rows stay side by side */
@media (max-width: 640px) {
  .st-key-lv_rows [data-testid="stHorizontalBlock"], .st-key-order_btns [data-testid="stHorizontalBlock"],
  .st-key-ops_btns [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.4rem !important; }
  .st-key-lv_rows [data-testid="stColumn"], .st-key-order_btns [data-testid="stColumn"], .st-key-ops_btns [data-testid="stColumn"] {
      min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
  .st-key-lv_rows [data-testid="stColumn"]:first-child { flex: 1 1 48% !important; }       /* the box */
  .st-key-lv_rows [data-testid="stColumn"]:last-child { flex: 1 1 52% !important; font-size: 0.9rem; }   /* its explanation */
  .st-key-tools_params [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }
  .st-key-tools_params [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
  .st-key-tools_params label p { font-size: 0.8rem !important; line-height: 1.15 !important; }
  .st-key-tools_params input { padding-left: 0.5rem !important; padding-right: 0.2rem !important; }
  .st-key-lib_list [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.3rem !important; }
  .st-key-lib_list [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
  .st-key-lib_list [data-testid="stColumn"]:first-child { flex: 0 0 2rem !important; }
  .st-key-lib_list [data-testid="stColumn"]:last-child { flex: 1 1 auto !important; }
  .st-key-order_btns button, .st-key-ops_btns button { padding: 0.25rem 0.15rem !important; min-height: 2.5rem !important; }
  .st-key-order_btns button p, .st-key-ops_btns button p { font-size: 0.72rem !important; line-height: 1.15 !important; white-space: normal !important;
      text-align: center; }
}
</style>
"""


def header_html(subtitle: str = "", size: str = "large") -> str:
    """The puzzle-piece logo and 'Nesto' in big letters, and below it `subtitle` (the project name) in a smaller,
    lighter font. The subtitle is escaped: it comes from the user."""
    small = size == "small"
    sub = f'<div class="nesto-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return (f'<div class="nesto-head{" small" if small else ""}"><span class="nesto-logo" role="img" '
            f'aria-label="Nesto logo">{LOGO}</span><div><div class="nesto-title">Nesto</div>{sub}</div></div>')
