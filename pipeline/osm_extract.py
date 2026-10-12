"""
Pull the bits we need out of Geofabrik's england-latest.osm.pbf.

Usage:  python pipeline/osm_extract.py
Needs:  osm/england-latest.osm.pbf  (https://download.geofabrik.de/europe/united-kingdom/england.html)
Output: data/osm_england.gpkg
          layer "railways"     - operational railway lines (barriers), with bridge/tunnel flags
          layer "entry_points" - gates, stiles, kissing gates etc. (evidence of a way in)
          layer "crossings"    - level crossings / railway footpath crossings
          layer "ways"         - tracks, paths and minor roads with access/foot/designation tags

        python pipeline/osm_extract.py ways          rebuilds just the "ways" layer
        python pipeline/osm_extract.py entry_points  rebuilds just the "entry_points" layer

Each layer is a full pass through the 1.7GB file, so this takes a while.
"""
import os, sys, time
import geopandas as gpd
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "osm", "england-latest.osm.pbf")
OUT = os.path.join(ROOT, "data", "osm_england.gpkg")

RAIL = ("rail", "light_rail", "narrow_gauge", "preserved", "subway", "tram")
ENTRY = ("gate", "stile", "kissing_gate", "swing_gate", "squeeze", "hampshire_gate",
         "lift_gate", "turnstile", "entrance", "gap", "chain", "bollard", "cattle_grid",
         "horse_stile", "motorcycle_barrier", "cycle_barrier", "step_over")
CROSSING = ("level_crossing", "crossing")
WAYS = ("track", "path", "footway", "bridleway", "byway", "service", "unclassified",
        "residential", "tertiary", "secondary", "primary", "living_street", "steps", "cycleway")


def tag(other, key):
    """Pull one key out of GDAL's hstore-style other_tags string."""
    return other.str.extract(rf'"{key}"=>"([^"]*)"')[0]


def grab(layer, where, columns):
    t = time.time()
    g = gpd.read_file(SRC, layer=layer, where=where, columns=columns, engine="pyogrio")
    print(f"  {layer} [{where[:60]}...]: {len(g):,} features ({time.time() - t:.0f}s)", flush=True)
    return g.to_crs(27700)


def sql_in(field, values):
    return f"{field} IN ({', '.join(repr(v) for v in values)})"


def extract_ways():
    """Tracks, paths and minor roads with their access tags: the detail OS Open Roads leaves out."""
    ways = grab("lines", sql_in("highway", WAYS), ["osm_id", "name", "highway", "other_tags"])
    ot = ways["other_tags"].fillna("")
    for k in ("access", "foot", "designation", "motor_vehicle", "prow_ref"):
        ways[k] = tag(ot, k)
    ways.drop(columns="other_tags").to_file(OUT, layer="ways", driver="GPKG")


def extract_entry_points():
    """Gates, stiles etc. 'locked' matters: a locked gate is a sign of a blocked way in, not an open one."""
    pts = grab("points", sql_in("barrier", ENTRY), ["osm_id", "name", "barrier", "other_tags"])
    ot = pts["other_tags"].fillna("")
    for k in ("access", "foot", "locked"):
        pts[k] = tag(ot, k)
    pts.drop(columns="other_tags").to_file(OUT, layer="entry_points", driver="GPKG")
    return pts


def main():
    os.environ.setdefault("OSM_MAX_TMPFILE_SIZE", "2048")  # MB of RAM before spilling to disk
    only = sys.argv[1:]  # e.g. "ways" to (re)build just that layer
    if os.path.exists(OUT) and not only:
        os.remove(OUT)
    if only == ["ways"]:
        return extract_ways()
    if only == ["entry_points"]:
        return extract_entry_points()

    rail = grab("lines", sql_in("railway", RAIL), ["osm_id", "name", "railway", "other_tags"])
    ot = rail["other_tags"].fillna("")
    rail["bridge"] = tag(ot, "bridge").notna()
    rail["tunnel"] = tag(ot, "tunnel").notna()
    rail["service"] = tag(ot, "service")
    rail.drop(columns="other_tags").to_file(OUT, layer="railways", driver="GPKG")

    pts = extract_entry_points()

    # railway=level_crossing / crossing live in other_tags on points
    xs = grab("points", "other_tags LIKE '%\"railway\"=>\"%crossing\"%'", ["osm_id", "other_tags"])
    xs["railway"] = tag(xs["other_tags"].fillna(""), "railway")
    xs = xs[xs["railway"].isin(CROSSING)]
    xs.drop(columns="other_tags").to_file(OUT, layer="crossings", driver="GPKG")

    extract_ways()

    print(f"Done -> {OUT}  ({os.path.getsize(OUT) / 1e6:.0f} MB)")
    print(pts["barrier"].value_counts().to_string())


if __name__ == "__main__":
    main()
