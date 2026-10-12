"""
Which highway authorities we have rights-of-way data for, on real council boundaries.

Usage:  python pipeline/build_coverage.py
Needs:  boundaries/ctyua_2025.geojson  (ONS Counties and Unitary Authorities, Dec 2025, BGC)
        data/prow_national.gpkg        (pipeline/kml_to_gpkg.py)
Output: boundaries/coverage.gpkg layer "councils": every English/Welsh council with
        has_prow, the rowmaps authority it matched and that data's date.
"""
import os, re
import geopandas as gpd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONS = os.path.join(ROOT, "boundaries", "ctyua_2025.geojson")
PROW = os.path.join(ROOT, "data", "prow_national.gpkg")
OUT = os.path.join(ROOT, "boundaries", "coverage.gpkg")

# ONS name -> rowmaps name where they differ beyond punctuation
ALIASES = {
    "County Durham": "Durham",
    "Rhondda Cynon Taf": "Rhondda Cynon Taff",
    "Cumberland": "Cumbria",  # rowmaps still publishes the pre-2023 Cumbria file
}


def norm(s):
    s = s.lower().replace("rights of way", "").replace("&", "and")
    s = re.sub(r"\b(city of|county of|the)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def main():
    ons = gpd.read_file(ONS)
    ons = ons[ons["CTYUA25CD"].str[0].isin(["E", "W"])].copy()
    auths = gpd.read_file(PROW, layer="authorities", ignore_geometry=True)
    by_norm = {norm(a): a for a in auths["authority"]}
    ons["rowmaps"] = ons["CTYUA25NM"].map(lambda n: by_norm.get(norm(ALIASES.get(n, n))))
    ons["has_prow"] = ons["rowmaps"].notna()
    ons = ons.merge(auths[["authority", "data_date", "path_count"]], how="left",
                    left_on="rowmaps", right_on="authority").drop(columns="authority")
    out = ons[["CTYUA25CD", "CTYUA25NM", "has_prow", "rowmaps", "data_date", "path_count", "geometry"]]
    out = out.rename(columns={"CTYUA25CD": "code", "CTYUA25NM": "council"})
    if os.path.exists(OUT):
        os.remove(OUT)
    out.to_file(OUT, layer="councils", driver="GPKG")
    missing = out[~out["has_prow"]]["council"].tolist()
    print(f"{out['has_prow'].sum()} of {len(out)} councils have PRoW data -> {OUT}")
    print("No data:", ", ".join(missing))


if __name__ == "__main__":
    main()
