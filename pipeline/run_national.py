"""
Run the detector over all of England, a block at a time so it fits in memory.

Usage:  python pipeline/run_national.py            (resumes: finished blocks are skipped)
        python pipeline/run_national.py --fresh    (rebuild clusters and every block)
Output: data/results_england.gpkg  layer "clusters"
        data/results_england.csv
        data/national_parts/       one file per block (delete to redo that block)

Clusters are built once for the whole country, so a common that crosses a block edge is still
one cluster. Each block then takes the clusters whose representative point falls inside it and
reads paths, roads and OSM data around those clusters only.
"""
import argparse, os, shutil, time, traceback
import pandas as pd
import geopandas as gpd
from shapely.geometry import box

import detect

BLOCK_M = 50000
PARTS = os.path.join(detect.DATA, "national_parts")
CLUSTERS = os.path.join(detect.DATA, "clusters_england.gpkg")
OUT = os.path.join(detect.DATA, "results_england.gpkg")


def national_clusters(fresh):
    if os.path.exists(CLUSTERS) and not fresh:
        return gpd.read_file(CLUSTERS)
    t = time.time()
    crow = gpd.read_file(detect.SRC["crow"], engine="pyogrio").to_crs(27700)
    clusters = detect.make_clusters(crow)
    print(f"{len(crow):,} polygons -> {len(clusters):,} clusters ({time.time() - t:.0f}s)", flush=True)
    clusters.to_file(CLUSTERS, driver="GPKG")
    return clusters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    if a.fresh and os.path.exists(PARTS):
        shutil.rmtree(PARTS)
    os.makedirs(PARTS, exist_ok=True)

    clusters = national_clusters(a.fresh)
    rp = clusters.representative_point()
    clusters["block"] = [f"{int(p.x // BLOCK_M)}_{int(p.y // BLOCK_M)}" for p in rp]
    blocks = clusters.groupby("block").size().sort_index()
    print(f"{len(clusters):,} clusters in {len(blocks)} blocks of {BLOCK_M // 1000}km", flush=True)

    start, failed = time.time(), []
    for n, (key, count) in enumerate(blocks.items(), 1):
        part = os.path.join(PARTS, f"{key}.gpkg")
        if os.path.exists(part):
            continue
        sub = clusters[clusters["block"] == key].drop(columns="block")
        t = time.time()
        print(f"\n=== block {key} ({n}/{len(blocks)}): {count:,} clusters", flush=True)
        try:
            res, _ = detect.process(box(*sub.total_bounds), sub)
        except Exception:
            traceback.print_exc()
            failed.append(key)
            print(f"=== block {key} FAILED, carrying on", flush=True)
            continue
        tmp = os.path.join(PARTS, f"_{key}_tmp.gpkg")
        res.to_file(tmp, driver="GPKG")
        os.replace(tmp, part)  # only a finished block counts, so a crash resumes cleanly
        print(f"=== block {key} done in {time.time() - t:.0f}s ({(time.time() - start) / 60:.0f} min so far)",
              flush=True)

    if failed:
        print(f"\n{len(failed)} blocks failed: {', '.join(failed)}. Fix and re-run to fill them in.")
        return
    res = pd.concat([gpd.read_file(os.path.join(PARTS, f"{k}.gpkg")) for k in blocks.index],
                    ignore_index=True).sort_values("area_m2", ascending=False)
    res = gpd.GeoDataFrame(res, geometry="geometry", crs=27700).reset_index(drop=True)
    res["weak_entry"] = res["weak_entry"].astype(bool)
    res = detect.refine(res)
    if os.path.exists(OUT):
        os.remove(OUT)
    res.to_file(OUT, layer="clusters", driver="GPKG")
    res.drop(columns=["geometry", "crow_ids"]).to_csv(OUT.replace(".gpkg", ".csv"), index=False)
    detect.summarise(res)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
