"""
Classify CRoW access land by whether the public can legally reach it.

Usage:  python pipeline/detect.py                 (Wiltshire pilot)
        python pipeline/detect.py --bbox xmin ymin xmax ymax
Needs:  data/prow_national.gpkg   (run pipeline/kml_to_gpkg.py first)
Output: data/results_<name>.gpkg  - "clusters" layer + the inputs, for QGIS
        data/results_<name>.csv   - the clusters as a table, biggest first

Status ladder (the first three are computed here; the rest come later):
  landlocked           no PRoW or public road within NEAR_M of the boundary
  near_miss            something within NEAR_M, but a gap > TOUCH_M or a barrier in the gap
  permissive_only      landlocked or near miss legally, but OSM shows a permissive path in
                       (the owner allows access, and can withdraw it)
  presumed_accessible  a PRoW/public road reaches the boundary (no evidence of a way in)
  unknown_no_data      nearby land falls outside any authority we have PRoW data for
  evidence_of_entry    as presumed, plus an OSM gate/stile on the boundary near a path or road
  confirmed            (needs field reports)

After classification, refine() runs once over all the results (it looks across block edges):
  - access land within TOUCH_M of reachable access land is presumed accessible through it
    (weak if the strip between them is over WEAK_GAP_M)
  - dedicated (s16) land with no legal way in, but an owner's road or OSM track reaching it,
    becomes permissive_only

Optional: data/osm_england.gpkg (pipeline/osm_extract.py) adds railways as barriers
and gates/stiles as evidence of entry. Without it those steps are skipped.
"""
import argparse, glob, os, time
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely.geometry import box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SRC = {
    "crow": glob.glob(os.path.join(ROOT, "CRoW_*", "*.shp"))[0],
    "roads": os.path.join(ROOT, "oproad_gpkg", "Data", "oproad_gb.gpkg"),
    "rivers": glob.glob(os.path.join(ROOT, "oprvrs_*", "data", "WatercourseLink.shp"))[0],
    "prow_national": os.path.join(DATA, "prow_national.gpkg"),
    "prow_wiltshire": glob.glob(os.path.join(ROOT, "Public_Rights_of_Way*", "*.shp"))[0],
    "osm": os.path.join(DATA, "osm_england.gpkg"),
    "names": os.path.join(ROOT, "opnames", "Data", "opname_gb.gpkg"),
    "coverage": os.path.join(ROOT, "boundaries", "coverage.gpkg"),      # pipeline/build_coverage.py
    "councils": os.path.join(ROOT, "boundaries", "ctyua_2025.geojson"),  # ONS, for the Scotland border
    "margin": os.path.join(ROOT, "boundaries", "coastal_margin.gpkg"),  # England Coast Path margin
}

TOUCH_M = 10     # PRoW data is ~1:10k accuracy, so within 10m counts as reaching it
NEAR_M = 50      # beyond this, nothing is close enough to be worth a look
MERGE_M = 1      # polygons this close are one cluster (slivers/digitising gaps)
MARGIN_M = 5000  # read entry/barrier data this far beyond the clusters
COVERAGE_M = 500 # land within this distance must be in a covered authority

# OS Open Roads 'function' values that are public highway you can walk along.
# Restricted/Secondary Access Roads are mostly private drives and farm tracks.
PUBLIC_ROAD = {"A Road", "B Road", "Minor Road", "Local Road", "Local Access Road"}
# Watercourse forms you can't reasonably wade. inlandRiver only counts if named
# "River ..." - OS Open Rivers has no width, so this is a rough proxy.
BARRIER_WATER_FORMS = {"canal", "tidalRiver"}
CROSSING_M = 30  # a level crossing this close to where the gap meets a railway cancels it
GATE_BOUNDARY_M = 20  # an OSM gate/stile this close to the boundary...
GATE_ENTRY_M = 25     # ...and this close to a path or public road is evidence of a way in
MIN_HA = 0.1     # smaller clusters are mostly digitising slivers: kept, but flagged and left out of totals
WEAK_GAP_M = 2   # a presumed way in whose right of way stops more than this short of the boundary is "weak":
                 # there's room for a private strip or fence in between (Paul Whitewick's White Hill point)
ACCESSIBLE = ("presumed_accessible", "evidence_of_entry")
NO_LEGAL_WAY = ("landlocked", "near_miss", "permissive_only")  # the headline figure

AREAS = {"wiltshire": None}  # None = take the extent of Wiltshire's own PRoW file


def read(path, bbox, **kw):
    g = gpd.read_file(path, bbox=tuple(bbox), engine="pyogrio", **kw)
    return g.to_crs(27700) if g.crs and g.crs.to_epsg() != 27700 else g.set_crs(27700, allow_override=True)


def load(core, with_crow=True):
    outer = box(*core.bounds).buffer(MARGIN_M).bounds
    t = time.time()

    crow = None
    if with_crow:
        crow = read(SRC["crow"], core.bounds)
        print(f"CRoW polygons: {len(crow):,}")

    prow = read(SRC["prow_national"], outer, layer="prow")
    councils = read(SRC["coverage"], outer, layer="councils")
    wilts = read(SRC["prow_wiltshire"], outer)
    wilts = gpd.GeoDataFrame({"council": "WT-direct", "row_type": wilts["LYR_NAME"].str.split(" \\(").str[0].str.title(),
                              "authority": "Wiltshire (council file)"},
                             geometry=wilts.geometry, crs=27700)
    prow_all = pd.concat([prow[["council", "row_type", "authority", "geometry"]], wilts],
                         ignore_index=True)
    print(f"PRoW lines: {len(prow):,} rowmaps + {len(wilts):,} Wiltshire council file")

    roads = read(SRC["roads"], outer, layer="road_link",
                 columns=["road_function", "road_classification", "name_1", "road_classification_number"])
    roads = roads.rename(columns={"road_function": "function", "road_classification": "class",
                                  "name_1": "name1", "road_classification_number": "roadNumber"})
    public_roads = roads[roads["function"].isin(PUBLIC_ROAD)]
    motorways = roads[roads["function"] == "Motorway"]
    print(f"Roads: {len(roads):,} ({len(public_roads):,} public, {len(motorways):,} motorway)")

    rivers = read(SRC["rivers"], outer)
    big_water = rivers[rivers["form"].isin(BARRIER_WATER_FORMS) |
                       rivers["name1"].fillna("").str.match(r"^River ")]
    print(f"Watercourses: {len(rivers):,} ({len(big_water):,} treated as barriers)")

    osm = {}
    if os.path.exists(SRC["osm"]):
        osm["railways"] = read(SRC["osm"], outer, layer="railways")
        osm["railways"] = osm["railways"][~osm["railways"]["tunnel"].astype(bool)
                                          & ~osm["railways"]["bridge"].astype(bool)]
        osm["crossings"] = read(SRC["osm"], outer, layer="crossings")
        gates = read(SRC["osm"], outer, layer="entry_points")
        osm["ways"] = read(SRC["osm"], outer, layer="ways")
        locked = gates["locked"].eq("yes") if "locked" in gates else pd.Series(False, index=gates.index)
        open_ = ~gates["access"].isin(["private", "no"]) & ~gates["foot"].isin(["private", "no"])
        osm["gates"] = gates[open_ & ~locked]
        osm["locked_gates"] = gates[locked]
        print(f"OSM: {len(osm['railways']):,} railway segments, {len(osm['crossings']):,} crossings, "
              f"{len(osm['gates']):,} gates/stiles ({len(osm['locked_gates']):,} locked)")
    else:
        print("OSM extract not found: skipping railways and gates/stiles")
    # Other ways in that aren't paths or roads: the coastal margin (access land reached along the
    # England Coast Path) and Scotland, where there's a general right of access to most land.
    margin = read(SRC["margin"], outer, layer="margin")
    margin["geometry"] = margin.make_valid()
    ons = read(SRC["councils"], outer)
    scotland = ons[ons["CTYUA25CD"].str.startswith("S")]
    other = pd.concat([
        gpd.GeoDataFrame({"kind": "coastal margin", "label": "England Coast Path " + margin["Stretch"].astype(str)},
                         geometry=margin.geometry),
        gpd.GeoDataFrame({"kind": "Scotland", "label": "right to roam"}, index=scotland.index,
                         geometry=scotland.boundary),
    ], ignore_index=True).set_crs(27700, allow_override=True)
    print(f"Other entries: {len(margin)} coastal margin stretches, {len(scotland)} Scottish councils")
    print(f"Loaded in {time.time() - t:.0f}s")
    return crow, prow_all, councils, public_roads, motorways, rivers, big_water, osm, other


def make_clusters(crow):
    """Merge touching/near-touching access polygons into clusters."""
    merged = shapely.union_all(crow.geometry.buffer(MERGE_M).values)
    parts = shapely.get_parts(merged)
    clusters = gpd.GeoDataFrame(geometry=gpd.GeoSeries(parts, crs=27700).buffer(-MERGE_M), crs=27700)
    clusters = clusters[~clusters.is_empty].reset_index(drop=True)
    # which source polygons went into each cluster
    idx = crow.sindex.query(clusters.geometry, predicate="intersects")
    src_ids = pd.Series(crow["GlobalID"].values[idx[1]]).groupby(idx[0]).agg(list)
    clusters["n_parcels"] = src_ids.map(len).reindex(clusters.index).fillna(0).astype(int)
    clusters["crow_ids"] = src_ids.map(lambda l: ";".join(l)).reindex(clusters.index).fillna("")
    # why it's access land: Open Country (mountain/moor/heath/down), Registered Common Land,
    # and/or Section 16 (voluntarily dedicated by the owner)
    flags = crow.iloc[idx[1]][["OC", "RCL", "S16"]].eq("Yes").set_axis(idx[0])
    kinds = flags.groupby(level=0).any()
    names = {"OC": "Open country", "RCL": "Common land", "S16": "Dedicated (s16)"}
    clusters["access_type"] = kinds.apply(
        lambda r: ", ".join(n for k, n in names.items() if r[k]), axis=1).reindex(clusters.index).fillna("")
    clusters["area_m2"] = clusters.area.round(0)
    clusters["area_ha"] = (clusters["area_m2"] / 1e4).round(2)
    # a stable-ish id from the representative point (survives re-runs if the land doesn't move)
    rp = clusters.representative_point()
    clusters["cluster_id"] = [f"E{int(p.x)}N{int(p.y)}" for p in rp]
    return clusters


def classify(clusters, entries, barriers, coverage, crossings):
    """entries: GeoDataFrame with 'kind' + 'label'. barriers: 'kind' + 'label'."""
    e_idx = entries.sindex
    b_idx = barriers.sindex
    x_idx = crossings.sindex if len(crossings) else None
    out = []
    for i, c in clusters.iterrows():
        geom = c.geometry
        hits = e_idx.query(geom, predicate="dwithin", distance=NEAR_M)
        best = None  # (rank, dist, label, barrier) - lower rank is better
        for j in hits:
            e = entries.iloc[j]
            d = geom.distance(e.geometry)
            barrier = None
            if d > 0:
                gap = shapely.shortest_line(e.geometry, geom)
                for k in b_idx.query(gap, predicate="intersects"):
                    b = barriers.iloc[k]
                    if b["kind"] == "railway" and x_idx is not None and len(
                            x_idx.query(gap.intersection(b.geometry), predicate="dwithin", distance=CROSSING_M)):
                        continue  # level crossing right there
                    barrier = b["label"]
                    break
            rank = 0 if (d <= TOUCH_M and barrier is None) else 1
            cand = (rank, d, f"{e['kind']}: {e['label']}", barrier)
            if best is None or cand[:2] < best[:2]:
                best = cand
        # coverage: is the land around this cluster inside an authority we have data for?
        ring = geom.buffer(COVERAGE_M)
        land = coverage[1].intersection(ring).area
        covered = coverage[0].intersection(ring).area / land if land else 1.0

        if best is None:
            status = "landlocked" if covered > 0.98 else "unknown_no_data"
            reason = f"nothing within {NEAR_M}m"
            dist = None
        elif best[0] == 0:
            status, dist = "presumed_accessible", best[1]
            reason = f"{best[2]} at {best[1]:.0f}m"
        else:
            status, dist = "near_miss", best[1]
            reason = f"nearest {best[2]} at {best[1]:.0f}m"
            if best[3]:
                reason += f", gap crosses {best[3]}"
        if status in ("landlocked", "near_miss") and covered <= 0.98:
            reason += f" (only {covered:.0%} of surroundings have PRoW data)"
            status = "unknown_no_data"
        out.append({"status": status, "nearest_m": None if dist is None else round(dist, 1),
                    "reason": reason, "prow_coverage": round(covered, 3)})
        if (i + 1) % 200 == 0:
            print(f"  classified {i + 1:,}/{len(clusters):,}", flush=True)
    return clusters.join(pd.DataFrame(out, index=clusters.index))


# OSM designation values that mean a legal public right of way / public highway
OSM_PUBLIC = {"public_footpath", "public_bridleway", "restricted_byway", "byway_open_to_all_traffic",
              "public_byway", "byway", "unclassified_county_road", "unclassified_highway", "public_right_of_way"}
OSM_PERMISSIVE = {"permissive_footpath", "permissive_bridleway", "permissive_path"}
OSM_HINT_M = 10  # OSM is usually more precise than PRoW data, so a tight tolerance


def describe_way(w):
    tags = [f"{k}={w[k]}" for k in ("designation", "access", "foot") if isinstance(w[k], str)]
    kind = w["highway"] + (f" '{w['name']}'" if isinstance(w["name"], str) else "")
    return kind + (f" ({', '.join(tags)})" if tags else " (no access tags)")


def add_osm_hints(res, ways):
    """What OSM shows reaching each cluster. Not legal evidence, but a big help when checking:
    a private track, a permissive path, or a right of way the council data is missing."""
    ci, wi = ways.sindex.query(res.boundary, predicate="dwithin", distance=OSM_HINT_M)
    hints, public, permissive = {}, set(), set()
    for c, w in pd.DataFrame({"c": res.index[ci], "w": wi}).groupby("c")["w"]:
        sub = ways.iloc[w.values]
        if sub["designation"].isin(OSM_PUBLIC).any():
            public.add(c)
        if (sub["designation"].isin(OSM_PERMISSIVE) | sub["access"].eq("permissive")
                | sub["foot"].eq("permissive")).any():
            permissive.add(c)
        desc = sub.apply(describe_way, axis=1).value_counts()
        hints[c] = "; ".join(f"{d}" + (f" x{n}" if n > 1 else "") for d, n in desc.head(4).items())
    res["osm_hint"] = pd.Series(hints).reindex(res.index).fillna("")
    res["osm_public_designation"] = res.index.isin(public)
    # A near miss where OSM shows a tagged right of way actually touching the land is almost
    # always council digitising error (their line sits 10-50m off), so treat it as reached.
    fix = (res["status"] == "near_miss") & res["osm_public_designation"]
    res.loc[fix, "status"] = "presumed_accessible"
    res.loc[fix, "reason"] = res.loc[fix, "reason"].str.replace("nearest ", "", regex=False) +         "; OSM shows a public right of way touching it"
    # No legal way in, but the landowner lets people use a path: its own rung on the ladder
    perm = res["status"].isin(["landlocked", "near_miss"]) & res.index.isin(permissive)
    res.loc[perm, "reason"] = res.loc[perm, "reason"] + " (" + res.loc[perm, "status"].str.replace("_", " ") +         " legally); OSM shows a permissive path in"
    res.loc[perm, "status"] = "permissive_only"
    return res


def add_streetview_point(res, roads):
    """Nearest point on a public road, so Street View opens somewhere it can actually see."""
    near = gpd.sjoin_nearest(gpd.GeoDataFrame(geometry=res.geometry, crs=27700),
                             roads[["geometry"]].reset_index(drop=True), max_distance=3000, how="left")
    near = near[~near.index.duplicated()]
    pts = [res.at[i, "geometry"].representative_point() if pd.isna(j)
           else shapely.Point(shapely.shortest_line(roads.geometry.iloc[int(j)], res.at[i, "geometry"]).coords[0])
           for i, j in near["index_right"].items()]
    ll = gpd.GeoSeries(pts, index=near.index, crs=27700).to_crs(4326)
    res["sv_lat"], res["sv_lon"] = ll.y.round(5), ll.x.round(5)
    return res


NAME_M = 300  # a named hill/wood/down this close to a cluster names it
SETTLEMENTS = ("Hamlet", "Village", "Town", "City", "Other Settlement", "Suburban Area")


def add_names(res, bounds):
    """Name each cluster from OS Open Names: a landform or wood on/near it, else 'near <village>'."""
    if not os.path.exists(SRC["names"]):
        res["name"] = ""
        return res
    names = gpd.read_file(SRC["names"], bbox=bounds, engine="pyogrio",
                          columns=["name1", "type", "local_type"])
    feats = names[names["type"].isin(["landform", "landcover"]) & (names["local_type"] != "Spot Height")]
    places = names[names["local_type"].isin(SETTLEMENTS)]
    ci, fi = feats.sindex.query(res.geometry, predicate="dwithin", distance=NAME_M)
    pairs = pd.DataFrame({"c": res.index[ci], "name": feats["name1"].values[fi],
                          "d": feats.geometry.values[fi].distance(res.geometry.values[ci])})
    best = pairs.sort_values("d").drop_duplicates("c").set_index("c")["name"]
    near = gpd.sjoin_nearest(gpd.GeoDataFrame(geometry=res.representative_point(), crs=27700),
                             places[["name1", "geometry"]], how="left")
    near = near[~near.index.duplicated()]["name1"]
    res["name"] = best.reindex(res.index).fillna("near " + near.fillna("?"))
    return res


def add_entry_evidence(res, entries, gates):
    """Promote presumed_accessible to evidence_of_entry where OSM has a gate/stile
    on the boundary close to a path or public road."""
    near_path = np.unique(entries.sindex.query(gates.geometry, predicate="dwithin", distance=GATE_ENTRY_M)[0])
    gates = gates.iloc[near_path]
    if not len(gates):
        return res
    presumed = res[res["status"] == "presumed_accessible"]
    ci, gi = gates.sindex.query(presumed.boundary, predicate="dwithin", distance=GATE_BOUNDARY_M)
    found = pd.Series(gates["barrier"].values[gi], index=presumed.index[ci])
    summary = found.groupby(level=0).agg(lambda b: ", ".join(f"{n} {k}" for k, n in b.value_counts().items()))
    res.loc[summary.index, "status"] = "evidence_of_entry"
    res.loc[summary.index, "reason"] = res.loc[summary.index, "reason"] + "; OSM: " + summary
    return res


def add_flags(res, entries, locked_gates):
    """Slivers, and presumed-accessible clusters whose way in is weak: the right of way stops short
    of the boundary, or OSM shows a locked gate on the boundary by the way in. (Evidence-of-entry
    clusters already have an open gate or stile, so one locked gate elsewhere doesn't count.)"""
    res["sliver"] = res["area_ha"] < MIN_HA
    acc = res["status"] == "presumed_accessible"
    # Only for rights of way: a road centreline is always a few metres in from the verge, which is
    # highway too. And not where OSM puts a tagged right of way on the boundary (better geometry).
    gap = ((res["status"] == "presumed_accessible") & (res["nearest_m"].fillna(0) > WEAK_GAP_M)
           & res["reason"].str.startswith("PRoW") & ~res["reason"].str.contains("OSM shows a public"))
    locked = pd.Series(False, index=res.index)
    if len(locked_gates) and acc.any():
        near_path = np.unique(entries.sindex.query(locked_gates.geometry, predicate="dwithin",
                                                   distance=GATE_ENTRY_M)[0])
        lg = locked_gates.iloc[near_path]
        if len(lg):
            ci, _ = lg.sindex.query(res[acc].boundary, predicate="dwithin", distance=GATE_BOUNDARY_M)
            locked.loc[res[acc].index[np.unique(ci)]] = True
    why = pd.Series("", index=res.index)
    if gap.any():
        why[gap] = "path stops " + res.loc[gap, "nearest_m"].map("{:.0f}".format) + "m short of the boundary"
    if locked.any():
        why[locked] = why[locked].where(why[locked] == "", why[locked] + "; ") + \
            "OSM shows a locked gate on the boundary"
    res["weak_entry"] = gap | locked
    res["weak_why"] = why
    return res


def process(core, clusters=None):
    """Classify the clusters in `core`, or the given clusters (data is then read around `core`)."""
    crow, prow, councils, public_roads, motorways, rivers, big_water, osm, other = load(
        core, with_crow=clusters is None)

    t = time.time()
    if clusters is None:
        clusters = make_clusters(crow)
        print(f"{len(crow):,} polygons -> {len(clusters):,} clusters ({time.time() - t:.0f}s)")

    entries = pd.concat([
        gpd.GeoDataFrame({"kind": "PRoW", "label": prow["row_type"] + " (" + prow["council"] + ")"},
                         geometry=prow.geometry),
        gpd.GeoDataFrame({"kind": "road",
                          "label": public_roads["function"] + " " + public_roads["name1"].fillna(
                              public_roads["roadNumber"].fillna("")).astype(str)},
                         geometry=public_roads.geometry),
        other,
    ], ignore_index=True).set_crs(27700, allow_override=True)
    rail = osm.get("railways", gpd.GeoDataFrame({"name": []}, geometry=[], crs=27700))
    barriers = pd.concat([
        gpd.GeoDataFrame({"kind": "motorway", "label": "motorway " + motorways["roadNumber"].fillna("").astype(str)},
                         geometry=motorways.geometry),
        gpd.GeoDataFrame({"kind": "water", "label": "watercourse " + big_water["name1"].fillna(big_water["form"]).astype(str)},
                         geometry=big_water.geometry),
        gpd.GeoDataFrame({"kind": "railway", "label": "railway " + rail["name"].fillna("").astype(str)},
                         geometry=rail.geometry),
    ], ignore_index=True).set_crs(27700, allow_override=True)
    crossings = osm.get("crossings", gpd.GeoDataFrame(geometry=[], crs=27700))
    # coverage is judged on land only, so the sea beside a coastal parcel doesn't count as "no data"
    coverage = (shapely.union_all(councils[councils["has_prow"].astype(bool)].geometry.values),
                shapely.union_all(councils.geometry.values))

    t = time.time()
    res = classify(clusters, entries, barriers, coverage, crossings)
    if "gates" in osm:
        res = add_entry_evidence(res, entries, osm["gates"])
    if "ways" in osm:
        res = add_osm_hints(res, osm["ways"])
    res = add_flags(res, entries, osm.get("locked_gates", gpd.GeoDataFrame(geometry=[], crs=27700)))
    print(f"Classified in {time.time() - t:.0f}s")

    # which authority each cluster is in: the most common council among rowmaps paths within 2km
    near = gpd.sjoin_nearest(gpd.GeoDataFrame(geometry=res.representative_point(), crs=27700),
                             prow[prow["council"] != "WT-direct"][["authority", "geometry"]],
                             max_distance=2000, how="left")
    res["authority"] = near.groupby(level=0)["authority"].first().str.replace(" Rights of Way", "")
    res = add_streetview_point(res, public_roads)
    res = add_names(res, box(*res.total_bounds).buffer(10000).bounds)
    # lat/lon + an OS Maps link so candidates can be checked by eye
    ll = res.representative_point().to_crs(4326)
    res["lat"], res["lon"] = ll.y.round(5), ll.x.round(5)
    res["os_map"] = [f"https://explore.osmaps.com/?lat={y}&lon={x}&zoom=15"
                     for y, x in zip(res["lat"], res["lon"])]
    layers = {"prow": prow, "public_roads": public_roads, "barriers": barriers}
    if "gates" in osm:
        layers["gates"] = osm["gates"]
    return res, layers


OWNER_ROADS = {"Restricted Local Access Road", "Restricted Secondary Access Road", "Secondary Access Road"}


def link_neighbours(res):
    """Access land within TOUCH_M of other access land you can get into is reachable through it.
    The map often leaves a strip of a few metres between parcels (a wall, a track, a drain), so they
    end up as separate clusters. Repeats until nothing changes, so chains of parcels are followed."""
    total = 0
    while True:
        src = res[res["status"].isin(ACCESSIBLE)]
        tgt = res[res["status"].isin(NO_LEGAL_WAY)]
        ti, si = src.sindex.query(tgt.geometry, predicate="dwithin", distance=TOUCH_M)
        if not len(ti):
            break
        pairs = pd.DataFrame({"t": tgt.index[ti], "s": src.index[si]})
        pairs["d"] = [res.geometry[t].distance(res.geometry[s]) for t, s in zip(pairs["t"], pairs["s"])]
        best = pairs.sort_values("d").drop_duplicates("t")
        for t, s, d in best.itertuples(index=False):
            nb = res.loc[s]
            name = nb["name"] if isinstance(nb.get("name"), str) else nb["cluster_id"]
            res.loc[t, "status"] = "presumed_accessible"
            res.loc[t, "nearest_m"] = round(d, 1)
            res.loc[t, "reason"] = f"adjoins reachable access land ({name}, {nb['cluster_id']}) at {d:.0f}m"
            why = []
            if d > WEAK_GAP_M:
                why.append(f"a {d:.0f}m strip that isn't access land separates it from the neighbouring access land")
            if nb["weak_entry"]:
                why.append(f"the neighbouring access land has a weak way in ({nb['weak_why']})")
            res.loc[t, "weak_entry"] = bool(why)
            res.loc[t, "weak_why"] = "; ".join(why)
        total += len(best)
    print(f"Reachable through neighbouring access land: {total:,} clusters")
    return res


def add_owner_access(res):
    """Dedicated (s16) land with no legal way in, but an owner's road or an OSM track/path reaching it.
    The owner chose to open it, and usually provides a way in: count it as permissive, which is
    provisional (the way in can be withdrawn, and nobody has checked it on the ground)."""
    s16 = res[res["status"].isin(["landlocked", "near_miss"])
              & res["access_type"].fillna("").str.contains("Dedicated")]
    if not len(s16):
        return res
    mask = shapely.union_all(s16.geometry.buffer(TOUCH_M).values)
    roads = gpd.read_file(SRC["roads"], layer="road_link", mask=mask, engine="pyogrio",
                          columns=["road_function"]).to_crs(27700)
    roads = roads[roads["road_function"].isin(OWNER_ROADS)]
    ways = gpd.read_file(SRC["osm"], layer="ways", mask=mask, engine="pyogrio")
    ways = ways.set_crs(27700, allow_override=True) if ways.crs is None else ways.to_crs(27700)
    ways = ways[~ways["access"].isin(["private", "no"]) & ~ways["foot"].isin(["private", "no"])]
    routes = pd.concat([
        gpd.GeoDataFrame({"label": roads["road_function"].str.lower()}, geometry=roads.geometry),
        gpd.GeoDataFrame({"label": "OSM " + ways["highway"]}, geometry=ways.geometry),
    ], ignore_index=True).set_crs(27700, allow_override=True)
    ci, ri = routes.sindex.query(s16.boundary, predicate="dwithin", distance=TOUCH_M)
    found = pd.Series(routes["label"].values[ri]).groupby(s16.index[ci]).agg(lambda l: ", ".join(sorted(set(l))))
    for i, labels in found.items():
        res.loc[i, "status"] = "permissive_only"
        res.loc[i, "reason"] = (f"dedicated by the owner (s16), no public way in; reached by {labels} "
                                f"(was: {res.loc[i, 'reason']})")
    print(f"Dedicated land reached by an owner's road or track: {len(found):,} clusters")
    return res


def refine(res):
    """Whole-results passes, run once all clusters are classified (they look across block edges)."""
    res = link_neighbours(res)
    return add_owner_access(res)


def summarise(res):
    real = res[~res["sliver"]]
    print(f"\nSummary (clusters of {MIN_HA}ha and over):")
    s = real.groupby("status").agg(clusters=("area_ha", "size"), hectares=("area_ha", "sum"))
    print(s.round(1).to_string())
    nl = real[real["status"].isin(NO_LEGAL_WAY)]
    print(f"No legal way in (landlocked + near miss + permissive only): {len(nl):,} clusters, "
          f"{nl['area_ha'].sum():,.1f}ha")
    weak = real[real["weak_entry"]]
    print(f"Weak ways in (accessible on paper, worth checking): {len(weak):,} clusters, "
          f"{weak['area_ha'].sum():,.1f}ha")
    sl = res[res["sliver"]]
    print(f"Left out as slivers under {MIN_HA}ha: {len(sl):,} clusters, {sl['area_ha'].sum():,.2f}ha "
          f"({sl['status'].isin(NO_LEGAL_WAY).sum():,} of them with no legal way in)")
    print("\nTop 15 non-accessible by area:")
    top = real[real.status.isin(["landlocked", "near_miss", "unknown_no_data"])].head(15)
    print(top[["cluster_id", "area_ha", "status", "reason"]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="wiltshire")
    ap.add_argument("--bbox", nargs=4, type=float)
    a = ap.parse_args()

    if a.bbox:
        core = box(*a.bbox)
    else:
        import pyogrio
        core = box(*pyogrio.read_info(SRC["prow_wiltshire"])["total_bounds"])
    print(f"Study area {a.name}: {[round(v) for v in core.bounds]}")

    res, layers = process(core)
    res = refine(res)
    # only report clusters that actually fall within the study area
    res = res[res.intersects(core)].sort_values("area_m2", ascending=False)
    out = os.path.join(DATA, f"results_{a.name}.gpkg")
    if os.path.exists(out):
        os.remove(out)
    res.to_file(out, layer="clusters", driver="GPKG")
    gpd.GeoDataFrame(geometry=[core], crs=27700).to_file(out, layer="study_area", driver="GPKG")
    for name, layer in layers.items():
        layer.to_file(out, layer=name, driver="GPKG")
    res.drop(columns=["geometry", "crow_ids"]).to_csv(os.path.join(DATA, f"results_{a.name}.csv"), index=False)
    summarise(res)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
