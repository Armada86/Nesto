"""Read part geometry from DXF drawings (one part per file)."""
from __future__ import annotations

import io

import ezdxf
from ezdxf.path import make_path


def read_dxf(data: bytes, flatten: float = 0.1):
    """Return (width, height, outline) from DXF bytes.

    outline is a list of polylines with the bounding-box lower-left moved to (0, 0).
    Units are whatever the drawing uses (usually mm).
    """
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="ignore")))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Cannot read DXF: {e}") from e
    msp = doc.modelspace()
    polylines = []
    for e in msp:
        if e.dxftype() in {"LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}:
            try:
                pts = [(v.x, v.y) for v in make_path(e).flattening(flatten)]
            except Exception:  # noqa: BLE001
                continue
            if len(pts) >= 2:
                polylines.append(pts)
    if not polylines:
        raise ValueError("No supported geometry found (LINE, ARC, CIRCLE, POLYLINE, SPLINE...)")
    xs = [x for pl in polylines for x, _ in pl]
    ys = [y for pl in polylines for _, y in pl]
    x0, y0 = min(xs), min(ys)
    outline = [[(x - x0, y - y0) for x, y in pl] for pl in polylines]
    return max(xs) - x0, max(ys) - y0, outline
