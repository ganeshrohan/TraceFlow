
import pandas as pd
import queries as q


def traverse(fq_object: str, direction: str, distance: int = 5) -> pd.DataFrame:
    """Return the GET_LINEAGE edge set for one direction (UPSTREAM/DOWNSTREAM)."""
    try:
        return q.object_lineage(fq_object, direction, distance)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def downstream_nodes(fq_object: str, distance: int = 5) -> pd.DataFrame:
    """Distinct downstream objects with the minimum hop distance (BFS-style)."""
    edges = traverse(fq_object, "DOWNSTREAM", distance)
    if edges is None or edges.empty:
        return pd.DataFrame(columns=["object", "domain", "distance"])
    nodes = (
        edges.rename(columns={"TARGET_OBJECT": "object", "TARGET_DOMAIN": "domain", "DISTANCE": "distance"})
        [["object", "domain", "distance"]]
        .groupby(["object", "domain"], as_index=False)["distance"].min()
        .sort_values(["distance", "object"])
    )
    return nodes[nodes["object"] != fq_object]


def upstream_nodes(fq_object: str, distance: int = 5) -> pd.DataFrame:
    edges = traverse(fq_object, "UPSTREAM", distance)
    if edges is None or edges.empty:
        return pd.DataFrame(columns=["object", "domain", "distance"])
    nodes = (
        edges.rename(columns={"SOURCE_OBJECT": "object", "SOURCE_DOMAIN": "domain", "DISTANCE": "distance"})
        [["object", "domain", "distance"]]
        .groupby(["object", "domain"], as_index=False)["distance"].min()
        .sort_values(["distance", "object"])
    )
    return nodes[nodes["object"] != fq_object]


def cascading_risk(fq_object: str, down: pd.DataFrame, consumers_df: pd.DataFrame) -> dict:
    """Risk score 0-100 from downstream breadth, BI/view exposure and consumer reach."""
    n_down = 0 if down is None else len(down)
    n_consumers = 0 if consumers_df is None else int(consumers_df["USER_NAME"].nunique()) if not consumers_df.empty else 0
    n_views = 0
    if down is not None and not down.empty:
        n_views = int(down["domain"].astype(str).str.contains("VIEW", case=False).sum())

    # Weighted, saturating components (each capped) -> 0..100
    breadth = min(45, n_down * 5)          # transitive children
    exposure = min(30, n_views * 6)        # BI / view surface
    reach = min(25, n_consumers * 3)       # unique human/service consumers
    score = int(min(100, breadth + exposure + reach))
    tier = "CRITICAL" if score >= 70 else "MODERATE" if score >= 35 else "LOW"
    return {
        "score": score,
        "tier": tier,
        "downstream_count": n_down,
        "view_count": n_views,
        "consumer_count": n_consumers,
    }


def stakeholders(fq_object: str, down: pd.DataFrame, lookback_days: int = 365) -> pd.DataFrame:
    """Compile users/roles to notify: consumers of the object and of its downstream nodes."""
    frames = []
    objs = [fq_object] + ([] if down is None or down.empty else down["object"].tolist())
    for obj in objs[:25]:  # cap to keep the query set bounded
        try:
            c = q.consumers(obj, lookback_days)
            if c is not None and not c.empty:
                c = c.assign(source_object=obj)
                frames.append(c[["USER_NAME", "ROLE_NAME", "source_object", "READ_COUNT", "LAST_READ"]])
        except Exception:  # noqa: BLE001
            continue
    if not frames:
        return pd.DataFrame(columns=["USER_NAME", "ROLE_NAME", "objects_affected", "total_reads", "last_access"])
    allc = pd.concat(frames, ignore_index=True)
    agg = (
        allc.groupby(["USER_NAME", "ROLE_NAME"], as_index=False)
        .agg(objects_affected=("source_object", "nunique"),
             total_reads=("READ_COUNT", "sum"),
             last_access=("LAST_READ", "max"))
        .sort_values(["objects_affected", "total_reads"], ascending=False)
    )
    return agg
