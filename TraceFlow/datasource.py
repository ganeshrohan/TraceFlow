# Snowflake connection adapter for Snowflow Portal: resolves st.connection (embedded identity when hosted, secrets.toml locally) and runs cached, parameterized queries.
# Co-authored with CoCo
import os
import streamlit as st
import pandas as pd

_CONN_NAME = "snowflake"


def get_connection():
    """Return a Streamlit Snowflake connection.

    - Inside Snowflake (hosted SiS / Workspace): uses the embedded session identity.
    - Local: resolves [connections.snowflake] from .streamlit/secrets.toml.
    No credentials are ever hard-coded; there is NO simulated-data fallback.
    """
    ttl = os.getenv("SNOWFLAKE_CONNECTION_TTL")
    return st.connection(_CONN_NAME, ttl=ttl)


def connection_status() -> dict:
    """Probe the connection. Returns {connected, user, role, warehouse, error}."""
    try:
        conn = get_connection()
        df = conn.query(
            "SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R, CURRENT_WAREHOUSE() AS W",
            ttl=0,
        )
        row = df.iloc[0]
        return {
            "connected": True,
            "user": row["U"],
            "role": row["R"],
            "warehouse": row["W"],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - surface any connection issue to the UI
        return {"connected": False, "user": None, "role": None, "warehouse": None, "error": str(exc)}


@st.cache_data(ttl=600, show_spinner=False)
def run_query(sql: str, params: list | tuple | None = None) -> pd.DataFrame:
    """Run a parameterized query (qmark ? binds) and return a DataFrame. Cached 10 min."""
    conn = get_connection()
    if params:
        return conn.query(sql, params=list(params), ttl=600)
    return conn.query(sql, ttl=600)


def clear_cache():
    """Invalidate cached query results (used by the sidebar refresh control)."""
    run_query.clear()


def run_meta(sql: str) -> pd.DataFrame:
    """Run a metadata command (SHOW / DESCRIBE) via the raw cursor.

    st.connection(...).query() is built for SELECTs and is unreliable for SHOW/DESCRIBE,
    so those go through the underlying Snowflake cursor. Not cached (metadata is cheap and
    should stay fresh). Returns an empty DataFrame on failure rather than raising."""
    try:
        cur = get_connection().cursor()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    try:
        cur.execute(sql)
        try:
            return cur.fetch_pandas_all()
        except Exception:  # noqa: BLE001 - SHOW/DESCRIBE aren't Arrow results
            rows = cur.fetchall()
            cols = [c[0] for c in (cur.description or [])]
            return pd.DataFrame(rows, columns=cols)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
