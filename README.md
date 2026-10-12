# Unreachable Access Land

**[Open the map](https://gophercs.github.io/unreachable-access-land/)**

England has around a million hectares of open-access land under the Countryside and Rights of Way Act 2000 (CRoW): moors, heaths, downs, commons and dedicated woods where you have a right to roam. You can only use that right if you can legally get there, though, and some access land has no public right of way or public road leading to it.

This project finds that land across England and puts it on a map, so people can check the candidates from their desk and on the ground. It was inspired by Paul Whitewick's videos on landlocked access land.

These are **candidates for checking, not legal findings.** The data has gaps and errors, and the map can't see what's on the ground: a locked gate, a missing stile or a "private" sign.

## Results (England, built 2026-10-11)

| Status | Areas | Hectares |
|---|---:|---:|
| Landlocked | 390 | 3,011 |
| Near miss | 229 | 1,195 |
| Permissive access only | 131 | 3,497 |
| **No legal way in (the three above)** | **750** | **7,704** |
| Presumed accessible | 6,928 | 64,877 |
| Evidence of entry | 4,486 | 933,518 |
| Unknown (no path data) | 4 | 32 |

Of the presumed accessible and evidence of entry areas, 426 (4,789ha) have a **weak way in**, worth checking first. A further 10,186 slivers under 0.1ha (157ha in total, mostly digitising gaps) are kept on the map but left out of every total.

## How it works

1. **Areas.** Access-land polygons from Natural England that touch, or lie within 1m of each other, are merged into one area (42,054 polygons become 22,354 areas).
2. **Ways in.** For each area the pipeline looks for a legal way in within 50m of its boundary:
   - public rights of way (footpaths, bridleways, restricted byways, byways)
   - public roads (OS Open Roads: A, B, minor, local and local access roads; private and restricted access roads don't count)
   - the England Coast Path coastal margin
   - the Scottish border, where there's a general right of access
3. **Classification.** Each area gets one status:
   - **Landlocked:** nothing within 50m.
   - **Near miss:** the nearest way in is 10–50m away, or there's a barrier in the gap. Barriers are a motorway, a canal, a tidal river, a named river, or a railway with no level crossing within 30m.
   - **Presumed accessible:** a right of way or public road comes within 10m. Rights-of-way data is digitised at about 1:10,000, so 10m allows for that.
   - **Evidence of entry:** as presumed accessible, plus a gate or stile in OpenStreetMap on the boundary. It must be within 20m of the boundary and 25m of the path or road, and not marked private or locked.
   - **Permissive access only:** no legal way in, but the owner provides one. Either OpenStreetMap shows a permissive path in, or the land was dedicated by the owner (CRoW s16) and their road or track reaches it. This is provisional, because the owner can withdraw it.
   - **Unknown:** less than 98% of the land within 500m is covered by rights-of-way data.
4. **Whole-country passes.**
   - Access land within 10m of other reachable access land counts as reachable through it. The map often leaves a strip a few metres wide between parcels: a wall, a track or a drain.
   - Dedicated land is checked for owner access, as above.
5. **Weak ways in.** These are accessible on paper but worth checking first:
   - the right of way stops more than 2m short of the boundary
   - a strip of more than 2m separates the area from the neighbouring access land
   - OpenStreetMap shows a locked gate at the way in

   Paul Whitewick's White Hill example (locked gates, barbed wire and a removed stile beside a path) is why being legally reachable isn't treated as proof of access.

Field reports on the map (got in / blocked / couldn't find a way in, with a date and a short note) are saved in the visitor's own browser only and can be exported as CSV. Nothing is sent anywhere.

## Data sources

| Data | From | Licence |
|---|---|---|
| CRoW Act 2000 access layer (open country, registered common land, s16 dedicated land) | Natural England | OGL |
| England Coast Path coastal margin | Natural England | OGL |
| Public rights of way | Council definitive map data, compiled by [rowmaps.com](https://www.rowmaps.com/); Wiltshire Council's own file for Wiltshire | Mostly OGL; varies by council |
| Roads, rivers and canals, place names | OS Open Roads, OS Open Rivers, OS Open Names | OGL |
| Railways, level crossings, gates and stiles, tracks and permissive paths | OpenStreetMap via the [Geofabrik](https://download.geofabrik.de/europe/united-kingdom/england.html) England extract | ODbL |
| Council boundaries | ONS Counties and Unitary Authorities, December 2025 | OGL |

Contains Natural England data, OS data © Crown copyright and database right, and © OpenStreetMap contributors.

## Running it yourself

You need Python with `geopandas`, `pyogrio` (its GDAL must include the PMTiles and OSM drivers), `shapely`, `pandas` and `pyproj`. The source downloads are several GB, so they aren't in this repo. `SRC` in `pipeline/detect.py` lists where each file is expected.

```sh
python pipeline/kml_to_gpkg.py       # rowmaps KML -> data/prow_national.gpkg
python pipeline/osm_extract.py       # OSM extract -> data/osm_england.gpkg (slow: one pass per layer)
python pipeline/build_coverage.py    # which councils have rights-of-way data
python pipeline/run_national.py      # classify England in 50km blocks (resumable, ~25 min)
python pipeline/export_web.py        # PMTiles, candidate list and GPX/KML downloads -> web/
python web/serve.py                  # view at http://localhost:8000/ (PMTiles needs range requests)
python pipeline/deploy_pages.py      # publish web/ to the gh-pages branch
```

`node tests/ui_test.js web england` runs the map page in jsdom with a stub map and clicks every control (run `npm install` in `tests/` first).

## Layout

- `pipeline/detect.py`: the classification rules and their thresholds.
- `pipeline/run_national.py`: runs `detect.py` across England in 50km blocks.
- `pipeline/export_web.py`: builds the map tiles, candidate list and downloads.
- `web/index.html`: the map, a single page using MapLibre GL and PMTiles.
- `tests/ui_test.js`: UI smoke test.
