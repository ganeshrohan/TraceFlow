
import html
import importlib
from datetime import date, timedelta
import streamlit as st
import pandas as pd

import datasource as ds
import queries as q
import graphs
import impact
import lineage_parser

# The hosted runtime keeps a warm Python process and caches imported modules in
# sys.modules, so a rerun re-execs this entry script but keeps STALE helper modules.
# Reload when staleness is detected - a missing symbol OR a behind `graphs.BUILD`
# (symbol-presence alone misses edits to existing functions/strings). This avoids
# recreating @st.cache_data functions (and busting their cache) on every rerun.
_GRAPHS_BUILD = 6
_stale = (
    getattr(graphs, "BUILD", 0) < _GRAPHS_BUILD
    or not hasattr(q, "db_lineage_recursive")
    or not hasattr(q, "column_lineage_all")
    or not hasattr(q, "column_lineage_all_downstream")
    or not hasattr(q, "semantic_view_mappings")
    or not hasattr(ds, "run_meta")
    or not hasattr(graphs, "render_cards")
)
if _stale:
    for _mod in (ds, q, graphs, impact, lineage_parser):
        importlib.reload(_mod)

expression_for = lineage_parser.expression_for
SQLGLOT_OK = lineage_parser.SQLGLOT_OK

st.set_page_config(page_title="TraceFlow", page_icon="\u2744\ufe0f", layout="wide")

# ---------------------------------------------------------------------------
# Styling - corporate governance aesthetic (Snowsight / Collibra inspired)
# ---------------------------------------------------------------------------
st.title(":rainbow[TraceFlow]:sparkles:") 
st.caption("Trace Every Change. Understand Every Impact.")

# Dark-theme chips for the connection pill and governance tags (component CSS lives in graphs.py).
st.markdown(
    """
    <style>
      .pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;
            font-weight:700;letter-spacing:.03em;}
      .pill-live{background:#12351f;color:#4ade80;border:1px solid #1f5132;}
      .pill-off{background:#3a1416;color:#f87171;border:1px solid #5b1d20;}
      .tag{display:inline-block;padding:2px 8px;margin:2px;border-radius:6px;font-size:11px;
           background:#1b2130;color:#c7ced9;border:1px solid #2a3346;}
      .tag-pii{background:#3a1416;color:#f87171;border-color:#5b1d20;}
      .tag-gdpr{background:#3a2c12;color:#f0b458;border-color:#5b451d;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar - connection status + global DB/Schema/Object filter
# ---------------------------------------------------------------------------
status = ds.connection_status()
with st.sidebar:
    st.subheader("Connection")
    if status["connected"]:
        st.markdown('<span class="pill pill-live">\u25cf LIVE</span>', unsafe_allow_html=True)
        st.caption(f"{status['user']} \u00b7 {status['role']} \u00b7 {status['warehouse'] or 'no warehouse'}")
    else:
        st.markdown('<span class="pill pill-off">\u25cf NOT CONNECTED</span>', unsafe_allow_html=True)
        st.error("Could not reach Snowflake. Configure the connection (App Settings when hosted, "
                 "or .streamlit/secrets.toml locally).")
        with st.expander("Details"):
            st.code(status["error"] or "unknown error")
        st.stop()

    st.divider()
    st.subheader("Scope filter")
    lookback = st.slider("Lookback (days)", 1, 365, 365, help="ACCESS_HISTORY window; retention max 365 days.")
    depth = st.slider("Lineage depth", 1, 5, 5,
                      help="Max hops for recursive DB / table / column lineage and impact traversal.")

    try:
        dbs = q.list_databases()
    except Exception as exc:  # noqa: BLE001
        st.error("Failed to list databases. Ensure the role has IMPORTED PRIVILEGES on SNOWFLAKE.")
        st.code(str(exc))
        st.stop()

    sel_db = st.selectbox("Database", dbs, index=None, placeholder="Select database")
    sel_schema = None
    sel_obj = None
    obj_domain = None
    if sel_db:
        sel_schema = st.selectbox("Schema", q.list_schemas(sel_db), index=None, placeholder="Select schema")
    if sel_db and sel_schema:
        objs = q.list_objects(sel_db, sel_schema)
        names = objs["NAME"].tolist()
        sel_obj = st.selectbox("Object (table/view)", names, index=None, placeholder="Select object")
        if sel_obj:
            obj_domain = objs.loc[objs["NAME"] == sel_obj, "OBJECT_TYPE"].iloc[0]

    st.divider()
    st.button("\U0001f504 Refresh data", on_click=ds.clear_cache, use_container_width=True)

fq_object = f"{sel_db}.{sel_schema}.{sel_obj}" if (sel_db and sel_schema and sel_obj) else None

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_cat, tab_graph, tab_col, tab_wc, tab_impact = st.tabs(
    ["\U0001f4d2 Catalog", "\U0001f517 Interaction Graphs", "\U0001f9ec Column Lineage",
     "\u270d\ufe0f Writers & Consumers", "\u26a0\ufe0f Impact & Traceability"]
)


def _tag_html(tag_name: str, tag_value: str) -> str:
    key = f"{tag_name}:{tag_value}".upper()
    cls = "tag-gen"
    if "PII" in key or "SENSITIVE" in tag_name.upper():
        cls = "tag-pii"
    elif "GDPR" in key or "FORGET" in key:
        cls = "tag-gdpr"
    name, value = html.escape(str(tag_name)), html.escape(str(tag_value))
    return f'<span class="tag {cls}">{name}: {value}</span>'


# ---- Catalog tab ----------------------------------------------------------
with tab_cat:
    if not fq_object:
        st.info("Select a Database, Schema and Object in the sidebar to browse its catalog details.")
    else:
        det = q.table_details(sel_db, sel_schema, sel_obj)
        if det.empty:
            st.warning("No metadata found for this object.")
        else:
            r = det.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Rows", f"{int(r['ROW_COUNT']):,}" if pd.notna(r["ROW_COUNT"]) else "-")
            c2.metric("Size", f"{(r['BYTES'] or 0)/1024/1024:,.1f} MB" if pd.notna(r["BYTES"]) else "-")
            c3.metric("Owner", r["TABLE_OWNER"] or "-")
            c4.metric("Type", r["OBJECT_TYPE"] or "-")

            tags = q.policy_tags(sel_db, sel_schema, sel_obj)
            st.markdown("**Governance tags**")
            if tags is None or tags.empty:
                st.caption("No classification / policy tags on this object.")
            else:
                st.markdown(" ".join(_tag_html(t["TAG_NAME"], t["TAG_VALUE"]) for _, t in tags.iterrows()),
                            unsafe_allow_html=True)

            st.markdown("**Column schema**")
            cols = q.column_schema(sel_db, sel_schema, sel_obj)
            st.dataframe(cols, use_container_width=True, hide_index=True)


# ---- Interaction Graphs tab ----------------------------------------------
with tab_graph:
    st.markdown("#### Database \u2194 Database interaction")
    if sel_db:
        st.caption(f"Complete recursive lineage around **{sel_db}** "
                   "(A \u2192 B \u2192 {} \u2192 D). Edge label = distinct queries; HOP = distance from focus.".format(sel_db))
        lin = q.db_lineage_recursive(sel_db, lookback, max_hops=depth)
        if lin is None or lin.empty:
            st.info(f"No cross-database movement involving {sel_db} in the selected window "
                    "(note ACCESS_HISTORY 45min-3hr latency).")
        else:
            edges_df = lin[["SOURCE_DATABASE", "TARGET_DATABASE", "MOVEMENT_COUNT"]]
            graphs.render_cards(graphs.db_card_graph(edges_df, focus_db=sel_db),
                                height=460, key="cards_db")

            # Derive the node set with role (upstream/downstream) and min hop from focus.
            node_rows = {}
            for _, r in lin.iterrows():
                if r["DIRECTION"] == "UPSTREAM":
                    db_name, hop = r["SOURCE_DATABASE"], int(r["HOP"])
                else:
                    db_name, hop = r["TARGET_DATABASE"], int(r["HOP"])
                if db_name == sel_db:
                    continue
                if db_name not in node_rows or hop < node_rows[db_name]["HOP"]:
                    node_rows[db_name] = {"DATABASE_NAME": db_name,
                                          "RELATION": r["DIRECTION"], "HOP": hop}
            nodes_df = pd.DataFrame(node_rows.values())

            up_n = nodes_df[nodes_df["RELATION"] == "UPSTREAM"] if not nodes_df.empty else nodes_df
            down_n = nodes_df[nodes_df["RELATION"] == "DOWNSTREAM"] if not nodes_df.empty else nodes_df
            m1, m2, m3 = st.columns(3)
            m1.metric("Upstream databases", 0 if nodes_df.empty else len(up_n))
            m2.metric("Downstream databases", 0 if nodes_df.empty else len(down_n))
            m3.metric("Max hops", 0 if nodes_df.empty else int(nodes_df["HOP"].max()))

            if not nodes_df.empty:
                details = q.db_node_details(lookback)
                merged = nodes_df.merge(details, on="DATABASE_NAME", how="left")
                merged = merged.sort_values(["RELATION", "HOP", "DATABASE_NAME"])
                st.markdown("**Every database in the chain \u2014 owners & consumers**")
                st.dataframe(
                    merged[["RELATION", "HOP", "DATABASE_NAME", "OWNER",
                            "CONSUMER_COUNT", "CONSUMERS", "WRITER_COUNT", "WRITERS"]],
                    use_container_width=True, hide_index=True,
                )
                st.caption("Full edge list (each recursive hop):")
                st.dataframe(lin, use_container_width=True, hide_index=True)
    else:
        st.caption("Account-wide cross-database data movement. Select a Database in the sidebar "
                   "to see its full upstream/downstream neighbours with owners and consumers.")
        dbmov = q.db_to_db_movement(lookback)
        graphs.render_cards(graphs.db_card_graph(dbmov), height=360, key="cards_db_all")

    st.markdown("#### Table \u2194 Table interaction")
    if fq_object:
        st.caption(f"Recursive table-level lineage around **{fq_object}** "
                   "(ACCESS_HISTORY data movement; HOP = distance from focus).")
        tl = q.table_lineage_recursive(fq_object, lookback, max_hops=depth)
        if tl is None or tl.empty:
            st.info(f"No table-level data movement involving {fq_object} in the selected window "
                    "(note ACCESS_HISTORY 45min-3hr latency).")
        else:
            graphs.render_cards(
                graphs.table_card_graph(tl[["SOURCE_OBJECT", "TARGET_OBJECT"]], focus_object=fq_object),
                height=520, key="cards_table")
            up_t = tl[tl["DIRECTION"] == "UPSTREAM"]
            dn_t = tl[tl["DIRECTION"] == "DOWNSTREAM"]
            tm1, tm2, tm3 = st.columns(3)
            tm1.metric("Upstream edges", len(up_t))
            tm2.metric("Downstream edges", len(dn_t))
            tm3.metric("Max hops", int(tl["HOP"].max()))
            st.dataframe(tl[["DIRECTION", "HOP", "SOURCE_OBJECT", "TARGET_OBJECT", "MOVEMENT_COUNT"]],
                         use_container_width=True, hide_index=True)
    else:
        st.caption("Object dependency graph" + (f" for {sel_db}.{sel_schema}" if sel_schema else " (select a DB/Schema to scope)"))
        tt = q.table_to_table(sel_db, sel_schema)
        graphs.render_cards(graphs.table_card_graph(tt), height=460, key="cards_table_deps")
    st.caption("Tip: pick an object in the sidebar, then open **Column Lineage** to drill into its columns.")


# ---- Column Lineage tab ---------------------------------------------------
with tab_col:
    if not fq_object:
        st.info("Select an object in the sidebar to see its complete column-level lineage.")
    else:
        st.markdown(f"#### Column lineage for `{fq_object}`")
        if not SQLGLOT_OK:
            st.caption("sqlglot not installed - transformations are extracted with a built-in "
                       "best-effort SQL parser (may be approximate for complex statements).")

        # --- Semantic exposure map: physical column -> semantic metrics/dimensions ---
        sem_df = q.semantic_view_mappings()
        sem_map = {}
        if sem_df is not None and not sem_df.empty:
            for _, s in sem_df.iterrows():
                sem_map.setdefault(f'{s["PHYSICAL_OBJECT"]}.{s["PHYSICAL_COLUMN"]}', []).append(
                    (s["SEMANTIC_VIEW"], s["ENTITY_TYPE"], s["LOGICAL_NAME"]))
        badge_keys = {f"{o}\u0001{c}" for k in sem_map for (o, c) in [k.rsplit(".", 1)]}

        # --- Point-in-time lineage diff (as-of two dates) ---
        with st.expander("\U0001f553 Compare lineage as-of two dates (diff)"):
            dc1, dc2, dc3 = st.columns([1, 1, 1.4])
            t1 = dc1.date_input("Before", value=date.today() - timedelta(days=7), key="diff_t1")
            t2 = dc2.date_input("After", value=date.today(), key="diff_t2")
            ddir = dc3.radio("Direction", ["Upstream", "Downstream"], horizontal=True, key="diff_dir")
            dfn = q.column_lineage_all if ddir == "Upstream" else q.column_lineage_all_downstream
            a = dfn(fq_object, lookback, max_hops=depth, as_of=f"{t1} 23:59:59")
            b = dfn(fq_object, lookback, max_hops=depth, as_of=f"{t2} 23:59:59")

            def _emap(dfx):
                m = {}
                if dfx is not None and not dfx.empty:
                    for _, e in dfx.iterrows():
                        m[(e["FROM_OBJECT"], e["FROM_COLUMN"], e["TO_OBJECT"], e["TO_COLUMN"])] = \
                            expression_for(e["EDGE_SQL"], e["FROM_COLUMN"])
                return m
            ma, mb = _emap(a), _emap(b)
            keys = set(ma) | set(mb)
            if not keys:
                st.caption("No lineage edges in either snapshot for this object/window.")
            else:
                drows = []
                for k in keys:
                    if k in mb and k not in ma:
                        sstat = "ADDED"
                    elif k in ma and k not in mb:
                        sstat = "REMOVED"
                    elif ma.get(k) != mb.get(k):
                        sstat = "TRANSFORM_CHANGED"
                    else:
                        sstat = "UNCHANGED"
                    drows.append({"FROM_OBJECT": k[0], "FROM_COLUMN": k[1], "TO_OBJECT": k[2],
                                  "TO_COLUMN": k[3], "STATUS": sstat,
                                  "TRANSFORMATION": mb.get(k) or ma.get(k) or ""})
                diff_df = pd.DataFrame(drows)
                graphs.render_cards(
                    graphs.column_card_graph(diff_df, focus_object=fq_object, badges=badge_keys),
                    height=560, key="cyto_col_diff")
                st.caption("Green = added \u00b7 red (dashed) = removed \u00b7 amber = transform changed \u00b7 grey = unchanged.")
                changed = diff_df[diff_df["STATUS"] != "UNCHANGED"]
                st.dataframe((changed if not changed.empty else diff_df)[
                    ["STATUS", "TO_OBJECT", "TO_COLUMN", "FROM_OBJECT", "FROM_COLUMN", "TRANSFORMATION"]],
                    use_container_width=True, hide_index=True)

        # ===== UPSTREAM: complete multi-hop column mapping for ALL columns =====
        st.markdown("##### Upstream \u2014 full column mapping (all columns, multi-hop) with transformations")
        allmap = q.column_lineage_all(fq_object, lookback, max_hops=depth)
        if allmap is not None and not allmap.empty:
            roots = sorted(allmap["ROOT_COLUMN"].dropna().unique().tolist())
            picks = st.multiselect("Filter to specific target column(s) (default: all)", roots, default=[])
            view = allmap if not picks else allmap[allmap["ROOT_COLUMN"].isin(picks)]

            col_view = view[["FROM_OBJECT", "FROM_COLUMN", "TO_OBJECT", "TO_COLUMN"]].copy()
            col_view["TRANSFORMATION"] = [
                expression_for(sql, fc) for sql, fc in zip(view["EDGE_SQL"], view["FROM_COLUMN"])
            ]
            graphs.render_cards(
                graphs.column_card_graph(col_view, focus_object=fq_object, badges=badge_keys),
                height=620, key="cyto_col_cards")
            st.caption("Click a column to trace it back to its source object(s); "
                       "the transformation for each hop appears on the highlighted edges.")

            rows = []
            for _, e in view.iterrows():
                rows.append({
                    "ROOT_COLUMN": e["ROOT_COLUMN"],
                    "HOP": int(e["HOP"]),
                    "TARGET": f'{e["FROM_OBJECT"]}.{e["FROM_COLUMN"]}',
                    "SOURCE": f'{e["TO_OBJECT"]}.{e["TO_COLUMN"]}',
                    "TRANSFORMATION": expression_for(e["EDGE_SQL"], e["FROM_COLUMN"]),
                })
            st.markdown("**Column mapping (main table \u2190 upstream, per hop)**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            with st.expander("Full lineage paths (per edge)"):
                st.dataframe(view[["ROOT_COLUMN", "HOP", "LINEAGE_PATH"]],
                             use_container_width=True, hide_index=True)
            with st.expander("\U0001f50d View full transformation SQL"):
                seen = set()
                for _, e in view.iterrows():
                    sql_text = e.get("EDGE_SQL")
                    if sql_text and sql_text not in seen:
                        seen.add(sql_text)
                        st.code(sql_text, language="sql")
                if not seen:
                    st.caption("No SQL text captured in ACCESS_HISTORY for these edges.")
        else:
            # Fallback: direct (1-hop) mapping when multi-hop column lineage is unavailable
            edges = q.column_edges_for_table(fq_object, lookback)
            if edges is None or edges.empty:
                st.info("No column-level lineage found in ACCESS_HISTORY for this object in the selected window "
                        "(note 45min-3hr latency).")
            else:
                gmap = pd.DataFrame({
                    "FROM_OBJECT": edges["SOURCE_OBJECT"], "FROM_COLUMN": edges["SOURCE_COLUMN"],
                    "TO_OBJECT": fq_object, "TO_COLUMN": edges["TARGET_COLUMN"],
                }).dropna(subset=["FROM_OBJECT", "FROM_COLUMN"])
                if not gmap.empty:
                    graphs.render_cards(graphs.column_card_graph(gmap, focus_object=fq_object),
                                        height=440, key="cyto_col_cards_fb")
                rows = [{
                    "SOURCE_OBJECT": e["SOURCE_OBJECT"], "SOURCE_COLUMN": e["SOURCE_COLUMN"],
                    "TARGET_COLUMN": e["TARGET_COLUMN"],
                    "TRANSFORMATION": expression_for(e["FULL_SQL"], e["TARGET_COLUMN"]),
                } for _, e in edges.iterrows()]
                st.markdown("**Full column mapping (source table \u2192 this table)**")
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                with st.expander("\U0001f50d View full transformation SQL"):
                    seen = set()
                    for _, e in edges.iterrows():
                        sql_text = e.get("FULL_SQL")
                        if sql_text and sql_text not in seen:
                            seen.add(sql_text)
                            st.code(sql_text, language="sql")
                    if not seen:
                        st.caption("No SQL text captured in ACCESS_HISTORY for these edges.")

        # ===== DOWNSTREAM: column-level lineage (mirror of upstream) =====
        st.markdown("##### Downstream \u2014 columns consuming this table (multi-hop) with transformations")
        dnmap = q.column_lineage_all_downstream(fq_object, lookback, max_hops=depth)
        if dnmap is not None and not dnmap.empty:
            dn_view = dnmap[["FROM_OBJECT", "FROM_COLUMN", "TO_OBJECT", "TO_COLUMN"]].copy()
            dn_view["TRANSFORMATION"] = [
                expression_for(sql, fc) for sql, fc in zip(dnmap["EDGE_SQL"], dnmap["FROM_COLUMN"])
            ]
            graphs.render_cards(
                graphs.column_card_graph(dn_view, focus_object=fq_object, badges=badge_keys),
                height=620, key="cyto_col_cards_down")
            st.caption("This table's columns flow left \u2192 right into their downstream consumers; "
                       "click a column to trace it and see the transformation on each hop.")
            drows = [{
                "ROOT_COLUMN": e["ROOT_COLUMN"],
                "HOP": int(e["HOP"]),
                "SOURCE": f'{e["TO_OBJECT"]}.{e["TO_COLUMN"]}',
                "TARGET": f'{e["FROM_OBJECT"]}.{e["FROM_COLUMN"]}',
                "TRANSFORMATION": expression_for(e["EDGE_SQL"], e["FROM_COLUMN"]),
            } for _, e in dnmap.iterrows()]
            st.markdown("**Column mapping (this table \u2192 downstream, per hop)**")
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)
        else:
            st.caption("No downstream column-level lineage recorded \u2014 this table is a leaf (no consumers).")

        # ===== Semantic exposure: which metrics/dimensions this table's columns power =====
        st.markdown("##### \u2605 Semantic exposure")
        sem_rows = []
        for _k, _ents in sem_map.items():
            _o, _c = _k.rsplit(".", 1)
            if _o == fq_object:
                for (_view, _etype, _logical) in _ents:
                    sem_rows.append({"COLUMN": _c, "SEMANTIC_VIEW": _view,
                                     "ENTITY_TYPE": _etype, "LOGICAL_NAME": _logical})
        if sem_rows:
            st.caption("Columns of this table that power semantic-view metrics/dimensions "
                       "(marked with \u2605 on the maps above).")
            st.dataframe(pd.DataFrame(sem_rows).sort_values(["COLUMN", "SEMANTIC_VIEW"]),
                         use_container_width=True, hide_index=True)
        elif not sem_map:
            st.caption("No semantic views defined in this account.")
        else:
            st.caption("No semantic-view metric/dimension references this table's columns.")


# ---- Writers & Consumers tab ---------------------------------------------
with tab_wc:
    if not fq_object:
        st.info("Select an object in the sidebar to see who writes to and reads from it.")
    else:
        st.markdown(f"#### DML writers & consumers for `{fq_object}`")
        cw, cc = st.columns(2)
        with cw:
            st.markdown("**\u270d\ufe0f Writers (DML)**")
            w = q.writers(fq_object, lookback)
            if w is None or w.empty:
                st.caption("No writes recorded in the selected window.")
            else:
                st.dataframe(w[["USER_NAME", "ROLE_NAME", "WAREHOUSE_NAME", "WRITE_COUNT", "LAST_WRITE"]],
                             use_container_width=True, hide_index=True)
        with cc:
            st.markdown("**\U0001f441\ufe0f Consumers (reads)**")
            c = q.consumers(fq_object, lookback)
            if c is None or c.empty:
                st.caption("No reads recorded in the selected window.")
            else:
                st.dataframe(c[["USER_NAME", "ROLE_NAME", "WAREHOUSE_NAME", "READ_COUNT", "LAST_READ"]],
                             use_container_width=True, hide_index=True)


# ---- Impact & Traceability tab -------------------------------------------
with tab_impact:
    if not fq_object:
        st.info("Select an object in the sidebar to compute upstream/downstream traceability and impact.")
    else:
        st.markdown(f"#### Impact & traceability for `{fq_object}`")
        up = impact.upstream_nodes(fq_object, depth)
        down = impact.downstream_nodes(fq_object, depth)
        cons = q.consumers(fq_object, lookback)
        risk = impact.cascading_risk(fq_object, down, cons)

        m1, m2, m3 = st.columns(3)
        m1.metric("Downstream deps", risk["downstream_count"])
        m2.metric("Upstream sources", 0 if up is None else len(up))
        color = {"CRITICAL": "#c5221f", "MODERATE": "#b06000", "LOW": "#188038"}[risk["tier"]]
        m3.markdown(f"**Cascading risk**<br><span class='risk' style='color:{color};font-size:1.6rem'>"
                    f"{risk['score']}% \u00b7 {risk['tier']}</span>", unsafe_allow_html=True)

        cu, cd = st.columns(2)
        with cu:
            st.markdown("**\u2b06\ufe0f Upstream sources**")
            st.dataframe(up, use_container_width=True, hide_index=True)
        with cd:
            st.markdown("**\u2b07\ufe0f Downstream dependents**")
            st.dataframe(down, use_container_width=True, hide_index=True)

        st.markdown("**\U0001f4e3 Stakeholders to notify before an ALTER / DROP**")
        st.caption("Consumers of this object and its downstream dependents (from ACCESS_HISTORY).")
        st.dataframe(impact.stakeholders(fq_object, down, lookback), use_container_width=True, hide_index=True)
