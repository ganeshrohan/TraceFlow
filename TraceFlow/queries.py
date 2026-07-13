
"""All SQL is parameterized with qmark (?) value binds. Identifiers are never
interpolated from raw user text; DB/schema/object choices are passed as bound
ILIKE/equality VALUES, which is injection-safe."""
from datasource import run_query, run_meta
import re
import pandas as pd


def _days(n) -> int:
    """Coerce lookback to a safe integer for inlining into DATEADD."""
    try:
        return max(1, min(365, int(n)))
    except (TypeError, ValueError):
        return 365


# ---------------------------------------------------------------------------
# Cascading filter selectors (DB -> Schema -> Object)
# ---------------------------------------------------------------------------
def list_databases():
    df = run_query(
        "SELECT DATABASE_NAME FROM SNOWFLAKE.ACCOUNT_USAGE.DATABASES "
        "WHERE DELETED IS NULL ORDER BY DATABASE_NAME"
    )
    return df["DATABASE_NAME"].tolist()


def list_schemas(database: str):
    df = run_query(
        "SELECT SCHEMA_NAME FROM SNOWFLAKE.ACCOUNT_USAGE.SCHEMATA "
        "WHERE DELETED IS NULL AND CATALOG_NAME = ? ORDER BY SCHEMA_NAME",
        [database],
    )
    return df["SCHEMA_NAME"].tolist()


def list_objects(database: str, schema: str):
    df = run_query(
        """
        SELECT TABLE_NAME AS NAME,
               CASE WHEN IS_DYNAMIC = 'YES' THEN 'DYNAMIC TABLE' ELSE TABLE_TYPE END AS OBJECT_TYPE
        FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES
        WHERE DELETED IS NULL AND TABLE_CATALOG = ? AND TABLE_SCHEMA = ?
        ORDER BY TABLE_NAME
        """,
        [database, schema],
    )
    return df


# ---------------------------------------------------------------------------
# Catalog browser detail
# ---------------------------------------------------------------------------
def table_details(database: str, schema: str, table: str):
    return run_query(
        """
        SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME,
               CASE WHEN IS_DYNAMIC = 'YES' THEN 'DYNAMIC TABLE' ELSE TABLE_TYPE END AS OBJECT_TYPE,
               ROW_COUNT, BYTES, TABLE_OWNER, CREATED, LAST_ALTERED, COMMENT
        FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES
        WHERE DELETED IS NULL AND TABLE_CATALOG = ? AND TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """,
        [database, schema, table],
    )


def column_schema(database: str, schema: str, table: str):
    return run_query(
        """
        SELECT ORDINAL_POSITION, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT
        FROM SNOWFLAKE.ACCOUNT_USAGE.COLUMNS
        WHERE DELETED IS NULL AND TABLE_CATALOG = ? AND TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        [database, schema, table],
    )


def policy_tags(database: str, schema: str, table: str):
    """Classification / governance tags attached to the object or its columns."""
    return run_query(
        """
        SELECT TAG_NAME, TAG_VALUE, COLUMN_NAME, DOMAIN AS OBJECT_TYPE
        FROM SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES
        WHERE OBJECT_DATABASE = ? AND OBJECT_SCHEMA = ? AND OBJECT_NAME = ?
        ORDER BY COLUMN_NAME, TAG_NAME
        """,
        [database, schema, table],
    )


# ---------------------------------------------------------------------------
# DB <-> DB interaction (account-wide data movement, rolled up per database pair)
# ---------------------------------------------------------------------------
def db_to_db_movement(lookback_days=365):
    d = _days(lookback_days)
    return run_query(
        f"""
        WITH movement AS (
            SELECT
                SPLIT_PART(src.value:objectName::STRING, '.', 1) AS source_database,
                SPLIT_PART(tgt.value:objectName::STRING, '.', 1) AS target_database,
                ah.query_id
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.base_objects_accessed) AS src,
                 LATERAL FLATTEN(input => ah.objects_modified)      AS tgt
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND src.value:objectName::STRING IS NOT NULL
              AND tgt.value:objectName::STRING IS NOT NULL
        )
        SELECT source_database, target_database,
               COUNT(DISTINCT query_id) AS movement_count
        FROM movement
        WHERE source_database <> target_database
        GROUP BY source_database, target_database
        ORDER BY movement_count DESC
        """
    )




def db_lineage_recursive(focus_db: str, lookback_days=365, max_hops=10):
    """Complete recursive DB->DB lineage around a focus database.

    Walks the cross-database movement graph both ways from focus_db:
      UPSTREAM   = every ancestor chain that eventually feeds focus_db
      DOWNSTREAM = every descendant chain fed by focus_db
    Returns one row per edge: DIRECTION, SOURCE_DATABASE, TARGET_DATABASE, HOP
    (distance from focus), MOVEMENT_COUNT. Cycle-safe via a delimited path guard.
    """
    d = _days(lookback_days)
    h = max(1, min(int(max_hops), 15))
    return run_query(
        f"""
        WITH RECURSIVE
        mv AS (
            SELECT
                SPLIT_PART(src.value:objectName::STRING, '.', 1) AS src_db,
                SPLIT_PART(tgt.value:objectName::STRING, '.', 1) AS tgt_db,
                ah.query_id
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.base_objects_accessed) AS src,
                 LATERAL FLATTEN(input => ah.objects_modified)      AS tgt
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND src.value:objectName::STRING IS NOT NULL
              AND tgt.value:objectName::STRING IS NOT NULL
        ),
        edges AS (
            SELECT src_db, tgt_db, COUNT(DISTINCT query_id) AS cnt
            FROM mv
            WHERE src_db <> tgt_db
            GROUP BY src_db, tgt_db
        ),
        up AS (
            SELECT src_db, tgt_db, cnt, 1 AS hop,
                   '/' || tgt_db || '/' || src_db || '/' AS path
            FROM edges
            WHERE tgt_db = ?
            UNION ALL
            SELECT e.src_db, e.tgt_db, e.cnt, u.hop + 1,
                   u.path || e.src_db || '/'
            FROM edges e
            JOIN up u ON e.tgt_db = u.src_db
            WHERE u.hop < {h}
              AND POSITION('/' || e.src_db || '/' IN u.path) = 0
        ),
        down AS (
            SELECT src_db, tgt_db, cnt, 1 AS hop,
                   '/' || src_db || '/' || tgt_db || '/' AS path
            FROM edges
            WHERE src_db = ?
            UNION ALL
            SELECT e.src_db, e.tgt_db, e.cnt, dn.hop + 1,
                   dn.path || e.tgt_db || '/'
            FROM edges e
            JOIN down dn ON e.src_db = dn.tgt_db
            WHERE dn.hop < {h}
              AND POSITION('/' || e.tgt_db || '/' IN dn.path) = 0
        )
        SELECT 'UPSTREAM' AS direction, src_db AS source_database,
               tgt_db AS target_database, MIN(hop) AS hop, MAX(cnt) AS movement_count
        FROM up
        GROUP BY src_db, tgt_db
        UNION ALL
        SELECT 'DOWNSTREAM' AS direction, src_db AS source_database,
               tgt_db AS target_database, MIN(hop) AS hop, MAX(cnt) AS movement_count
        FROM down
        GROUP BY src_db, tgt_db
        ORDER BY direction, hop
        """,
        [focus_db, focus_db],
    )


def db_node_details(lookback_days=365):
    """Per-database owner, consumers (readers) and writers over the window - used to
    annotate every database node appearing in the recursive lineage chain."""
    d = _days(lookback_days)
    return run_query(
        f"""
        WITH readers AS (
            SELECT DISTINCT SPLIT_PART(base.value:objectName::STRING, '.', 1) AS db, ah.user_name
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.base_objects_accessed) AS base
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND base.value:objectName::STRING IS NOT NULL
        ),
        modifiers AS (
            SELECT DISTINCT SPLIT_PART(m.value:objectName::STRING, '.', 1) AS db, ah.user_name
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.objects_modified) AS m
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND m.value:objectName::STRING IS NOT NULL
        ),
        cons AS (
            SELECT db, COUNT(DISTINCT user_name) AS consumer_count,
                   LISTAGG(DISTINCT user_name, ', ') WITHIN GROUP (ORDER BY user_name) AS consumers
            FROM readers GROUP BY db
        ),
        wr AS (
            SELECT db, COUNT(DISTINCT user_name) AS writer_count,
                   LISTAGG(DISTINCT user_name, ', ') WITHIN GROUP (ORDER BY user_name) AS writers
            FROM modifiers GROUP BY db
        ),
        dbs AS (
            SELECT DISTINCT db FROM readers
            UNION
            SELECT DISTINCT db FROM modifiers
        )
        SELECT
            dbs.db                        AS database_name,
            d.DATABASE_OWNER              AS owner,
            COALESCE(c.consumer_count, 0) AS consumer_count,
            c.consumers,
            COALESCE(w.writer_count, 0)   AS writer_count,
            w.writers
        FROM dbs
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.DATABASES d
               ON d.DATABASE_NAME = dbs.db AND d.DELETED IS NULL
        LEFT JOIN cons c ON c.db = dbs.db
        LEFT JOIN wr   w ON w.db = dbs.db
        """
    )


# ---------------------------------------------------------------------------
# Table <-> Table interaction (object dependency graph, scoped by DB/schema)
# ---------------------------------------------------------------------------
def table_to_table(database: str = None, schema: str = None):
    where = ["1=1"]
    params = []
    if database:
        where.append("(REFERENCED_DATABASE = ? OR REFERENCING_DATABASE = ?)")
        params += [database, database]
    if schema:
        where.append("(REFERENCED_SCHEMA = ? OR REFERENCING_SCHEMA = ?)")
        params += [schema, schema]
    clause = " AND ".join(where)
    return run_query(
        f"""
        SELECT
            REFERENCED_DATABASE || '.' || REFERENCED_SCHEMA || '.' || REFERENCED_OBJECT_NAME AS source_object,
            REFERENCED_OBJECT_DOMAIN AS source_domain,
            REFERENCING_DATABASE || '.' || REFERENCING_SCHEMA || '.' || REFERENCING_OBJECT_NAME AS target_object,
            REFERENCING_OBJECT_DOMAIN AS target_domain,
            DEPENDENCY_TYPE,
            (REFERENCED_DATABASE <> REFERENCING_DATABASE) AS crosses_database
        FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES
        WHERE {clause}
        ORDER BY source_object, target_object
        """,
        params or None,
    )


def table_lineage_recursive(focus_object: str, lookback_days=365, max_hops=10):
    """Complete recursive TABLE/OBJECT-level lineage around a focus object, built from
    ACCESS_HISTORY data movement (so it captures INSERT INTO ... SELECT, CTAS, MERGE - not
    just view/DDL dependencies like OBJECT_DEPENDENCIES).

      UPSTREAM   = every object chain that feeds writes into focus_object
      DOWNSTREAM = every object chain fed by reads of focus_object
    Returns per-edge: DIRECTION, SOURCE_OBJECT, TARGET_OBJECT, HOP, MOVEMENT_COUNT.
    Cycle-safe via a delimited path guard.
    """
    d = _days(lookback_days)
    h = max(1, min(int(max_hops), 15))
    return run_query(
        f"""
        WITH RECURSIVE
        mv AS (
            SELECT base.value:objectName::STRING AS src_obj,
                   m.value:objectName::STRING    AS tgt_obj,
                   ah.query_id
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.base_objects_accessed) AS base,
                 LATERAL FLATTEN(input => ah.objects_modified)      AS m
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND base.value:objectName::STRING IS NOT NULL
              AND m.value:objectName::STRING IS NOT NULL
        ),
        edges AS (
            SELECT src_obj, tgt_obj, COUNT(DISTINCT query_id) AS cnt
            FROM mv WHERE src_obj <> tgt_obj
            GROUP BY src_obj, tgt_obj
        ),
        up AS (
            SELECT src_obj, tgt_obj, cnt, 1 AS hop,
                   '|' || tgt_obj || '|' || src_obj || '|' AS path
            FROM edges WHERE tgt_obj = ?
            UNION ALL
            SELECT e.src_obj, e.tgt_obj, e.cnt, u.hop + 1, u.path || e.src_obj || '|'
            FROM edges e JOIN up u ON e.tgt_obj = u.src_obj
            WHERE u.hop < {h} AND POSITION('|' || e.src_obj || '|' IN u.path) = 0
        ),
        down AS (
            SELECT src_obj, tgt_obj, cnt, 1 AS hop,
                   '|' || src_obj || '|' || tgt_obj || '|' AS path
            FROM edges WHERE src_obj = ?
            UNION ALL
            SELECT e.src_obj, e.tgt_obj, e.cnt, dn.hop + 1, dn.path || e.tgt_obj || '|'
            FROM edges e JOIN down dn ON e.src_obj = dn.tgt_obj
            WHERE dn.hop < {h} AND POSITION('|' || e.tgt_obj || '|' IN dn.path) = 0
        )
        SELECT 'UPSTREAM' AS direction, src_obj AS source_object,
               tgt_obj AS target_object, MIN(hop) AS hop, MAX(cnt) AS movement_count
        FROM up GROUP BY src_obj, tgt_obj
        UNION ALL
        SELECT 'DOWNSTREAM' AS direction, src_obj AS source_object,
               tgt_obj AS target_object, MIN(hop) AS hop, MAX(cnt) AS movement_count
        FROM down GROUP BY src_obj, tgt_obj
        ORDER BY direction, hop
        """,
        [focus_object, focus_object],
    )


# ---------------------------------------------------------------------------
# GET_LINEAGE object drill-down (upstream + downstream) for a specific object
# ---------------------------------------------------------------------------
def object_lineage(fq_object: str, direction: str, distance: int = 5):
    dir_up = direction.upper()
    if dir_up not in ("UPSTREAM", "DOWNSTREAM"):
        raise ValueError("direction must be UPSTREAM or DOWNSTREAM")
    dist = max(1, min(5, int(distance)))
    return run_query(
        f"""
        SELECT DISTANCE,
               SOURCE_OBJECT_DATABASE || '.' || SOURCE_OBJECT_SCHEMA || '.' || SOURCE_OBJECT_NAME AS source_object,
               SOURCE_OBJECT_DOMAIN AS source_domain,
               TARGET_OBJECT_DATABASE || '.' || TARGET_OBJECT_SCHEMA || '.' || TARGET_OBJECT_NAME AS target_object,
               TARGET_OBJECT_DOMAIN AS target_domain
        FROM TABLE(SNOWFLAKE.CORE.GET_LINEAGE(?, 'TABLE', '{dir_up}', {dist}))
        ORDER BY DISTANCE
        """,
        [fq_object],
    )


# ---------------------------------------------------------------------------
# Writers (DML) and Consumers for a specific object
# ---------------------------------------------------------------------------
def writers(fq_object: str, lookback_days=365):
    d = _days(lookback_days)
    return run_query(
        f"""
        WITH w AS (
            SELECT ah.query_id, ah.user_name, ah.query_start_time,
                   modified.value:objectName::STRING AS written_object
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.objects_modified) AS modified
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND modified.value:objectName::STRING = ?
        )
        SELECT w.user_name, qh.role_name, qh.warehouse_name,
               COUNT(DISTINCT w.query_id) AS write_count,
               MAX(w.query_start_time)    AS last_write,
               ANY_VALUE(qh.query_text)   AS sample_sql
        FROM w
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qh.query_id = w.query_id
        GROUP BY w.user_name, qh.role_name, qh.warehouse_name
        ORDER BY write_count DESC
        """,
        [fq_object],
    )


def consumers(fq_object: str, lookback_days=365):
    d = _days(lookback_days)
    return run_query(
        f"""
        WITH r AS (
            SELECT ah.query_id, ah.user_name, ah.query_start_time,
                   base.value:objectName::STRING AS accessed_object
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.base_objects_accessed) AS base
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND base.value:objectName::STRING = ?
        )
        SELECT r.user_name, qh.role_name, qh.warehouse_name,
               COUNT(DISTINCT r.query_id) AS read_count,
               MAX(r.query_start_time)    AS last_read,
               ANY_VALUE(qh.query_text)   AS sample_sql
        FROM r
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qh.query_id = r.query_id
        GROUP BY r.user_name, qh.role_name, qh.warehouse_name
        ORDER BY read_count DESC
        """,
        [fq_object],
    )


# ---------------------------------------------------------------------------
# Column-level lineage edges for one table (all target columns + direct sources + full SQL)
# ---------------------------------------------------------------------------
def column_edges_for_table(fq_object: str, lookback_days=365):
    d = _days(lookback_days)
    return run_query(
        f"""
        WITH tc AS (
            SELECT ah.query_id, ah.query_start_time,
                   c.value:columnName::STRING  AS target_column,
                   ds.value:objectName::STRING AS source_object,
                   ds.value:columnName::STRING AS source_column
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.objects_modified) AS m,
                 LATERAL FLATTEN(input => m.value:columns)     AS c,
                 LATERAL FLATTEN(input => c.value:directSources, outer => true) AS ds
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP())
              AND m.value:objectName::STRING = ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY c.value:columnName::STRING,
                             ds.value:objectName::STRING, ds.value:columnName::STRING
                ORDER BY ah.query_start_time DESC) = 1
        )
        SELECT tc.target_column, tc.source_object, tc.source_column,
               tc.query_id, qh.query_text AS full_sql, qh.role_name, tc.query_start_time
        FROM tc
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qh.query_id = tc.query_id
        ORDER BY tc.target_column, tc.source_object, tc.source_column
        """,
        [fq_object],
    )


# ---------------------------------------------------------------------------
# Recursive multi-hop column lineage for one column
# ---------------------------------------------------------------------------


def column_lineage_all(fq_object: str, lookback_days=365, max_hops=10, as_of=None):
    """Recursive multi-hop column lineage for EVERY column of the main table at once.

    Anchors on all columns written into fq_object, then walks upstream through every
    intermediate table. ROOT_COLUMN is the main-table column each edge ultimately feeds.
    `as_of` (timestamp string) caps ACCESS_HISTORY at that time for point-in-time lineage.
    Returns: ROOT_COLUMN, HOP, FROM_OBJECT, FROM_COLUMN, TO_OBJECT, TO_COLUMN,
    LINEAGE_PATH, EDGE_SQL. (FROM = downstream side of the edge, TO = upstream source.)
    """
    d = _days(lookback_days)
    hops = max(1, min(20, int(max_hops)))
    asof = "AND ah.query_start_time <= TO_TIMESTAMP_LTZ(?)" if as_of else ""
    params = ([str(as_of)] if as_of else []) + [fq_object]
    return run_query(
        f"""
        WITH RECURSIVE col_edges AS (
            SELECT
                m.value:objectName::STRING  AS tgt_obj,
                c.value:columnName::STRING  AS tgt_col,
                ds.value:objectName::STRING AS src_obj,
                ds.value:columnName::STRING AS src_col,
                ah.query_id                 AS edge_query_id
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.objects_modified) AS m,
                 LATERAL FLATTEN(input => m.value:columns)     AS c,
                 LATERAL FLATTEN(input => c.value:directSources, outer => true) AS ds
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP()) {asof}
              AND m.value:objectName::STRING  IS NOT NULL
              AND ds.value:columnName::STRING IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY m.value:objectName::STRING, c.value:columnName::STRING,
                             ds.value:objectName::STRING, ds.value:columnName::STRING
                ORDER BY ah.query_start_time DESC) = 1
        ),
        lineage AS (
            SELECT 1 AS hop, tgt_col AS root_column,
                   tgt_obj AS from_object, tgt_col AS from_column,
                   src_obj AS to_object, src_col AS to_column, edge_query_id,
                   tgt_obj || '.' || tgt_col || '  <-  ' || src_obj || '.' || src_col AS lineage_path
            FROM col_edges
            WHERE tgt_obj = ?
            UNION ALL
            SELECT l.hop + 1, l.root_column, e.tgt_obj, e.tgt_col, e.src_obj, e.src_col, e.edge_query_id,
                   l.lineage_path || '  <-  ' || e.src_obj || '.' || e.src_col
            FROM col_edges e
            JOIN lineage l ON e.tgt_obj = l.to_object AND e.tgt_col = l.to_column
            WHERE l.hop < {hops}
              AND POSITION(e.src_obj || '.' || e.src_col IN l.lineage_path) = 0
        )
        SELECT l.root_column, l.hop, l.from_object, l.from_column, l.to_object, l.to_column,
               l.lineage_path, qh.query_text AS edge_sql
        FROM lineage l
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qh.query_id = l.edge_query_id
        ORDER BY l.root_column, l.hop
        """,
        params,
    )


def column_lineage_all_downstream(fq_object: str, lookback_days=365, max_hops=10, as_of=None):
    """Recursive multi-hop DOWNSTREAM column lineage for every column of the focus object.

    Anchors on columns whose source is fq_object and walks forward through every consuming
    table. ROOT_COLUMN is the focus column the chain originates from. `as_of` caps
    ACCESS_HISTORY at a timestamp for point-in-time lineage. Returns the same shape as
    column_lineage_all (FROM = downstream consumer, TO = upstream source)."""
    d = _days(lookback_days)
    hops = max(1, min(20, int(max_hops)))
    asof = "AND ah.query_start_time <= TO_TIMESTAMP_LTZ(?)" if as_of else ""
    params = ([str(as_of)] if as_of else []) + [fq_object]
    return run_query(
        f"""
        WITH RECURSIVE col_edges AS (
            SELECT
                m.value:objectName::STRING  AS tgt_obj,
                c.value:columnName::STRING  AS tgt_col,
                ds.value:objectName::STRING AS src_obj,
                ds.value:columnName::STRING AS src_col,
                ah.query_id                 AS edge_query_id
            FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
                 LATERAL FLATTEN(input => ah.objects_modified) AS m,
                 LATERAL FLATTEN(input => m.value:columns)     AS c,
                 LATERAL FLATTEN(input => c.value:directSources, outer => true) AS ds
            WHERE ah.query_start_time >= DATEADD(day, -{d}, CURRENT_TIMESTAMP()) {asof}
              AND m.value:objectName::STRING  IS NOT NULL
              AND ds.value:columnName::STRING IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY m.value:objectName::STRING, c.value:columnName::STRING,
                             ds.value:objectName::STRING, ds.value:columnName::STRING
                ORDER BY ah.query_start_time DESC) = 1
        ),
        lineage AS (
            SELECT 1 AS hop, src_col AS root_column,
                   tgt_obj AS from_object, tgt_col AS from_column,
                   src_obj AS to_object, src_col AS to_column, edge_query_id,
                   src_obj || '.' || src_col || '  ->  ' || tgt_obj || '.' || tgt_col AS lineage_path
            FROM col_edges
            WHERE src_obj = ?
            UNION ALL
            SELECT l.hop + 1, l.root_column, e.tgt_obj, e.tgt_col, e.src_obj, e.src_col, e.edge_query_id,
                   l.lineage_path || '  ->  ' || e.tgt_obj || '.' || e.tgt_col
            FROM col_edges e
            JOIN lineage l ON e.src_obj = l.from_object AND e.src_col = l.from_column
            WHERE l.hop < {hops}
              AND POSITION(e.tgt_obj || '.' || e.tgt_col IN l.lineage_path) = 0
        )
        SELECT l.root_column, l.hop, l.from_object, l.from_column, l.to_object, l.to_column,
               l.lineage_path, qh.query_text AS edge_sql
        FROM lineage l
        LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qh.query_id = l.edge_query_id
        ORDER BY l.root_column, l.hop
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Semantic view -> physical column overlay
# ---------------------------------------------------------------------------
def semantic_views():
    """List account semantic views as a DataFrame with FQN, NAME, DATABASE_NAME, SCHEMA_NAME."""
    empty = pd.DataFrame(columns=["FQN", "NAME", "DATABASE_NAME", "SCHEMA_NAME"])
    try:
        df = run_meta("SHOW SEMANTIC VIEWS IN ACCOUNT")
    except Exception:  # noqa: BLE001
        return empty
    if df is None or df.empty:
        return empty
    df = df.rename(columns={c: c.lower() for c in df.columns})
    if not {"name", "database_name", "schema_name"}.issubset(df.columns):
        return empty
    out = df[["name", "database_name", "schema_name"]].rename(
        columns={"name": "NAME", "database_name": "DATABASE_NAME", "schema_name": "SCHEMA_NAME"})
    out["FQN"] = out["DATABASE_NAME"] + "." + out["SCHEMA_NAME"] + "." + out["NAME"]
    return out.reset_index(drop=True)


def _qident(part: str) -> str:
    return '"' + str(part).replace('"', '""') + '"'


def semantic_view_mappings():
    """Resolve every semantic view's dimensions/metrics to the physical columns they reference.

    Returns rows: SEMANTIC_VIEW, ENTITY_TYPE (dimension/metric/fact), LOGICAL_NAME,
    PHYSICAL_OBJECT (db.schema.table), PHYSICAL_COLUMN, EXPRESSION.
    Uses DESCRIBE SEMANTIC VIEW (structured output); identifiers are quoted, never raw."""
    cols = ["SEMANTIC_VIEW", "ENTITY_TYPE", "LOGICAL_NAME", "PHYSICAL_OBJECT", "PHYSICAL_COLUMN", "EXPRESSION"]
    views = semantic_views()
    if views is None or views.empty:
        return pd.DataFrame(columns=cols)
    ref_re = re.compile(r"([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)")
    frames = []
    for _, v in views.iterrows():
        db, sc, nm = v["DATABASE_NAME"], v["SCHEMA_NAME"], v["NAME"]
        fqn_disp = f"{db}.{sc}.{nm}"
        fqn = f"{_qident(db)}.{_qident(sc)}.{_qident(nm)}"
        try:
            d = run_meta(f"DESCRIBE SEMANTIC VIEW {fqn}")
        except Exception:  # noqa: BLE001
            continue
        if d is None or d.empty:
            continue
        d = d.rename(columns={c: c.lower() for c in d.columns})
        need = {"object_kind", "object_name", "parent_entity", "property", "property_value"}
        if not need.issubset(d.columns):
            continue
        # alias (UPPER) -> physical db.schema.table
        alias_obj = {}
        for alias, g in d[d["object_kind"] == "TABLE"].groupby("object_name"):
            props = dict(zip(g["property"], g["property_value"]))
            adb, asc_, anm = (props.get("BASE_TABLE_DATABASE_NAME"),
                              props.get("BASE_TABLE_SCHEMA_NAME"), props.get("BASE_TABLE_NAME"))
            if adb and asc_ and anm:
                alias_obj[str(alias).upper()] = f"{adb}.{asc_}.{anm}"
        rows = []
        ent = d[d["object_kind"].isin(["DIMENSION", "METRIC", "FACT"])]
        for (kind, logical, alias), g in ent.groupby(["object_kind", "object_name", "parent_entity"]):
            props = dict(zip(g["property"], g["property_value"]))
            expr = str(props.get("EXPRESSION", "") or "")
            default_obj = alias_obj.get(str(alias).upper())
            seen = set()
            for m in ref_re.finditer(expr):
                obj = alias_obj.get(m.group(1).upper(), default_obj)
                if not obj:
                    continue
                phys_col = m.group(2).upper()
                if (obj, phys_col) in seen:
                    continue
                seen.add((obj, phys_col))
                rows.append({"SEMANTIC_VIEW": fqn_disp, "ENTITY_TYPE": str(kind).lower(),
                             "LOGICAL_NAME": str(logical), "PHYSICAL_OBJECT": obj,
                             "PHYSICAL_COLUMN": phys_col, "EXPRESSION": expr})
        if rows:
            frames.append(pd.DataFrame(rows))
    if frames:
        return pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True)
    return pd.DataFrame(columns=cols)
