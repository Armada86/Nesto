"""Streamlit wrapper of the draggable sheet editor (components/sheet_editor)."""
from __future__ import annotations

import pathlib

import matplotlib
import streamlit.components.v1 as components

from layout_edit import hold_rect, placement_geom
from shapes import geom_to_json

_DIR = pathlib.Path(__file__).parent / "components" / "sheet_editor"
_component = components.declare_component("nesto_sheet_editor", path=str(_DIR))


def palette(names):
    """A colour per part name (the same tab20 colours as the static drawings)."""
    cmap = matplotlib.colormaps["tab20"]
    order = {}
    for n in names:
        order.setdefault(n, len(order))
    return {n: matplotlib.colors.to_hex(cmap(i % 20)) for n, i in order.items()}


def parts_payload(sheet: dict) -> list[dict]:
    colors = palette([p["part"] for p in sheet["placements"]])
    out = []
    for p in sheet["placements"]:
        polys = p.get("polys") or geom_to_json(placement_geom(p))
        out.append({"name": p["part"], "polys": polys, "color": colors[p["part"]]})
    return out


def sheet_editor(sheet: dict, margin: float, spacing: float, scrap, rev: int, key: str):
    """Show one sheet whose parts can be dragged. Returns the last reported move
    {"id", "rev", "part", "dx", "dy"} (or None)."""
    return _component(width=sheet["width"], height=sheet["height"], margin=margin, spacing=spacing,
                      parts=parts_payload(sheet), hold=list(hold_rect(sheet)), scrap=[list(r) for r in scrap], rev=rev, key=key, default=None)
