"""
Convert rowmaps' national augmented.kml into a GeoPackage in British National Grid.

The KML is one <Folder> per highway authority, each holding a sub-folder per
path type. GDAL's KML reader flattens those badly, so we stream it ourselves.

Usage:  python pipeline/kml_to_gpkg.py
Output: data/prow_national.gpkg
          layer "prow"        - one line per path (council, type, route, ...)
          layer "authorities" - one row per authority: name, code, data date,
                                path count, extent (the start of a coverage layer)
"""
import os, re, sys
import xml.etree.ElementTree as ET
import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "augmented.kml")
OUT = os.path.join(ROOT, "data", "prow_national.gpkg")
NS = "{http://www.opengis.net/kml/2.2}"
TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
DATE_RE = re.compile(r"date of this data is ([^.]+)\.", re.I)

TYPE_MAP = {"footpath": "Footpath", "fp": "Footpath", "bridleway": "Bridleway",
            "restricted byway": "Restricted Byway", "boat": "BOAT"}


def norm_type(t):
    t = (t or "").strip()
    if t.lower() in TYPE_MAP:
        return TYPE_MAP[t.lower()]
    return "BOAT" if "byway open" in t.lower() else "Unknown"


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows, xs, ys, offsets = [], [], [], [0]
    authorities = []
    depth = 0          # Folder nesting depth
    auth = None        # current authority dict
    n = 0

    for event, el in ET.iterparse(SRC, events=("start", "end")):
        tag = el.tag.replace(NS, "")
        if event == "start":
            if tag == "Folder":
                depth += 1
                if depth == 1:
                    auth = {"name": None, "description": "", "codes": set(), "count": 0}
            continue

        # --- end events ---
        if tag == "name" and depth == 1 and auth["name"] is None:
            auth["name"] = (el.text or "").strip()
        elif tag == "description" and depth == 1 and not auth["description"]:
            auth["description"] = el.text or ""
        elif tag == "Placemark":
            data = {d.get("name"): (d.findtext(f"{NS}value") or "")
                    for d in el.iter(f"{NS}Data")}
            coords = el.findtext(f".//{NS}coordinates") or ""
            pts = [c.split(",")[:2] for c in coords.split()]
            if len(pts) >= 2:
                lon, lat = np.array(pts, dtype=float).T
                x, y = TO_BNG.transform(lon, lat)
                xs.append(x); ys.append(y)
                offsets.append(offsets[-1] + len(x))
                rows.append({
                    "council": data.get("COUNCIL", ""),
                    "authority": auth["name"],
                    "row_type": norm_type(data.get("ROW_TYPE")),
                    "row_type_raw": data.get("ROW_TYPE", ""),
                    "parish": data.get("PARISH", ""),
                    "route_no": data.get("ROUTENO", ""),
                    "unique_id": data.get("UNIQUE", ""),
                })
                auth["codes"].add(data.get("COUNCIL", ""))
                auth["count"] += 1
            n += 1
            if n % 50000 == 0:
                print(f"  {n:,} paths read", flush=True)
            el.clear()
        elif tag == "Folder":
            if depth == 1:
                m = DATE_RE.search(auth["description"])
                authorities.append({
                    "authority": auth["name"],
                    "council": ",".join(sorted(c for c in auth["codes"] if c)),
                    "data_date": m.group(1).strip() if m else "",
                    "path_count": auth["count"],
                })
                auth = None
            depth -= 1
            el.clear()

    print(f"Building geometries for {len(rows):,} paths ...")
    x = np.concatenate(xs); y = np.concatenate(ys)
    geoms = shapely.linestrings(np.column_stack([x, y]),
                                indices=np.repeat(np.arange(len(rows)), np.diff(offsets)))
    prow = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs=27700)
    prow["length_m"] = prow.length.round(1)

    if os.path.exists(OUT):
        os.remove(OUT)
    prow.to_file(OUT, layer="prow", driver="GPKG", engine="pyogrio")

    # Authority extents: convex hull of each authority's paths, buffered a little.
    # Rough, but enough to say "we have data here". Proper council boundaries later.
    hulls = prow.dissolve(by="authority").geometry.convex_hull.buffer(500)
    auths = pd.DataFrame(authorities)
    auths = gpd.GeoDataFrame(auths, geometry=auths["authority"].map(hulls), crs=27700)
    auths.to_file(OUT, layer="authorities", driver="GPKG", engine="pyogrio")

    print(f"Done: {len(prow):,} paths from {len(auths)} authorities -> {OUT}")
    print(prow["row_type"].value_counts().to_string())


if __name__ == "__main__":
    sys.exit(main())
