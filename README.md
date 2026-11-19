# Nesto – 2D nesting in the browser

Streamlit app + Neon Postgres. Enter part dimensions (or import DXF drawings), set the sheet size, click **Nest**.

## Run
```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your Neon DATABASE_URL
streamlit run app.py
python -m pytest tests
```

## v1 scope
- Rectangular nesting (MaxRects, tries 5 orderings x 4 heuristics, keeps the best), multi-sheet, optional 90° rotation, edge margin and part spacing.
- DXF import: one part per file; the **bounding box** is nested, the real outline is drawn.
- Neon tables: `projects`, `parts`, `nest_runs` (created automatically).

## Next steps
True-shape nesting (polygons, Shapely/no-fit-polygon), multiple sheet sizes / remnants, DXF/PDF export of layouts, multiple parts per DXF, auth.
