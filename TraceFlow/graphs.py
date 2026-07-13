
"""Renders lineage as dbt / Select Star style CARD graphs (table cards with a scrollable
column list, column-to-column bezier edges, click-to-trace, and per-edge transformations)
via a dependency-free HTML/CSS/SVG inline CCv2 component. Falls back to a Graphviz clustered
diagram when CCv2 is unavailable. No external JS libraries are required."""

import streamlit as st

# Module build number. Bump on ANY change to this file so the entry script's reload guard
# reloads it in the warm runtime (symbol-presence checks miss edits to existing functions/strings).
BUILD = 6

# CCv2 availability is runtime-dependent (platform Streamlit build); probe defensively.
_CCV2_OK = hasattr(st, "components") and hasattr(getattr(st, "components"), "v2")


# ======================================================================================
# dbt / Select Star style COLUMN CARD lineage - custom, dependency-free HTML/CSS/SVG.
# Cards have a FIXED height with a SCROLLABLE column list (handles tables with hundreds of
# columns). SVG bezier connectors anchor to each column row; clicking a column highlights
# its links. Falls back to a Graphviz clustered diagram when CCv2 is unavailable.
# ======================================================================================
_CARD_CSS = r"""
.cardmap-shell{position:relative;font-family:'Source Sans Pro',sans-serif;}
.cardmap-shell.fs{position:fixed;inset:0;z-index:99999;background:#0e1117;padding:6px;}
.cardmap-bar{display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid #2a3346;
  border-bottom:none;border-radius:10px 10px 0 0;background:#1b2130;}
.cardmap-bar .ttl{font-size:12px;font-weight:700;color:#c7ced9;}
.cardmap-bar .sp{flex:1;}
.cardmap-bar button{cursor:pointer;border:1px solid #384357;background:#232b3b;border-radius:6px;
  padding:3px 9px;font-size:12px;color:#c7ced9;}
.cardmap-bar button:hover{background:#2a3346;}
.cardmap-bar input{border:1px solid #384357;border-radius:6px;padding:3px 8px;font-size:12px;width:150px;
  background:#0e1117;color:#e6e8eb;}
.cardmap-wrap{position:relative;overflow:auto;border:1px solid #2a3346;border-radius:0 0 10px 10px;
  background:#0e1117;}
.cardmap-canvas{position:relative;}
.cardmap-svg{position:absolute;left:0;top:0;pointer-events:none;overflow:visible;}
.cardmap-card{position:absolute;background:#161b26;border:1px solid #2a3346;border-radius:8px;
  box-shadow:0 2px 6px rgba(0,0,0,.5);overflow:hidden;}
.cardmap-card.focus{border-color:#e8a33d;box-shadow:0 0 0 2px #3a2c12;}
.cardmap-hd{min-height:48px;line-height:1.2;box-sizing:border-box;background:#2563c9;color:#fff;
  font-weight:600;font-size:12px;padding:6px 10px;cursor:grab;display:flex;align-items:center;gap:6px;
  user-select:none;}
.cardmap-hd:active{cursor:grabbing;}
.cardmap-card.focus .cardmap-hd{background:#b06000;}
.cardmap-hd .cv{font-size:10px;transition:transform .15s;flex:none;}
.cardmap-card.collapsed .cardmap-hd .cv{transform:rotate(-90deg);}
.cardmap-hd .nm{flex:1;white-space:normal;word-break:break-all;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.cardmap-hd .ct{opacity:.85;font-weight:400;font-size:11px;flex:none;}
.cardmap-body{overflow-y:auto;overflow-x:hidden;}
.cardmap-card.collapsed .cardmap-body{display:none;}
.cardmap-col{height:22px;box-sizing:border-box;font-size:11px;color:#d7dbe0;padding:3px 10px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-top:1px solid #232b3b;cursor:pointer;}
.cardmap-col:hover{background:#202838;}
.cardmap-col.hl{background:#3a2c12;color:#f0b458;font-weight:600;}
.cardmap-col.hl2{background:#17233b;}
.cardmap-col.fltout{opacity:.25;}
.cardmap-edge{fill:none;stroke:#4a5468;stroke-width:1.2px;}
.cardmap-edge.hl{stroke:#e8a33d;stroke-width:2.4px;}
.cardmap-canvas.has-active .cardmap-edge:not(.hl){stroke:#2a3346;opacity:.45;}
.cardmap-edge.add{stroke:#4ade80;stroke-width:1.8px;}
.cardmap-edge.rem{stroke:#f87171;stroke-width:1.8px;stroke-dasharray:5 3;}
.cardmap-edge.chg{stroke:#f0b458;stroke-width:1.8px;}
.cardmap-col.badge::after{content:"\2605";color:#f0b458;float:right;margin-left:6px;font-size:10px;}
.cardmap-elabel{font:600 10px 'Source Sans Pro',sans-serif;fill:#f0b458;text-anchor:middle;paint-order:stroke;stroke:#0e1117;stroke-width:3px;stroke-linejoin:round;pointer-events:none;}
"""

_CARD_RENDERER_JS = r"""
export default function (component) {
  const { data, parentElement } = component;
  const H = (data && data.height) || 600;
  const CARD_W=260, HEADER_H=48, ROW_H=22, BODY_MAX=220, GAP_X=90, GAP_Y=30, PAD=24, SEP="\u0001", BAR_H=40;
  const tables = (data.cards && data.cards.tables) || [];
  const edges  = (data.cards && data.cards.edges)  || [];
  const badgeSet = new Set((data.cards && data.cards.badges) || []);

  // NOTE: do NOT clear parentElement.innerHTML - that would delete the injected
  // <style> block (a sibling of the mount div) and break all card styling/layout.
  const _old = parentElement.querySelector(".cardmap-shell");
  if (_old) _old.remove();
  const shell = document.createElement("div"); shell.className = "cardmap-shell";
  const bar = document.createElement("div"); bar.className = "cardmap-bar";
  const ttl = document.createElement("span"); ttl.className = "ttl"; ttl.textContent = "Column card lineage";
  const sp  = document.createElement("span"); sp.className = "sp";
  const filt = document.createElement("input"); filt.placeholder = "filter columns\u2026";
  const bExp = document.createElement("button"); bExp.textContent = "Expand all";
  const bCol = document.createElement("button"); bCol.textContent = "Collapse all";
  const bClr = document.createElement("button"); bClr.textContent = "Clear";
  const bFs  = document.createElement("button"); bFs.textContent = "\u26f6 Full screen";
  bar.append(ttl, sp, filt, bExp, bCol, bClr, bFs);
  const wrap = document.createElement("div");
  wrap.className = "cardmap-wrap"; wrap.style.height = (H - BAR_H) + "px";
  const canvas = document.createElement("div"); canvas.className = "cardmap-canvas";
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "cardmap-svg");
  canvas.appendChild(svg); wrap.appendChild(canvas);
  shell.append(bar, wrap); parentElement.appendChild(shell);

  // depth (longest-path) for left->right layering
  const byId = {}; tables.forEach(t => byId[t.id] = t);
  const succ = {}, indeg = {}; tables.forEach(t => { succ[t.id]=[]; indeg[t.id]=0; });
  edges.forEach(e => { if (byId[e.s] && byId[e.t]) { succ[e.s].push(e.t); indeg[e.t]++; } });
  const depth = {}; tables.forEach(t => depth[t.id]=0);
  const ind = Object.assign({}, indeg);
  let q = tables.filter(t => indeg[t.id]===0).map(t => t.id);
  while (q.length) { const n=q.shift(); (succ[n]||[]).forEach(m => {
    depth[m]=Math.max(depth[m], depth[n]+1); if(--ind[m]===0) q.push(m); }); }
  const groups = {}; tables.forEach(t => { (groups[depth[t.id]] = groups[depth[t.id]]||[]).push(t); });
  const depthKeys = Object.keys(groups).map(Number).sort((a,b)=>a-b);

  // build cards once
  const mode = {}, cardEl = {}, bodyEl = {}, colIndex = {}, rowEl = {}, pos = {}, bodyH = {};
  tables.forEach(t => {
    mode[t.id] = "normal";
    colIndex[t.id] = {}; t.columns.forEach((c,i) => colIndex[t.id][c]=i);
    const card = document.createElement("div");
    card.className = "cardmap-card" + (t.kind==="focus" ? " focus" : "");
    card.style.width = CARD_W + "px";
    const hd = document.createElement("div"); hd.className = "cardmap-hd";
    const cv = document.createElement("span"); cv.className = "cv"; cv.textContent = "\u25be";
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = t.label; nm.title = t.label;
    const ct = document.createElement("span"); ct.className = "ct"; ct.textContent = t.columns.length;
    hd.append(cv, nm, ct);
    // drag to move the card; a click without movement toggles collapse
    let drag = null;
    hd.addEventListener("pointerdown", ev => {
      if (ev.button !== 0) return;
      const p = pos[t.id] || {x: parseFloat(card.style.left)||0, y: parseFloat(card.style.top)||0};
      drag = {sx: ev.clientX, sy: ev.clientY, ox: p.x, oy: p.y, moved: false};
      try { hd.setPointerCapture(ev.pointerId); } catch(e) {}
    });
    hd.addEventListener("pointermove", ev => {
      if (!drag) return;
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      if (!drag.moved && (Math.abs(dx) + Math.abs(dy)) < 4) return;
      drag.moved = true;
      const nx = Math.max(0, drag.ox + dx), ny = Math.max(0, drag.oy + dy);
      pos[t.id] = {x: nx, y: ny};
      card.style.left = nx + "px"; card.style.top = ny + "px";
      scheduleDraw();
    });
    hd.addEventListener("pointerup", ev => {
      const wasDrag = drag && drag.moved;
      if (drag) { try { hd.releasePointerCapture(ev.pointerId); } catch(e) {} }
      drag = null;
      if (!wasDrag) {
        const collapsed = card.classList.toggle("collapsed");
        mode[t.id] = collapsed ? "collapsed" : "normal";
        relayout();
      }
    });
    card.appendChild(hd);
    const body = document.createElement("div"); body.className = "cardmap-body";
    t.columns.forEach(c => {
      const r = document.createElement("div");
      const key = t.id+SEP+c;
      r.className = "cardmap-col" + (badgeSet.has(key) ? " badge" : "");
      r.textContent = c; r.title = c; r.dataset.key = key;
      rowEl[key] = r; r.addEventListener("click", ev => { ev.stopPropagation(); toggle(key); });
      body.appendChild(r);
    });
    card.appendChild(body); canvas.appendChild(card);
    bodyEl[t.id] = body; body.addEventListener("scroll", scheduleDraw);
    cardEl[t.id] = card;
  });

  function relayout() {
    let maxX=0, maxY=0;
    depthKeys.forEach(d => {
      const x = PAD + d*(CARD_W+GAP_X); let y = PAD;
      groups[d].forEach(t => {
        const ncol = t.columns.length;
        const noBody = (mode[t.id]==="collapsed") || ncol===0;
        let bh;
        if (noBody) bh = 0;
        else if (mode[t.id]==="expanded") bh = Math.max(ROW_H, ncol*ROW_H);
        else bh = Math.min(BODY_MAX, Math.max(ROW_H, ncol*ROW_H));
        pos[t.id] = {x, y}; bodyH[t.id] = bh;
        const card = cardEl[t.id];
        card.style.left = x+"px"; card.style.top = y+"px";
        const body = bodyEl[t.id];
        body.style.display = noBody ? "none" : "";
        body.style.maxHeight = bh+"px"; body.style.height = bh+"px";
        y += HEADER_H + bh + GAP_Y;
        maxY = Math.max(maxY, y);
      });
      maxX = Math.max(maxX, x + CARD_W);
    });
    canvas.style.width  = (maxX + PAD) + "px";
    canvas.style.height = Math.max(maxY, H - BAR_H) + "px";
    svg.setAttribute("width", canvas.style.width);
    svg.setAttribute("height", canvas.style.height);
    draw();
  }

  // connectors
  const labelG = document.createElementNS(NS, "g");
  const links = [];
  edges.forEach(e => {
    if (!byId[e.s] || !byId[e.t]) return;
    const si = (e.sc === "" ? -1 : colIndex[e.s][e.sc]);
    const ti = (e.tc === "" ? -1 : colIndex[e.t][e.tc]);
    if (si == null || ti == null) return;
    const st = e.status || "";
    const scls = st === "ADDED" ? " add" : st === "REMOVED" ? " rem" : st === "TRANSFORM_CHANGED" ? " chg" : "";
    const path = document.createElementNS(NS, "path"); path.setAttribute("class","cardmap-edge"+scls);
    svg.appendChild(path);
    const lab = document.createElementNS(NS, "text");
    lab.setAttribute("class","cardmap-elabel"); lab.style.display = "none";
    links.push({el:path, lab, tr:(e.tr||""), s:e.s, si, t:e.t, ti, sk:e.s+SEP+e.sc, tk:e.t+SEP+e.tc});
  });
  svg.appendChild(labelG);
  links.forEach(L => labelG.appendChild(L.lab));
  function anchorY(tid, ci) {
    const p = pos[tid]; if (!p) return 0;
    const t = byId[tid];
    if (ci < 0 || !t || !t.columns.length || mode[tid]==="collapsed") return p.y + HEADER_H/2;
    const bt = p.y + HEADER_H, bh = bodyH[tid];
    const st = bodyEl[tid] ? bodyEl[tid].scrollTop : 0;
    const y = bt + ci*ROW_H + ROW_H/2 - st;
    return Math.max(bt+3, Math.min(bt+bh-3, y));
  }
  function draw() {
    links.forEach(L => {
      const s = pos[L.s], t = pos[L.t]; if (!s || !t) return;
      const x1 = s.x + CARD_W, y1 = anchorY(L.s, L.si);
      const x2 = t.x,          y2 = anchorY(L.t, L.ti);
      const dx = Math.max(30, Math.abs(x2 - x1) / 2);
      L.el.setAttribute("d", `M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`);
      if (L.lab.style.display !== "none") {
        L.lab.setAttribute("x", (x1 + x2) / 2);
        L.lab.setAttribute("y", (y1 + y2) / 2 - 4);
      }
    });
  }
  let raf = null;
  function scheduleDraw() { if (raf) return; raf = requestAnimationFrame(() => { raf=null; draw(); }); }

  // click a column -> highlight the whole UPSTREAM chain feeding it (previous cards)
  const incoming = {}; links.forEach((L,i) => { (incoming[L.tk]=incoming[L.tk]||[]).push(i); });
  let active = null;
  function clearHl() {
    canvas.classList.remove("has-active");
    links.forEach(L => { L.el.classList.remove("hl"); L.lab.style.display = "none"; });
    Object.keys(rowEl).forEach(k => rowEl[k].classList.remove("hl","hl2"));
  }
  function trunc(s, n) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "\u2026" : s; }
  function toggle(key) {
    if (active === key) { clearHl(); active = null; return; }
    clearHl(); active = key; canvas.classList.add("has-active");
    if (rowEl[key]) rowEl[key].classList.add("hl");
    const eSet = new Set(), rSet = new Set([key]); const stack = [key];
    while (stack.length) {
      const k = stack.pop();
      (incoming[k]||[]).forEach(i => {
        if (!eSet.has(i)) { eSet.add(i); const L = links[i]; rSet.add(L.sk); stack.push(L.sk); }
      });
    }
    eSet.forEach(i => {
      const L = links[i];
      L.el.classList.add("hl");
      if (L.tr) {
        while (L.lab.firstChild) L.lab.removeChild(L.lab.firstChild);
        L.lab.appendChild(document.createTextNode(trunc(L.tr, 48)));
        const tt = document.createElementNS(NS, "title"); tt.textContent = L.tr; L.lab.appendChild(tt);
        L.lab.style.display = "";
      }
    });
    rSet.forEach(k => { if (k !== key && rowEl[k]) rowEl[k].classList.add("hl2"); });
    scheduleDraw();
  }
  wrap.addEventListener("click", ev => {
    if (ev.target === wrap || ev.target === canvas || ev.target === svg) { clearHl(); active = null; }
  });

  // toolbar
  bExp.onclick = () => { tables.forEach(t => { mode[t.id]="expanded"; cardEl[t.id].classList.remove("collapsed"); }); relayout(); };
  bCol.onclick = () => { tables.forEach(t => { mode[t.id]="collapsed"; cardEl[t.id].classList.add("collapsed"); }); relayout(); };
  bClr.onclick = () => { clearHl(); active = null; };
  bFs.onclick  = () => {
    const fs = shell.classList.toggle("fs");
    wrap.style.height = (fs ? window.innerHeight - BAR_H - 14 : H - BAR_H) + "px";
    bFs.textContent = fs ? "\u2716 Exit full screen" : "\u26f6 Full screen";
    relayout();
  };
  filt.addEventListener("input", () => {
    const term = filt.value.trim().toLowerCase();
    Object.keys(rowEl).forEach(k => {
      const el = rowEl[k];
      el.classList.toggle("fltout", !!term && !el.textContent.toLowerCase().includes(term));
    });
  });

  wrap.addEventListener("scroll", scheduleDraw);
  window.addEventListener("resize", scheduleDraw);
  relayout();
  return function () { window.removeEventListener("resize", scheduleDraw); };
}
"""


def _get_cards_component():
    """Register the dependency-free column card-map CCv2 component (reload-safe, no vendored libs)."""
    if not _CCV2_OK:
        return None
    if "_sf_cards_comp_v2" in st.session_state:
        return st.session_state["_sf_cards_comp_v2"]
    comp = None
    try:
        comp = st.components.v2.component(
            "snowflow_cards_v2",
            html="<style>" + _CARD_CSS + "</style><div id='sf-cards'></div>",
            js=_CARD_RENDERER_JS,
        )
    except Exception:  # noqa: BLE001
        comp = None
    st.session_state["_sf_cards_comp_v2"] = comp
    return comp


def _dot_id(name: str) -> str:
    return '"' + str(name).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _split_db(obj: str) -> str:
    return str(obj).split(".")[0]


def _short_name(obj: str) -> str:
    parts = str(obj).split(".")
    return ".".join(parts[1:]) if len(parts) > 1 else str(obj)


# Scale guards: bound what the browser renders so a hub object (hundreds of neighbours)
# can't freeze the tab. Edges are kept in query order (nearest hops / highest movement first).
_MAX_CARDS = 80
_MAX_EDGES = 600


def _apply_caps(tables: list, cedges: list, focus_id: str = None):
    """Trim tables/edges to the render caps; keep the focus and nearest edges. Returns
    (tables, cedges, truncated)."""
    truncated = False
    if len(cedges) > _MAX_EDGES:
        cedges = cedges[:_MAX_EDGES]
        truncated = True
    used = {e["s"] for e in cedges} | {e["t"] for e in cedges}
    if focus_id:
        used.add(focus_id)
    kept = [t for t in tables if t["id"] in used] or tables
    if len(kept) > _MAX_CARDS:
        keep_ids = {t["id"] for t in kept[:_MAX_CARDS]}
        if focus_id:
            keep_ids.add(focus_id)
        kept = [t for t in tables if t["id"] in keep_ids]
        cedges = [e for e in cedges if e["s"] in keep_ids and e["t"] in keep_ids]
        truncated = True
    return kept, cedges, truncated


def _cards_dot(tables: list, cedges: list) -> str:
    """Dark-themed Graphviz twin for the card payload (fallback when CCv2 is unavailable).
    Cards with columns render as clusters; header-only cards (DB map) render as nodes."""
    dot = ["digraph G {", '  rankdir=LR; bgcolor="#0e1117"; compound=true;',
           '  node [shape=box style="rounded,filled" fillcolor="#161b26" color="#2a3346" '
           'fontcolor="#e6e8eb" fontname="Source Sans Pro" fontsize=10];',
           '  edge [color="#e8a33d" arrowsize=0.7];']
    for i, t in enumerate(tables):
        cols = t.get("columns") or []
        border = "#e8a33d" if t.get("kind") == "focus" else "#4c8dff"
        if cols:
            dot.append(f'  subgraph cluster_{i} {{ label={_dot_id(t["label"])}; style="rounded"; '
                       f'color="{border}"; fontcolor="#c7ced9"; fontsize=11;')
            for c in cols:
                dot.append(f'    {_dot_id(t["id"] + "." + c)} [label={_dot_id(c)}];')
            dot.append("  }")
        else:
            dot.append(f'  {_dot_id(t["id"])} [label={_dot_id(t["label"])} '
                       f'fillcolor="#1b2130" color="{border}"];')
    for e in cedges:
        if e.get("sc"):
            dot.append(f'  {_dot_id(e["s"] + "." + e["sc"])} -> {_dot_id(e["t"] + "." + e["tc"])};')
        else:
            dot.append(f'  {_dot_id(e["s"])} -> {_dot_id(e["t"])};')
    dot.append("}")
    return "\n".join(dot)


def _cards_spec(tables: list, cedges: list, focus_id: str = None) -> dict:
    tables, cedges, truncated = _apply_caps(tables, cedges, focus_id)
    return {"kind": "cardmap", "cards": {"tables": tables, "edges": cedges},
            "dot": _cards_dot(tables, cedges), "truncated": truncated,
            "n_cards": len(tables), "n_edges": len(cedges)}


def column_card_graph(df, focus_object: str = None, badges=None) -> dict:
    """dbt-style column card lineage from a column-edge DataFrame with columns
    FROM_OBJECT, FROM_COLUMN, TO_OBJECT, TO_COLUMN (FROM = downstream, TO = upstream source).
    Optional TRANSFORMATION column is shown on the highlighted edges; optional STATUS column
    (ADDED/REMOVED/TRANSFORM_CHANGED) colors edges for diffs. `badges` is a set of
    "OBJECT\\x01COLUMN" keys to mark (semantic exposure)."""
    if df is None or df.empty:
        return {"kind": "cardmap", "cards": None, "dot": None, "truncated": False}
    order, edges = {}, []
    has_tr = "TRANSFORMATION" in df.columns
    has_st = "STATUS" in df.columns
    for _, r in df.iterrows():
        fo, fc = str(r["FROM_OBJECT"]), str(r["FROM_COLUMN"])
        to, tc = str(r["TO_OBJECT"]), str(r["TO_COLUMN"])
        tr = str(r["TRANSFORMATION"]) if has_tr and r["TRANSFORMATION"] is not None else ""
        order.setdefault(fo, set()).add(fc)
        order.setdefault(to, set()).add(tc)
        edge = {"s": to, "sc": tc, "t": fo, "tc": fc, "tr": tr}
        if has_st and r["STATUS"] is not None:
            edge["status"] = str(r["STATUS"])
        edges.append(edge)
    tables = [{"id": o, "label": o,
               "kind": "focus" if (focus_object and o == focus_object) else "table",
               "columns": sorted(cols)} for o, cols in order.items()]
    spec = _cards_spec(tables, edges, focus_id=focus_object)
    if badges and spec.get("cards"):
        spec["cards"]["badges"] = sorted(badges)
    return spec


def table_card_graph(df, focus_object: str = None) -> dict:
    """Card lineage for table->table: card = database, row = table (schema.table),
    edges connect table rows across database cards. df: SOURCE_OBJECT, TARGET_OBJECT."""
    if df is None or df.empty:
        return {"kind": "cardmap", "cards": None, "dot": None, "truncated": False}
    order, edges = {}, []
    for _, r in df.iterrows():
        so, to = str(r["SOURCE_OBJECT"]), str(r["TARGET_OBJECT"])
        sdb, tdb = _split_db(so), _split_db(to)
        ss, ts = _short_name(so), _short_name(to)
        order.setdefault(sdb, set()).add(ss)
        order.setdefault(tdb, set()).add(ts)
        edges.append({"s": sdb, "sc": ss, "t": tdb, "tc": ts})
    focus_db = _split_db(focus_object) if focus_object else None
    tables = [{"id": db, "label": db,
               "kind": "focus" if (focus_db and db == focus_db) else "table",
               "columns": sorted(cols)} for db, cols in order.items()]
    return _cards_spec(tables, edges, focus_id=focus_db)


def db_card_graph(df, focus_db: str = None) -> dict:
    """Card lineage for database->database: each database is a header-only card;
    edges anchor to card headers. df: SOURCE_DATABASE, TARGET_DATABASE."""
    if df is None or df.empty:
        return {"kind": "cardmap", "cards": None, "dot": None, "truncated": False}
    dbs, seen, edges = [], set(), []
    for _, r in df.iterrows():
        s, t = str(r["SOURCE_DATABASE"]), str(r["TARGET_DATABASE"])
        for d in (s, t):
            if d not in seen:
                seen.add(d)
                dbs.append(d)
        edges.append({"s": s, "sc": "", "t": t, "tc": ""})
    tables = [{"id": db, "label": db,
               "kind": "focus" if (focus_db and db == focus_db) else "table",
               "columns": []} for db in dbs]
    return _cards_spec(tables, edges, focus_id=focus_db)


def render_cards(spec: dict, height: int = 600, key: str = None):
    """Render a column card map with the custom HTML/SVG component (fixed-height cards, a
    scrollable column list, and click-a-column to highlight its links). Falls back to a
    Graphviz clustered diagram when CCv2 is unavailable."""
    if not spec or not spec.get("cards"):
        st.info("No lineage to map for the current selection and window.")
        return
    if spec.get("truncated"):
        st.caption(f"Large graph - showing the {spec.get('n_cards', 0)} nearest cards / "
                   f"{spec.get('n_edges', 0)} edges. Narrow the scope (lower depth, pick a column) "
                   "for the full picture.")
    comp = _get_cards_component()
    if comp is not None:
        comp(data={"cards": spec["cards"], "height": height}, key=key or "cards")
        return
    dot = spec.get("dot")
    if dot:
        st.graphviz_chart(dot, use_container_width=True)
        st.caption("Rendered with Graphviz - CCv2 (st.components.v2) is not available in this runtime.")
    else:
        st.info("No lineage to map for the current selection and window.")
