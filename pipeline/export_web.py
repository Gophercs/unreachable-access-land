"""
Export detection results for the web map: PMTiles for the map layers plus one small JSON.

Usage:  python pipeline/export_web.py [name]     (default: england; or e.g. wiltshire for a pilot run)
Needs:  data/results_<name>.gpkg  (run_national.py or detect.py) and the source data detect.py uses
Output: web/tiles/<name>/clusters|clusters_lo|prow|barriers|gates.pmtiles
        web/downloads/<name>_candidates.gpx|kml
        web/data/<name>.json   bounds, headline totals and the candidate list
View:   python web/serve.py, then open http://localhost:8000/?d=<name>
        (PMTiles are read in byte ranges, so the page needs a web server, not file://)
"""
import json, os, sys, time
from datetime import date
import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect
from detect import SRC, read

ROOT = detect.ROOT
CONTEXT_M = 1000  # paths and barriers this close to access land go on the map
GATE_M = 30       # gates/stiles this close to access land go on the map
# What each map layer needs, and from which zoom it's drawn (tiles overzoom past MAXZOOM)
CLUSTER_COLS = ["cluster_id", "status", "area_ha", "n_parcels", "nearest_m", "reason", "authority",
                "lat", "lon", "sv_lat", "sv_lon", "access_type", "name", "osm_hint",
                "weak_entry", "weak_why", "sliver"]
# Zoomed out, the full outlines overflow the tile size limit and GDAL silently drops features (the
# biggest, most detailed moors first), so zooms 4-8 get a simplified copy with fewer columns.
LO_SIMPLIFY_M = 40  # ~1/10 of a pixel at zoom 8
LO_COLS = ["cluster_id", "status", "area_ha", "n_parcels", "name", "access_type", "reason", "authority",
           "lat", "lon", "weak_entry", "weak_why", "sliver"]  # enough for the popup
ZOOMS = {"clusters_lo": (4, 8), "clusters": (9, 13), "prow": (10, 13), "barriers": (8, 14), "gates": (12, 14)}


def near(g, clusters, dist):
    """Rows of g within dist of any cluster."""
    hit = np.unique(clusters.sindex.query(g.geometry, predicate="dwithin", distance=dist)[0])
    return g.iloc[hit]


def write_tiles(g, folder, layer, **options):
    path = os.path.join(folder, f"{layer}.pmtiles")
    if os.path.exists(path):
        os.remove(path)
    lo, hi = ZOOMS[layer]
    t = time.time()
    pyogrio.write_dataframe(g.to_crs(4326), path, driver="PMTiles", layer=layer, encoding="UTF-8",
                            dataset_options={"MINZOOM": lo, "MAXZOOM": hi, **options})
    print(f"  {layer}: {len(g):,} features -> {os.path.getsize(path) / 1e6:.1f} MB ({time.time() - t:.0f}s)",
          flush=True)


LABEL = {"landlocked": "Landlocked", "near_miss": "Near miss", "permissive_only": "Permissive only",
         "unknown_no_data": "Unknown", "presumed_accessible": "Weak way in", "evidence_of_entry": "Weak way in"}
KML_COLOUR = {"landlocked": "d7263d", "near_miss": "ee8e1a", "permissive_only": "8e44ad",
              "unknown_no_data": "8a8a8a", "presumed_accessible": "c2185b", "evidence_of_entry": "c2185b"}


def write_downloads(cand, name):
    """The candidates as files for phone mapping apps: GPX pins and KML outlines."""
    cand = cand.sort_values("area_ha", ascending=False)
    label = cand["status"].map(LABEL) + " " + cand["area_ha"].map("{:.1f}ha".format) + " - " + cand["name"].fillna("")
    desc = (cand["reason"].fillna("") + np.where(cand["weak_entry"].astype(bool), ". Weak: " + cand["weak_why"], "")
            + ". " + cand["access_type"].fillna("") + ". Id " + cand["cluster_id"])
    folder = os.path.join(ROOT, "web", "downloads")
    os.makedirs(folder, exist_ok=True)
    pts = gpd.GeoDataFrame({"name": label.values, "desc": desc.values},
                           geometry=gpd.points_from_xy(cand["lon"], cand["lat"]), crs=4326)
    kml = gpd.GeoDataFrame({"Name": label.values, "Description": desc.values,
                            "OGR_STYLE": [f"BRUSH(fc:#{KML_COLOUR[s]}66);PEN(c:#{KML_COLOUR[s]},w:2px)" for s in cand["status"]]},
                           geometry=cand.geometry.simplify(2).values, crs=27700).to_crs(4326)
    for g, ext, kw in ((pts, "gpx", {"driver": "GPX", "layer": "waypoints"}), (kml, "kml", {"driver": "KML"})):
        path = os.path.join(folder, f"{name}_candidates.{ext}")
        if os.path.exists(path):
            os.remove(path)
        pyogrio.write_dataframe(g, path, encoding="UTF-8", **kw)
        print(f"  download: {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "england"
    clusters = gpd.read_file(os.path.join(detect.DATA, f"results_{name}.gpkg"), layer="clusters")
    for c in ("weak_entry", "sliver"):  # older pilot results may predate these
        if c not in clusters:
            clusters[c] = False
    if "weak_why" not in clusters:
        clusters["weak_why"] = ""
    bounds = tuple(clusters.total_bounds + np.array([-CONTEXT_M, -CONTEXT_M, CONTEXT_M, CONTEXT_M]))
    folder = os.path.join(ROOT, "web", "tiles", name)
    os.makedirs(folder, exist_ok=True)

    # ---- map layers ----
    cl = clusters[[c for c in CLUSTER_COLS if c in clusters] + ["geometry"]].copy()
    for c in ("weak_entry", "sliver"):
        cl[c] = cl[c].astype(bool).astype(int)  # vector tiles have no booleans
    write_tiles(cl, folder, "clusters")
    lo = cl[[c for c in LO_COLS if c in cl] + ["geometry"]].copy()
    lo["geometry"] = lo.simplify(LO_SIMPLIFY_M)
    write_tiles(lo[~lo.is_empty], folder, "clusters_lo", MAX_SIZE=4000000, MAX_FEATURES=1000000)

    prow = read(SRC["prow_national"], bounds, layer="prow", columns=["row_type", "council"])
    write_tiles(near(prow, clusters, CONTEXT_M), folder, "prow")

    roads = read(SRC["roads"], bounds, layer="road_link", columns=["road_function", "road_classification_number"],
                 where="road_function = 'Motorway'")
    rivers = read(SRC["rivers"], bounds)
    rivers = rivers[rivers["form"].isin(detect.BARRIER_WATER_FORMS) | rivers["name1"].fillna("").str.match(r"^River ")]
    rail = read(SRC["osm"], bounds, layer="railways")
    rail = rail[~rail["tunnel"].astype(bool) & ~rail["bridge"].astype(bool)]
    barriers = pd.concat([
        gpd.GeoDataFrame({"kind": "motorway", "label": "motorway " + roads["road_classification_number"].fillna("")},
                         geometry=roads.geometry),
        gpd.GeoDataFrame({"kind": "water", "label": rivers["name1"].fillna(rivers["form"]).astype(str)},
                         geometry=rivers.geometry),
        gpd.GeoDataFrame({"kind": "railway", "label": "railway " + rail["name"].fillna("")}, geometry=rail.geometry),
    ], ignore_index=True).set_crs(27700, allow_override=True)
    write_tiles(near(barriers, clusters, CONTEXT_M), folder, "barriers")

    gates = read(SRC["osm"], bounds, layer="entry_points")
    gates = gates[~gates["access"].isin(["private", "no"]) & ~gates["foot"].isin(["private", "no"])]
    gates["locked"] = (gates["locked"].eq("yes") if "locked" in gates else False)
    gates["locked"] = gates["locked"].astype(int)
    write_tiles(near(gates[["barrier", "locked", "geometry"]], clusters, GATE_M), folder, "gates")

    # ---- headline totals (slivers left out) and the candidate list ----
    real = clusters[~clusters["sliver"].astype(bool)]
    by_status = real.groupby("status")["area_ha"].agg(["size", "sum"])
    summary = {
        "status": {s: {"n": int(r["size"]), "ha": round(float(r["sum"]), 2)} for s, r in by_status.iterrows()},
        "weak": {"n": int(real["weak_entry"].sum()), "ha": round(float(real.loc[real["weak_entry"], "area_ha"].sum()), 2)},
        "slivers": {"n": int(clusters["sliver"].sum()), "ha": round(float(clusters.loc[clusters["sliver"], "area_ha"].sum()), 2),
                    "min_ha": detect.MIN_HA},
        "built": date.today().isoformat(),
    }
    cand = real[~real["status"].isin(detect.ACCESSIBLE) | real["weak_entry"].astype(bool)].copy()
    b = cand.to_crs(4326).bounds.round(5)
    cand["bbox"] = b.values.tolist()
    cand["weak_entry"] = cand["weak_entry"].astype(bool).astype(int)
    cand["sliver"] = 0
    cols = [c for c in CLUSTER_COLS + ["bbox"] if c in cand]
    rows = json.loads(pd.DataFrame(cand[cols]).to_json(orient="records"))  # NaN -> null, numpy -> plain
    write_downloads(cand, name)
    out = os.path.join(ROOT, "web", "data", f"{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"name": name, "bounds": [round(v, 4) for v in clusters.to_crs(4326).total_bounds],
                   "summary": summary, "candidates": rows}, f, separators=(",", ":"), default=str)
    print(f"{len(clusters):,} clusters, {len(rows):,} candidates -> {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
