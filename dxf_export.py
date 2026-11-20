"""Export nested sheets to DXF (one drawing per sheet, or all sheets side by side)."""
from __future__ import annotations

import io
import zipfile

import ezdxf
from ezdxf.enums import TextEntityAlignment

SHEET_LAYER, CUT_LAYER, LABEL_LAYER = "SHEET", "CUT", "LABELS"


def _new_doc():
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    doc.layers.add(SHEET_LAYER, color=8)   # grey
    doc.layers.add(CUT_LAYER, color=7)     # white/black
    doc.layers.add(LABEL_LAYER, color=3)   # green
    return doc


def _draw_sheet(msp, sheet, dx=0.0, dy=0.0, labels=True):
    msp.add_lwpolyline([(dx, dy), (dx + sheet.width, dy), (dx + sheet.width, dy + sheet.height),
                        (dx, dy + sheet.height)], close=True, dxfattribs={"layer": SHEET_LAYER})
    for p in sheet.placements:
        if p.polys:
            rings = [r for poly in p.polys for r in [poly["ext"], *poly.get("holes", [])]]
        else:  # plain rectangle from the rectangle engine
            rings = [[(p.x, p.y), (p.x + p.w, p.y), (p.x + p.w, p.y + p.h), (p.x, p.y + p.h)]]
        for ring in rings:
            pts = [(x + dx, y + dy) for x, y in ring]
            if len(pts) > 1 and tuple(pts[0]) == tuple(pts[-1]):
                pts = pts[:-1]  # polyline is closed by the flag
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": CUT_LAYER})
        if labels:
            height = max(5.0, min(40.0, min(p.w, p.h) * 0.08))
            msp.add_text(p.part, height=height, dxfattribs={"layer": LABEL_LAYER}).set_placement(
                (p.x + p.w / 2 + dx, p.y + p.h / 2 + dy), align=TextEntityAlignment.MIDDLE_CENTER)


def _to_bytes(doc) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def sheet_dxf(sheet, labels=True) -> bytes:
    doc = _new_doc()
    _draw_sheet(doc.modelspace(), sheet, labels=labels)
    return _to_bytes(doc)


def all_sheets_dxf(sheets, gap=300.0, labels=True) -> bytes:
    """One DXF with the sheets stacked vertically (sheet 1 on top) and "Sheet N" beside each."""
    doc = _new_doc()
    msp = doc.modelspace()
    y = 0.0
    for i, s in enumerate(sheets, 1):
        dy = y - s.height  # top edge of this sheet at y
        _draw_sheet(msp, s, dy=dy, labels=labels)
        title_h = min(150.0, max(40.0, s.height * 0.12))
        msp.add_text(f"Sheet {i}", height=title_h, dxfattribs={"layer": SHEET_LAYER}).set_placement(
            (-title_h, dy + s.height / 2), align=TextEntityAlignment.MIDDLE_RIGHT)
        y = dy - gap
    return _to_bytes(doc)


def sheets_zip(sheets, prefix="sheet", labels=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, s in enumerate(sheets, 1):
            z.writestr(f"{prefix}_{i:02d}.dxf", sheet_dxf(s, labels))
    return buf.getvalue()
