# TraceFlow

A Streamlit-in-Snowflake app for **metadata discovery, data lineage and governance** built entirely on live Snowflake `ACCOUNT_USAGE` metadata. It visualizes how databases, tables and columns are connected — who writes them, who consumes them, and how data is transformed as it moves — using interactive Apache ECharts graphs.

The app is **zero-external-access**: it runs on the pre-installed Streamlit runtime and a locally vendored ECharts bundle, with no PyPI or CDN calls at runtime (this account is a trial account, where External Access Integrations are not available).

---

## Features

- **Catalog** — object metadata (rows, size, owner, type), governance/classification tags, and column schema.
- **Interaction Graphs**
  - **Database ↔ Database**: complete **recursive** upstream/downstream DB lineage (A → B → C → D) with per-database owners, consumers and writers.
  - **Table ↔ Table**: **recursive** upstream/downstream object lineage from `ACCESS_HISTORY` data movement (captures `INSERT INTO ... SELECT`, `CTAS`, `MERGE`).
- **Column Lineage**
  - **Upstream**: full **multi-hop column mapping for all columns** (source + intermediate + main tables) with the transformation expression at each hop.
  - **Downstream**: the recursive list of consuming table names.
- **Writers & Consumers** — who writes to and reads from an object, with role, warehouse, counts and sample SQL.
- **Impact & Traceability** — upstream sources, downstream dependents, BI/view exposure, a cascading-risk score, and stakeholders to notify before an `ALTER`/`DROP`.
- **Full-screen Graph** — render any lineage graph large across the full page width, with an adjustable height, pan and zoom.

All graphs render with **Apache ECharts** (force layout) via an inline `st.components.v2` component, and degrade gracefully to the built-in **Graphviz** renderer when ECharts/CCv2 is unavailable.

---

## Project layout

| File | Purpose |
|------|---------|
| `streamlit_app.py` | Entry point; sidebar selectors, tabs, and page wiring. |
| `datasource.py` | `st.connection("snowflake")` adapter + cached, parameterized query runner. |
| `queries.py` | All SQL: catalog, DB/table/column lineage (incl. recursive), writers/consumers. |
| `graphs.py` | ECharts graph builders + inline CCv2 renderer (with Graphviz fallback). |
| `graph_echarts.ts` | TypeScript source for the CCv2 renderer (authoritative; inline JS twin lives in `graphs.py`). |
| `lineage_parser.py` | `sqlglot`-based per-column transformation extractor (degrades gracefully if `sqlglot` is absent). |
| `impact.py` | Impact analysis / cascading-risk / stakeholder helpers. |
| `static/echarts.min.js` | **Vendored** Apache ECharts UMD bundle (offline; no CDN). |
| `pyproject.toml` / `requirements.txt` | Dependencies — kept minimal to avoid PyPI fetch on a trial account. |
| `.streamlit/config.toml` | Theme / config. |
| `snowflake.yml` | Streamlit-in-Snowflake deployment manifest (`main_file`, `artifacts`, warehouse). |

---

## Requirements

- Snowflake account with `ACCOUNT_USAGE` access (the app reads `ACCESS_HISTORY`, `QUERY_HISTORY`, `DATABASES`, `OBJECT_DEPENDENCIES`). `ACCOUNTADMIN` or a role with `IMPORTED PRIVILEGES` on the `SNOWFLAKE` database is required.
- A warehouse for the app to run queries.
- Streamlit-in-Snowflake (Workspace / container runtime).

> **Note on data latency:** `ACCESS_HISTORY` / `QUERY_HISTORY` have 45min–3hr latency and 365-day retention, so very recent activity may not appear immediately.

---

## Deploy / run in Snowsight

1. Upload the `snowflow_portal/` folder into a Snowflake Workspace (or deploy via `snow streamlit`).
2. Ensure `snowflake.yml` points `query_warehouse` to a valid warehouse and `main_file: streamlit_app.py`.
3. Confirm `artifacts` lists every file (including `static/echarts.min.js` and `graph_echarts.ts`).
4. Click **Run** in Snowsight. Pick a Database / Schema / Object in the sidebar to populate the tabs.

### Re-vendoring ECharts (if needed)
`static/echarts.min.js` must be the real ~1 MB Apache ECharts UMD build (already included). To refresh it, on a machine with internet:
```
curl -sSL -o echarts.min.js https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js
```
and replace `static/echarts.min.js`. If it's missing or a placeholder, the app automatically falls back to Graphviz.

### Optional: `sqlglot` for exact per-column expressions
`sqlglot` is not in the pre-installed runtime and cannot be fetched on a trial account. Column-expression parsing degrades gracefully without it. To enable it offline, vendor the pure-Python `sqlglot/` package folder into the project and add it to `artifacts`.

---

## Companion SQL toolkit

A standalone, modular lineage SQL toolkit (SET-variable driven, raw output) is maintained separately in `Untitled 9.sql`, and a demo pipeline that seeds source/target databases via `INSERT INTO` lives in `lineage_demo_setup.sql`. Both are useful for exploring the same `GET_LINEAGE` / `ACCESS_HISTORY` metadata this app is built on.

---

*Co-authored with CoCo.*
