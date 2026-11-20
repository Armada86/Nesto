# Nesto – 2D nesting in the browser

Streamlit app + Neon Postgres. Enter part dimensions (or import DXF drawings), set the sheet size, click **Nest**.

## Run
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your Neon DATABASE_URL
streamlit run app.py
python -m pytest tests
```

## Features
- **Any shape**: rectangle, circle, ellipse, triangle, right triangle, obround from the table, **STEP files of flat parts** (largest flat face -> outline + holes), or **any closed outline from DXF** (lines, arcs, circles, polylines, splines; inner contours become holes that other parts can fill).
- **Smart search** (irregular parts): builds rigid 2-up/4-up clusters (e.g. right triangles hypotenuse-to-hypotenuse), tries 4 placement rules and randomised restarts for a chosen time budget (Search effort: Quick / Normal 15 s / Thorough 60 s) and keeps the best layout.
- **True-shape nesting** (raster/FFT based): rotations in 90/45/30/15 degree steps, edge margin, part spacing, multi-sheet. Pure-rectangle jobs use an exact, instant MaxRects engine.
- **DXF export** of the nested sheets: ZIP with one DXF per sheet, or all sheets side by side in one DXF. Layers: `SHEET` (sheet outline), `CUT` (closed part outlines and holes), `LABELS` (part names, optional). Units: same as the sheet size you typed (mm).
- **Project menu** (left sidebar): New / Load / Save / Delete. Save stores the parts *and* the sheet + nesting settings of the open project; Load restores both. The open project is kept in the page address, so a browser reload reopens it.
- Neon tables: `projects`, `parts`, `nest_runs` (created/migrated automatically).

## Notes
- Irregular nesting resolution is set by *Precision* (cell = longest sheet side / 500, 800 or 1400). Parts end up about 1-2 cells further apart than the spacing you ask for; use *Fine* for tighter results. Overlap-free placement is guaranteed (checked by tests).
- Circles/ellipses are polygons slightly larger than the true curve (0.05 %), so real parts never overlap.

## Next steps
Multiple sheet sizes / remnants, DXF export of nested layouts, better search (multiple orderings, compaction pass), mirrored parts, auth.
