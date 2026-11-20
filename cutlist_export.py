"""Excel cut list of a finished nest (.xlsx).

Tabs
  Summary     the settings, totals and one line per sheet
  Parts       every part: requested vs placed quantity, size, area, which sheets it is on
  Placements  one line per placed part: sheet, position, size of its bounding box, rotation, flipped
  Offcuts     the usable (regular) scrap pieces of every sheet
All lengths are in the units of the sheet size (normally mm); positions are the lower-left corner of a part's
bounding box measured from the lower-left corner of the sheet (x along the sheet length, y along its width).
"""
from __future__ import annotations

import io
from collections import Counter, OrderedDict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F6F43")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)


def _header(ws, row, titles):
    for c, t in enumerate(titles, 1):
        cell = ws.cell(row=row, column=c, value=t)
        cell.font, cell.fill, cell.border = HEADER_FONT, HEADER_FILL, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _put(ws, row, values, formats=None):
    for c, v in enumerate(values, 1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.border = BORDER
        if formats and formats[c - 1]:
            cell.number_format = formats[c - 1]


def _widths(ws, minimum=9, maximum=46):
    for col in ws.columns:
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(minimum, min(maximum, longest + 2))


def _fmt_dims(*vals):
    return " × ".join(f"{v:,.1f}".rstrip("0").rstrip(".") for v in vals)


def cutlist_xlsx(sheets, project="", sheet_length=None, sheet_width=None, margin=0.0, spacing=0.0,
                 seconds=None, parts=None, offcuts=None) -> bytes:
    """Build the workbook and return the .xlsx file as bytes.

    sheets   list of nesting.Sheet
    parts    optional project part list [{"name", "shape", "width", "height", "qty"}] - adds the requested quantity
             (and a warning colour where requested != placed)
    offcuts  optional [[(x, y, w, h), ...] per sheet] regular scrap pieces
    """
    wb = Workbook()
    n_sheets = len(sheets)
    sheet_area = (sheet_length or sheets[0].width) * (sheet_width or sheets[0].height) if sheets else 0
    placed_total = sum(len(s.placements) for s in sheets)
    used_total = sum(s.used_area for s in sheets)
    utilization = used_total / (sheet_area * n_sheets) if sheet_area and n_sheets else 0.0

    # ------------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"Cut list – {project}" if project else "Cut list"
    ws["A1"].font = TITLE_FONT
    rows = [
        ("Created", datetime.now().strftime("%Y-%m-%d %H:%M"), None),
        ("Sheet length (x)", sheet_length if sheet_length is not None else (sheets[0].width if sheets else None), "#,##0.0"),
        ("Sheet width (y)", sheet_width if sheet_width is not None else (sheets[0].height if sheets else None), "#,##0.0"),
        ("Edge margin", margin, "#,##0.0"),
        ("Part spacing (kerf)", spacing, "#,##0.0"),
        ("Sheets used", n_sheets, "0"),
        ("Parts placed", placed_total, "0"),
        ("Utilization", utilization, "0.00%"),
    ]
    if seconds is not None:
        rows.append(("Nesting time (s)", round(seconds, 1), "0.0"))
    for i, (k, v, f) in enumerate(rows, 3):
        a, b = ws.cell(row=i, column=1, value=k), ws.cell(row=i, column=2, value=v)
        a.font = Font(bold=True)
        a.border = b.border = BORDER
        b.alignment = Alignment(horizontal="right")
        if f:
            b.number_format = f
    r = 3 + len(rows) + 1
    ws.cell(row=r, column=1, value="Per sheet").font = Font(bold=True, size=12)
    _header(ws, r + 1, ["Sheet", "Parts", "Used area", "Utilization", "Largest offcut (L × W)"])
    for i, sh in enumerate(sheets, 1):
        best = max(offcuts[i - 1], key=lambda o: o[2] * o[3]) if offcuts and offcuts[i - 1] else None
        _put(ws, r + 1 + i, [i, len(sh.placements), sh.used_area, sh.used_area / (sh.width * sh.height),
                             _fmt_dims(best[2], best[3]) if best else "–"], [None, "0", "#,##0", "0.00%", None])
    _widths(ws)
    ws.column_dimensions["A"].width = 22

    # -------------------------------------------------------------------- Parts
    requested = OrderedDict()
    info = {}
    for p in parts or []:
        requested[p["name"]] = requested.get(p["name"], 0) + int(p["qty"])
        info.setdefault(p["name"], p)
    per_part = OrderedDict()                       # name -> Counter(sheet index -> count), total area
    for i, sh in enumerate(sheets, 1):
        for pl in sh.placements:
            d = per_part.setdefault(pl.part, {"sheets": Counter(), "area": 0.0, "n": 0})
            d["sheets"][i] += 1
            d["n"] += 1
            d["area"] += pl.area if pl.area is not None else pl.w * pl.h
    names = list(requested) + [n for n in per_part if n not in requested]
    wp = wb.create_sheet("Parts")
    _header(wp, 1, ["Part", "Shape", "Size A", "Size B", "Qty requested", "Qty placed", "Area each", "Total area",
                    "On sheets (sheet × qty)"])
    for r, name in enumerate(names, 2):
        d = per_part.get(name, {"sheets": Counter(), "area": 0.0, "n": 0})
        p = info.get(name, {})
        req = requested.get(name)
        _put(wp, r, [name, p.get("shape", ""), p.get("width"), p.get("height"), req, d["n"],
                     d["area"] / d["n"] if d["n"] else None, d["area"],
                     ", ".join(f"{s} × {c}" for s, c in sorted(d["sheets"].items()))],
             [None, None, "#,##0.0", "#,##0.0", "0", "0", "#,##0", "#,##0", None])
        if req is not None and req != d["n"] and req > 0:   # something is missing (or extra): make it visible
            for c in (5, 6):
                wp.cell(row=r, column=c).fill = BAD_FILL
    last = 1 + len(names)
    total_r = last + 1
    totals = ["Total", None, None, None, sum(requested.values()) if requested else None,
              sum(d["n"] for d in per_part.values()), None, sum(d["area"] for d in per_part.values()), None]
    _put(wp, total_r, totals, [None, None, None, None, "0", "0", None, "#,##0", None])
    for c in range(1, 10):                       # plain numbers, not formulas: previews on phones show formulas blank
        wp.cell(row=total_r, column=c).font = Font(bold=True)
    wp.freeze_panes = "A2"
    wp.auto_filter.ref = f"A1:I{last}"
    _widths(wp)

    # ---------------------------------------------------------------- Placements
    wl = wb.create_sheet("Placements")
    _header(wl, 1, ["Sheet", "Part", "X", "Y", "Box length (x)", "Box width (y)", "Rotation (°)", "Flipped", "Area"])
    r = 2
    for i, sh in enumerate(sheets, 1):
        for pl in sorted(sh.placements, key=lambda p: (round(p.y, 1), p.x)):
            _put(wl, r, [i, pl.part, pl.x, pl.y, pl.w, pl.h, pl.angle % 360, "yes" if getattr(pl, "mirrored", False) else "no",
                         pl.area if pl.area is not None else pl.w * pl.h],
                 ["0", None, "#,##0.0", "#,##0.0", "#,##0.0", "#,##0.0", "0.0", None, "#,##0"])
            r += 1
    wl.freeze_panes = "A2"
    wl.auto_filter.ref = f"A1:I{max(r - 1, 1)}"
    _widths(wl)

    # ------------------------------------------------------------------- Offcuts
    wo = wb.create_sheet("Offcuts")
    _header(wo, 1, ["Sheet", "X", "Y", "Length (x)", "Width (y)", "Area", "Squareness"])
    r = 2
    for i, rects in enumerate(offcuts or [], 1):
        for x, y, w, h in rects:
            _put(wo, r, [i, x, y, w, h, w * h, min(w, h) / max(w, h) if max(w, h) else 0.0],
                 ["0", "#,##0.0", "#,##0.0", "#,##0.0", "#,##0.0", "#,##0", "0.00"])
            r += 1
    if r == 2:
        wo.cell(row=2, column=1, value="No usable (rectangular, edge-touching) offcut on any sheet.")
    wo.freeze_panes = "A2"
    _widths(wo)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
