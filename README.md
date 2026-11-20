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
- **Regular scrap**: sheet count always comes first. Scrap worth keeping = a free region that is rectangular (its biggest rectangle fills >= 70 % of it), reaches the sheet edge, is at least 15 % of the sheet's narrow side thick and big enough; holes in parts, irregular wedges and thinner strips are waste. Layouts are ranked by scrap quality = area x squareness (short side / long side), best piece first, so a big square beats a long thin strip; ties go to the layout that keeps the biggest strip whole. **Small parts go into the smallest gap**: every free region on every sheet where the part fits without cutting regular scrap is considered, and the smallest one wins (a tight pocket beats the long open strip). Only if no such gap exists does a small part take the spot that costs the least scrap. Scrap pieces are drawn as green dashed boxes.
- **Settle pass + bottom preference**: after the search, every part of the winning layout slides down, then left, as far as it can (exact geometry, never closer than the part spacing, never into the margin; parts lying inside the hole of another part are respected). Parts end up flush on the bottom/left edge and the free space collects at the top/right. A sheet is kept settled only if its scrap is not worse than before. When two layouts are otherwise equal, the lower one (parts nearer the bottom edge) wins.
- **Flipping**: every part has *Can rotate* and *Can flip* ticks (both on by default; both sides of the sheet are assumed to be the same). With *Can flip* on, the engine may also place the mirror image of a part (turned over) - useful for asymmetric parts, free for symmetric ones (a mirror image that is just a rotation is skipped; mirror images are tried at the right-angle rotations and the angles that lay a straight edge flat). Flipped layouts cost more computing time, so they are explored after the usual layouts (in *Quick* mode one flip layout is always included) and the best layout wins. Switch *Can flip* off for parts with a finish side, grain direction, handing or a bend direction that matters. Pairs built from a part flip consistently.
- **True-shape nesting** (raster/FFT based): rotations in 90/45/30/15 degree steps, edge margin, part spacing, multi-sheet. Pure-rectangle jobs use an exact, instant MaxRects engine.
- **DXF export** of the nested sheets: ZIP with one DXF per sheet, or all sheets side by side in one DXF. Layers: `SHEET` (sheet outline), `CUT` (closed part outlines and holes), `LABELS` (part names, optional). Units: same as the sheet size you typed (mm).
- **All parts** tab: every part of the project (nested or not - quantity 0 keeps a part in the project without nesting it) with a search box; click a name to see its dimensioned drawing (overall size, edge lengths, diameters, hole sizes) plus area and cut length.
- **History**: the sidebar lists the open project's past runs (number, sheets, utilization, time). *Load run* shows one again; *Delete…* removes one or several runs (pick them in a list or *Select all*; it only ever deletes runs of the open project).
- **Project menu** (left sidebar): New / Load / Save / Rename / Delete. Rename keeps the parts, settings and history; duplicate names (any letter case) are refused. Sheet and nesting settings are saved with the project automatically the moment you change them; Save also writes the parts (settings first, each step reported separately). Load restores both. The open project is kept in the page address, so a browser reload reopens it.
- Neon tables: `projects`, `parts`, `nest_runs` (created/migrated automatically).

## Notes
- Irregular nesting resolution is set by *Precision* (cell = longest sheet side / 500, 800 or 1400). Parts end up about 1-2 cells further apart than the spacing you ask for; use *Fine* for tighter results. Overlap-free placement is guaranteed (checked by tests).
- Circles/ellipses are polygons slightly larger than the true curve (0.05 %), so real parts never overlap.

## Next steps
Multiple sheet sizes / remnants, DXF export of nested layouts, better search (multiple orderings, compaction pass), mirrored parts, auth.
