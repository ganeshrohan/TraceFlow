# sqlglot-based per-column transformation extractor for Snowflow Portal, covering INSERT/CTAS, UPDATE and MERGE.
# Co-authored with CoCo
"""Maps each target column to its exact SQL expression by parsing query_text with sqlglot.

Handles INSERT ... SELECT, CREATE TABLE AS SELECT, UPDATE ... SET, and MERGE (matched
UPDATE + not-matched INSERT). If sqlglot is unavailable, SQLGLOT_OK is False and callers
show the full SQL only (no regex fallback)."""

DIALECT = "snowflake"

try:
    import sqlglot
    from sqlglot import exp
    SQLGLOT_OK = True
except Exception:  # noqa: BLE001
    SQLGLOT_OK = False


def _insert_columns(stmt):
    """Explicit INSERT column list (INSERT INTO t (c1, c2) ...) or None."""
    node = stmt.this
    if isinstance(node, exp.Schema):
        return [c.name.upper() for c in node.expressions]
    return None


def _projections(stmt):
    sel = stmt.expression if stmt.expression else stmt.find(exp.Select)
    if isinstance(sel, exp.Select):
        return sel.selects
    return []


def _expr_sql(projection) -> str:
    e = projection.unalias() if isinstance(projection, exp.Alias) else projection
    return e.sql(dialect=DIALECT)


def _add_set_assignments(out: dict, update_node) -> None:
    """Map each SET assignment (col = expr) of an UPDATE node into out."""
    for eq in (update_node.args.get("expressions") or []):
        try:
            col = eq.this.name.upper()
            if col:
                out[col] = eq.expression.sql(dialect=DIALECT)
        except Exception:  # noqa: BLE001
            continue


def parse_column_expressions(sql: str) -> dict:
    """Return {TARGET_COLUMN: expression_sql} for one statement (exact, via sqlglot).

    Covers INSERT/CTAS (SELECT projections), UPDATE (SET assignments) and MERGE
    (WHEN MATCHED UPDATE SET + WHEN NOT MATCHED INSERT VALUES). DELETE has no column
    outputs and yields {}."""
    if not sql or not SQLGLOT_OK:
        return {}
    try:
        stmt = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    try:
        if isinstance(stmt, (exp.Insert, exp.Create)):
            projs = _projections(stmt)
            ins_cols = _insert_columns(stmt) if isinstance(stmt, exp.Insert) else None
            if ins_cols and projs:
                for col, proj in zip(ins_cols, projs):
                    out[col] = _expr_sql(proj)
            else:
                for proj in projs:
                    name = (proj.alias_or_name or "").upper()
                    if name and name != "*":
                        out[name] = _expr_sql(proj)
        elif isinstance(stmt, exp.Update):
            _add_set_assignments(out, stmt)
        elif isinstance(stmt, exp.Merge):
            whens = stmt.args.get("whens")
            wlist = getattr(whens, "expressions", None) or (whens if isinstance(whens, list) else [])
            for w in wlist:
                then = w.args.get("then") if hasattr(w, "args") else None
                if isinstance(then, exp.Update):
                    _add_set_assignments(out, then)
                elif isinstance(then, exp.Insert):
                    # columns: Schema (INSERT INTO t(cols)) or Tuple (MERGE INSERT (cols) VALUES (...))
                    node = then.this
                    icols = ([c.name.upper() for c in node.expressions]
                             if isinstance(node, (exp.Schema, exp.Tuple)) else [])
                    val = then.expression
                    if isinstance(val, exp.Tuple):
                        vexprs = val.expressions
                    else:  # VALUES (...) -> exp.Values with tuple rows
                        rows = getattr(val, "expressions", None) or []
                        vexprs = (getattr(rows[0], "expressions", None) or []) if rows else []
                    for c, v in zip(icols, vexprs):
                        out[c] = v.sql(dialect=DIALECT)
    except Exception:  # noqa: BLE001
        return out
    return out


def expression_for(sql: str, target_column: str) -> str:
    """Expression for one target column, or a helpful placeholder."""
    col_map = parse_column_expressions(sql)
    return col_map.get(str(target_column).upper(), "(expression not parsed - see full SQL)")
