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
- **Regular scrap**: sheet count always comes first. Regular scrap = a free region that is rectangular (its biggest rectangle fills >= 70 % of it), reaches the sheet edge and is big enough relative to the sheet; holes in parts and irregular wedges do not count. Small parts (relative to the sheet) go into those irregular gaps - on any sheet - before they cut into a regular scrap piece; if no gap exists they go where they cost the least scrap. Among layouts with the same number of sheets the one with the biggest scrap pieces wins (biggest piece first, then the next). Scrap pieces are drawn as green dashed boxes.
- **True-shape nesting** (raster/FFT based): rotations in 90/45/30/15 degree steps, edge margin, part spacing, multi-sheet. Pure-rectangle jobs use an exact, instant MaxRects engine.
- **DXF export** of the nested sheets: ZIP with one DXF per sheet, or all sheets side by side in one DXF. Layers: `SHEET` (sheet outline), `CUT` (closed part outlines and holes), `LABELS` (part names, optional). Units: same as the sheet size you typed (mm).
- **All parts** tab: every part of the project (nested or not - quantity 0 keeps a part in the project without nesting it) with a search box; click a name to see its dimensioned drawing (overall size, edge lengths, diameters, hole sizes) plus area and cut length.
- **Project menu** (left sidebar): New / Load / Save / Delete. Sheet and nesting settings are saved with the project automatically the moment you change them; Save also writes the parts (settings first, each step reported separately). Load restores both. The open project is kept in the page address, so a browser reload reopens it.
- Neon tables: `projects`, `parts`, `nest_runs` (created/migrated automatically).

## Notes
- Irregular nesting resolution is set by *Precision* (cell = longest sheet side / 500, 800 or 1400). Parts end up about 1-2 cells further apart than the spacing you ask for; use *Fine* for tighter results. Overlap-free placement is guaranteed (checked by tests).
- Circles/ellipses are polygons slightly larger than the true curve (0.05 %), so real parts never overlap.

## Next steps
Multiple sheet sizes / remnants, DXF export of nested layouts, better search (multiple orderings, compaction pass), mirrored parts, auth.
