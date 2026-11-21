/* Collision geometry for the sheet editor (pure functions, no DOM).
 *
 * A part is a list of polygons {ext: [[x, y], ...], holes: [[[x, y], ...], ...]} in sheet units (closed rings,
 * first point repeated at the end). Parts are moved by an offset (ox, oy); nothing is copied while dragging.
 * Two parts collide when their outlines come closer than `gap` (parts too close) or when one lies inside the
 * material of the other (parts overlapping). A part may sit inside a hole of another part.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Geom = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function makePart(polys) {
    const rings = [];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const poly of polys) {
      for (const ring of [poly.ext].concat(poly.holes || [])) {
        let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
        for (const p of ring) {
          if (p[0] < a) a = p[0]; if (p[0] > c) c = p[0];
          if (p[1] < b) b = p[1]; if (p[1] > d) d = p[1];
        }
        rings.push({ pts: ring, bbox: [a, b, c, d] });
      }
      for (const p of poly.ext) {
        if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0];
        if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1];
      }
    }
    return { polys: polys, rings: rings, bbox: [x0, y0, x1, y1] };
  }

  function orient(ax, ay, bx, by, cx, cy) { return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); }
  function inBox(ax, ay, bx, by, px, py) {
    return Math.min(ax, bx) <= px && px <= Math.max(ax, bx) && Math.min(ay, by) <= py && py <= Math.max(ay, by);
  }
  function segsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
    const o1 = orient(ax, ay, bx, by, cx, cy), o2 = orient(ax, ay, bx, by, dx, dy);
    const o3 = orient(cx, cy, dx, dy, ax, ay), o4 = orient(cx, cy, dx, dy, bx, by);
    if (((o1 > 0 && o2 < 0) || (o1 < 0 && o2 > 0)) && ((o3 > 0 && o4 < 0) || (o3 < 0 && o4 > 0))) return true;
    if (o1 === 0 && inBox(ax, ay, bx, by, cx, cy)) return true;
    if (o2 === 0 && inBox(ax, ay, bx, by, dx, dy)) return true;
    if (o3 === 0 && inBox(cx, cy, dx, dy, ax, ay)) return true;
    if (o4 === 0 && inBox(cx, cy, dx, dy, bx, by)) return true;
    return false;
  }
  function ptSeg2(px, py, ax, ay, bx, by) {
    const vx = bx - ax, vy = by - ay, l2 = vx * vx + vy * vy;
    let t = l2 > 0 ? ((px - ax) * vx + (py - ay) * vy) / l2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const dx = px - (ax + t * vx), dy = py - (ay + t * vy);
    return dx * dx + dy * dy;
  }
  /** squared distance between two segments (0 if they touch or cross) */
  function segSeg2(ax, ay, bx, by, cx, cy, dx, dy) {
    if (segsIntersect(ax, ay, bx, by, cx, cy, dx, dy)) return 0;
    return Math.min(ptSeg2(ax, ay, cx, cy, dx, dy), ptSeg2(bx, by, cx, cy, dx, dy),
                    ptSeg2(cx, cy, ax, ay, bx, by), ptSeg2(dx, dy, ax, ay, bx, by));
  }

  function pointInRing(x, y, pts) {          // even-odd rule
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  /** is (x, y) inside the material of the part (outer ring, not in a hole)? part is NOT offset here */
  function pointInPart(x, y, part) {
    for (const poly of part.polys) {
      if (!pointInRing(x, y, poly.ext)) continue;
      let hole = false;
      for (const h of poly.holes || []) if (pointInRing(x, y, h)) { hole = true; break; }
      if (!hole) return true;
    }
    return false;
  }

  /** do part A (offset ax, ay) and part B (offset bx, by) overlap or come closer than `gap`? */
  function collides(A, ax, ay, B, bx, by, gap) {
    const g = gap, g2 = gap * gap;
    // 1. bounding boxes (grown by the gap)
    if (A.bbox[2] + ax + g <= B.bbox[0] + bx || B.bbox[2] + bx + g <= A.bbox[0] + ax ||
        A.bbox[3] + ay + g <= B.bbox[1] + by || B.bbox[3] + by + g <= A.bbox[1] + ay) return false;
    // 2. outlines too close / crossing
    for (const ra of A.rings) {
      const ab = ra.bbox;
      for (const rb of B.rings) {
        const bb = rb.bbox;
        if (ab[2] + ax + g <= bb[0] + bx || bb[2] + bx + g <= ab[0] + ax ||
            ab[3] + ay + g <= bb[1] + by || bb[3] + by + g <= ab[1] + ay) continue;
        const pa = ra.pts, pb = rb.pts;
        for (let i = 0; i + 1 < pa.length; i++) {
          const x1 = pa[i][0] + ax, y1 = pa[i][1] + ay, x2 = pa[i + 1][0] + ax, y2 = pa[i + 1][1] + ay;
          const sx0 = Math.min(x1, x2) - g, sx1 = Math.max(x1, x2) + g, sy0 = Math.min(y1, y2) - g, sy1 = Math.max(y1, y2) + g;
          for (let j = 0; j + 1 < pb.length; j++) {
            const x3 = pb[j][0] + bx, y3 = pb[j][1] + by, x4 = pb[j + 1][0] + bx, y4 = pb[j + 1][1] + by;
            if (Math.max(x3, x4) < sx0 || Math.min(x3, x4) > sx1 || Math.max(y3, y4) < sy0 || Math.min(y3, y4) > sy1) continue;
            if (segSeg2(x1, y1, x2, y2, x3, y3, x4, y4) < g2) return true;
          }
        }
      }
    }
    // 3. one part inside the material of the other (outlines do not touch): test one vertex of every polygon
    for (const poly of A.polys) {
      if (pointInPart(poly.ext[0][0] + ax - bx, poly.ext[0][1] + ay - by, B)) return true;
    }
    for (const poly of B.polys) {
      if (pointInPart(poly.ext[0][0] + bx - ax, poly.ext[0][1] + by - ay, A)) return true;
    }
    return false;
  }

  /** is the part (offset ox, oy) inside the rectangle [x0, y0, x1, y1]? (small tolerance for rounding) */
  function insideRect(part, ox, oy, r, tol) {
    tol = tol === undefined ? 0.01 : tol;
    return part.bbox[0] + ox >= r[0] - tol && part.bbox[1] + oy >= r[1] - tol &&
           part.bbox[2] + ox <= r[2] + tol && part.bbox[3] + oy <= r[3] + tol;
  }
  /** is the part (offset ox, oy) inside the usable area of the sheet? */
  function insideSheet(part, ox, oy, W, H, margin, tol) {
    return insideRect(part, ox, oy, [margin, margin, W - margin, H - margin], tol);
  }

  /**
   * Move part `i` of `parts` (all with current offsets offs[k] = [ox, oy]) from (x0, y0) towards (x1, y1) in small
   * steps and stop at the first position where it would hit another part or leave the sheet. Then try to slide
   * along the obstacle (x first, then y - and the other way round) and keep the better result.
   * Returns [x, y, blocked].
   */
  function moveConstrained(parts, offs, i, x0, y0, x1, y1, W, H, margin, gap) {
    return moveInRect(parts, offs, i, x0, y0, x1, y1, [margin, margin, W - margin, H - margin], gap);
  }
  function moveInRect(parts, offs, i, x0, y0, x1, y1, rect, gap) {
    const P = parts[i];
    gap = Math.max(gap - 0.005, 0);   // same rounding slack as the server check, so parts that sit exactly at the spacing can move
    const step = Math.min(4, Math.max(0.4, 2 * gap));
    const feasible = function (x, y) {
      if (!insideRect(P, x, y, rect, 0.005)) return false;
      for (let k = 0; k < parts.length; k++) {
        if (k !== i && collides(P, x, y, parts[k], offs[k][0], offs[k][1], gap)) return false;
      }
      return true;
    };
    const advance = function (ax, ay, bx, by) {
      const d = Math.hypot(bx - ax, by - ay);
      if (d < 1e-9) return [ax, ay, false];
      const n = Math.max(1, Math.ceil(d / step));
      for (let s = 1; s <= n; s++) {
        const t = s / n;
        if (!feasible(ax + (bx - ax) * t, ay + (by - ay) * t)) {
          let lo = (s - 1) / n, hi = t;
          for (let it = 0; it < 12; it++) {
            const mid = (lo + hi) / 2;
            if (feasible(ax + (bx - ax) * mid, ay + (by - ay) * mid)) lo = mid; else hi = mid;
          }
          return [ax + (bx - ax) * lo, ay + (by - ay) * lo, true];
        }
      }
      return [bx, by, false];
    };
    const first = advance(x0, y0, x1, y1);
    if (!first[2]) return first;
    // blocked: slide along the obstacle
    const dist = function (r) { return Math.hypot(x1 - r[0], y1 - r[1]); };
    const a = advance(first[0], first[1], x1, first[1]);
    const a2 = advance(a[0], a[1], a[0], y1);
    const b = advance(first[0], first[1], first[0], y1);
    const b2 = advance(b[0], b[1], x1, b[1]);
    let best = first;
    for (const r of [a2, b2]) if (dist(r) < dist(best) - 1e-9) best = r;
    return [best[0], best[1], true];
  }

  /** rotate (op "rotate", value = degrees, counter-clockwise) or mirror (op "flip", value "h" = left-right, "v" = top-bottom)
   *  a part about the centre of its bounding box (flip) or its centre of mass (rotate). The part is first shifted by (ox, oy), the drag offset it carries.
   *  Same convention as layout_edit.apply_transform on the server. Returns new polygons in sheet coordinates. */
  function centroid(polys) {            // area centroid (holes count as negative area)
    let A = 0, X = 0, Y = 0;
    const ring = function (pts, sign) {
      let a = 0, x = 0, y = 0;
      for (let i = 0; i + 1 < pts.length; i++) {
        const c = pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1];
        a += c; x += (pts[i][0] + pts[i + 1][0]) * c; y += (pts[i][1] + pts[i + 1][1]) * c;
      }
      const sg = a < 0 ? -1 : 1;           // orientation does not matter: use the absolute area
      A += sign * Math.abs(a) / 2; X += sign * sg * x / 6; Y += sign * sg * y / 6;
    };
    for (const poly of polys) { ring(poly.ext, 1); for (const h of poly.holes || []) ring(h, -1); }
    return A > 1e-9 ? [X / A, Y / A] : null;
  }
  function transformPolys(polys, ox, oy, op, value) {
    const b = makePart(polys).bbox;
    let cx = (b[0] + b[2]) / 2 + ox, cy = (b[1] + b[3]) / 2 + oy;
    if (op === 'rotate') {                // turn about the centre of mass, so that turning back returns to the same spot
      const c = centroid(polys);
      if (c) { cx = c[0] + ox; cy = c[1] + oy; }
    }
    let f;
    if (op === 'rotate') {
      const a = value * Math.PI / 180, co = Math.cos(a), si = Math.sin(a);
      f = function (x, y) { x += ox - cx; y += oy - cy; return [cx + x * co - y * si, cy + x * si + y * co]; };
    } else if (value === 'h') f = function (x, y) { return [2 * cx - (x + ox), y + oy]; };
    else f = function (x, y) { return [x + ox, 2 * cy - (y + oy)]; };
    const ring = function (r) { return r.map(function (p) { return f(p[0], p[1]); }); };
    return polys.map(function (poly) { return { ext: ring(poly.ext), holes: (poly.holes || []).map(ring) }; });
  }
  function translatePolys(polys, dx, dy) {
    const ring = function (r) { return r.map(function (p) { return [p[0] + dx, p[1] + dy]; }); };
    return polys.map(function (poly) { return { ext: ring(poly.ext), holes: (poly.holes || []).map(ring) }; });
  }
  /** the shift that brings a part's bounding box inside the rectangle, or null when it is too big for it */
  function shiftIntoRect(part, r) {
    const b = part.bbox;
    if (b[2] - b[0] > r[2] - r[0] + 0.005 || b[3] - b[1] > r[3] - r[1] + 0.005) return null;
    return [Math.max(0, r[0] - b[0]) - Math.max(0, b[2] - r[2]),
            Math.max(0, r[1] - b[1]) - Math.max(0, b[3] - r[3])];
  }
  function shiftIntoSheet(part, W, H, margin) { return shiftIntoRect(part, [margin, margin, W - margin, H - margin]); }
  /** may `part` (offset ox, oy) take the place of part i inside rectangle r? (not closer than `gap` to any other part) */
  function fitsRect(parts, offs, i, part, ox, oy, r, gap) {
    gap = Math.max(gap - 0.005, 0);
    if (!insideRect(part, ox, oy, r, 0.005)) return false;
    for (let k = 0; k < parts.length; k++) {
      if (k !== i && collides(part, ox, oy, parts[k], offs[k][0], offs[k][1], gap)) return false;
    }
    return true;
  }
  function fits(parts, offs, i, part, ox, oy, W, H, margin, gap) {
    return fitsRect(parts, offs, i, part, ox, oy, [margin, margin, W - margin, H - margin], gap);
  }

  return { insideRect: insideRect, shiftIntoRect: shiftIntoRect, fitsRect: fitsRect, moveInRect: moveInRect,
           transformPolys: transformPolys, translatePolys: translatePolys, shiftIntoSheet: shiftIntoSheet, fits: fits,
           makePart: makePart, collides: collides, insideSheet: insideSheet, moveConstrained: moveConstrained,
           segSeg2: segSeg2, pointInPart: pointInPart };
});
