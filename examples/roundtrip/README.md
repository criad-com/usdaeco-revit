# Revit offline replay in the full facility

Set the checkout variables listed in the repository README, then run:

```sh
env -u PYTHONPATH "$AECO_PYTHON" examples/roundtrip/run.py
env -u PYTHONPATH "$AECO_PYTHON" examples/roundtrip/run.py --publish
usdview examples/roundtrip/result/example.usdc
```

The source is the complete `base` publication in data centre v0.4.9, unchanged
since its v0.4.2 generator publication. The manifest supplies 2,954 elements,
33 spaces, two levels, 6,212 ports and 2,987 meshes. The stage has 12,266 source
prims. Every source prim and identity survives the replay. The result adds
axis/proxy guides, two review cameras and diagnostic scopes; no synthetic `/Model` replaces it.

| Source element | Core identity | Offline edit |
|---|---|---|
| Office wall `wall_l1_h_010` | `46bb8ddc-66ac-5ee6-ba2f-72feb5bf8844` | Height 3.0 → 3.4 m |
| Boundary pipe `rt_clg_fws_main_r_Seg_9` | `e4b8ed4b-bb06-525a-b907-1cb5c1d03950` | Nominal diameter 0.20 → 0.25 m |

The base has no pipe inside the office wing. The nearest chilled-water pipe at
the wing boundary is used. The recording's former synthetic identities do not
exist in this variant. `inputs/offline-receipts.json` was therefore re-recorded
**offline**, from these source identities and tessellations. `record.py` is the
explicit regeneration command; ordinary runs compare against the committed
recording and refuse stale input. Source counts and provenance come from
`dc.manifest.json`, not a repeated census constant.

The fixture scales the wall mesh vertically and the pipe mesh radially, keeping
its endpoints fixed. Its small diameter table describes the two illustrative
solid tessellations, not a manufacturer's product catalogue. The offline adapter
preserves the recorded USD port identities, placement and connectivity. Native
exporter connector mapping, hydraulic suitability and a native solve are not
proven by this fixture. The production Revit host and sync engine are unchanged.

The replay uses the Revit host lifecycle and driver operations with a recorded
client: two accepted edits, 14 driver components and two bodies compared, zero
differences, zero repeat mutations, zero native mutations. `inputs/recording.json`
is separately audited historical live summary evidence: 11 passed cases,
42 compared drivers, 11 elements, zero recorded driver differences. It contains
no raw wall/pipe payloads. That archived evidence is not counted as current
execution. **Live execution is NOT RUN; native receipt replay and native
export/re-import remain NOT PROVEN.**

The self-contained `result/example.usdc` needs no plugins or source checkout.
`result/vanilla.png` is its isolated stock USD render. Cyan marks the replayed
wall, orange marks the pipe; a presentation layer hides roofs so the corner is
visible within the facility. `renders/edited_corner.png` provides a closer view.
Hidden roofs remain in the crate and can be made visible in a viewer.

`result/layers/out/seed.usda` composes the full source and the offline size table.
`result/layers/out/replay/session/` retains resolved drivers, derived bodies and
measurements, consumed intent, diagnostics, kind layer and session root.
`result/layers/out/presentation.usda` contains only review visibility and colour.
The original mesh is replaced at its own `Geom` path; geometry is never an edit
request. Fixed document token `offline-demo-datacentre-01` and example clock
`2026-01-01T00:00:00+00:00` keep review layers deterministic.

The runner creates the ignored `inputs/source` alias from
`AECO_DATACENTRE_ROOT`. Archived review layers reference
`../../../inputs/source/dist/base/dc.usda` from `result/layers/out/seed.usda`.
After relocating the checkout, run again with the same release root to refresh
that alias. Source hashes and the flattened crate contain no local paths.

Ordinary runs write only ignored `out/` evidence. `--publish` refreshes the
result, renders and manifest after expected findings match. The gate checks
freshness, stock composition/rendering, portable review layers and inventory
hashes. Result size is capped at 10 MB, each USDA at 2 MB, each image at 400 KB
and 1600 pixels. Current measurements are in
[verification](../../docs/public-repin-verification.md).
