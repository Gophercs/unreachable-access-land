// UI smoke test for web/index.html: runs the page script in jsdom with a stub MapLibre and
// stub fetch (serving web/data/<name>.json), clicks every control and reports script errors.
// Setup (once): cd tests && npm init -y && npm i jsdom@24.1.3
// Run:          node tests/ui_test.js web [name]      (name defaults to wiltshire)
const fs = require("fs"), { JSDOM, VirtualConsole } = require("jsdom");
const WEB = process.argv[2], NAME = process.argv[3] || "wiltshire";
let html = fs.readFileSync(WEB + "/index.html", "utf8").replace(/<script src=[^>]*><\/script>/g, "");
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push(e.message)); vc.on("error", e => errors.push(String(e)));
const dom = new JSDOM(html, { runScripts: "outside-only", url: `http://localhost/?d=${NAME}`, virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const DATA = JSON.parse(fs.readFileSync(`${WEB}/data/${NAME}.json`, "utf8"));
w.fetch = url => Promise.resolve({ ok: url === `data/${NAME}.json`, status: 404, json: () => Promise.resolve(DATA) });
const handlers = {}, calls = { sources: [], filters: {}, layout: {}, zoom: {} };
class Map {
  constructor() { setTimeout(() => this.fire("load"), 0); }
  on(ev, layerOrFn, fn) { const k = fn ? ev + ":" + layerOrFn : ev; (handlers[k] = handlers[k] || []).push(fn || layerOrFn); }
  once(ev, fn) { this.on(ev, fn); } fire(k, e) { (handlers[k] || []).forEach(f => f(e)); }
  addControl() {} addSource(id, s) { calls.sources.push(id + "=" + (s.url || s.type)); } addLayer() {}
  getLayer() { return true; } setFilter(l, f) { calls.filters[l] = JSON.stringify(f); } setLayoutProperty(l, k, v) { calls.layout[l] = v; } setLayerZoomRange(l, a) { calls.zoom[l] = a; }
  fitBounds() { setTimeout(() => this.fire("moveend"), 0); } getCenter() { return { lng: -2, lat: 51 }; }
  getBounds() { const b = DATA.bounds; return { getWest: () => b[0], getSouth: () => b[1], getEast: () => b[2], getNorth: () => b[3] }; }
  getCanvas() { return { style: {} }; }
}
class Popup {
  constructor() { this.el = w.document.createElement("div"); this.h = {}; }
  setLngLat(l) { this.l = l; return this; } getLngLat() { return this.l; }
  setHTML(h) { this.el.innerHTML = h; return this; }
  addTo() { w.document.body.appendChild(this.el); this.open = true; return this; }
  getElement() { return this.el; } on(e, f) { this.h[e] = f; return this; } isOpen() { return this.open; }
  remove() { this.el.remove(); this.open = false; this.h.close && this.h.close(); }
}
w.maplibregl = { Map, Popup, NavigationControl: class {}, ScaleControl: class {}, addProtocol() {} };
w.pmtiles = { Protocol: class { tile() {} } };
w.URL.createObjectURL = () => "blob:x";
let downloaded = "";
w.Blob = class { constructor(parts) { downloaded = parts.join(""); } };
w.HTMLAnchorElement.prototype.click = function () {};  // downloads aren't testable in jsdom
try { w.eval(html.split("<script>")[1].split("</script>")[0]); } catch (e) { errors.push("script: " + e.message); }
const $ = s => w.document.querySelector(s), $$ = s => w.document.querySelectorAll(s);
setTimeout(() => {
  try {
    const out = [];
    out.push("headline: " + $("#headline").textContent.replace(/\s+/g, " ").trim());
    out.push("legend: " + [...$$("#legend .count")].map(e => e.textContent).join(" | "));
    out.push("sources: " + calls.sources.join(", "));
    out.push("list: " + $$("#list .item").length + " items; " + $("#list-note").textContent);
    for (const u of ["km2", "mi2", "ha"]) { $(`#units [data-u=${u}]`).click(); out.push(u + " -> " + $("#legend .count").textContent + " / " + $("#list .item .a").textContent.trim()); }
    // click a landlocked polygon on the map (properties as a vector tile gives them), then a dot
    const p = DATA.candidates.find(p => p.status === "landlocked");
    (handlers["click:c-fill"] || [])[0]({ features: [{ properties: { cluster_id: p.cluster_id } }], lngLat: { lng: -2, lat: 51 } });
    out.push("popup after map click: " + ($(".pop h3") || {}).textContent + " | " + ($(".pop p") || {}).textContent);
    out.push("links: " + [...$$(".pop .links a")].map(a => a.textContent.trim() + "=" + a.href.slice(0, 50)).join(" ; "));
    // a feature not in the candidate list (accessible): popup from tile properties alone
    (handlers["click:c-fill"] || [])[0]({ features: [{ properties: { cluster_id: "X1", status: "presumed_accessible", area_ha: 3.2,
      reason: "PRoW at 0m", lat: 51, lon: -2, weak_entry: 0 } }], lngLat: { lng: -2, lat: 51 } });
    out.push("tile-only popup: " + $(".pop .badge").textContent + " / street view link: " + /Street View/.test($(".pop .links").textContent));
    (handlers["click:c-fill"] || [])[0]({ features: [{ properties: { cluster_id: p.cluster_id } }], lngLat: { lng: -2, lat: 51 } });
    $('#units [data-u="mi2"]').click(); out.push("popup in mi2: " + $(".pop b").textContent);
    $('.pop [data-k="verdict"] [data-v="genuine"]').click(); out.push("desk check badge: " + ($("#list .v") || {}).textContent);
    $('.pop [data-k="outcome"] [data-v="blocked"]').click();
    $(".pop #f-note").value = 'Locked gate, "private" sign';
    $(".pop #f-save").click(); out.push("after field save badge: " + ($("#list .v") || {}).textContent +
      " / popup keeps note: " + $(".pop #f-note").value + " / outcome on: " + $('.pop [data-k="outcome"] .on').textContent);
    $('.pop [data-k="verdict"] [data-v="genuine"]').click(); out.push("verdict toggled off, field kept: " + ($("#list .v") || {}).textContent);
    const weak = DATA.candidates.find(p => p.weak_entry);
    if (weak) { (handlers["click:c-fill"] || [])[0]({ features: [{ properties: { cluster_id: weak.cluster_id } }], lngLat: { lng: -2, lat: 51 } });
      out.push("weak popup: " + ($(".pop .warn") || {}).textContent); }
    (handlers["click:c-dot"] || [])[0]({ features: [{ properties: { cluster_id: p.cluster_id } }], lngLat: { lng: -2, lat: 51 } }); out.push("dot click popups open: " + $$(".pop").length);
    $("#list .item").click();
    $('#legend input[data-s="landlocked"]').click(); out.push("after hiding landlocked: " + $$("#list .item").length + " items");
    $("#s-weak").click(); out.push("after hiding weak: " + $$("#list .item").length + " items; c-weak filter " + (calls.filters["c-weak"] || "").slice(0, 60));
    for (const id of ["t-prow", "t-barriers", "t-gates"]) $("#" + id).click();
    out.push("view on load: dots " + calls.layout["c-dot"] + ", fill from z" + calls.zoom["lo-fill"]);
    $('#view [data-v="areas"]').click();
    out.push("areas view: dots " + calls.layout["c-dot"] + ", fill from z" + calls.zoom["lo-fill"]);
    $('#view [data-v="bubbles"]').click();
    $('#basemap [data-b="sat"]').click(); $("#export").click();
    out.push("export: " + downloaded.split("\n").length + " lines; " + downloaded.split("\n")[1]);
    out.push("all toggles clicked");
    console.log(out.join("\n"));
  } catch (e) { errors.push("test: " + e.stack); }
  setTimeout(() => { console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "NO ERRORS"); process.exit(0); }, 50);
}, 100);
