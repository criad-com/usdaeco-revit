# Full facility replay verification

Version 0.1.4 publishes the complete pinned base facility after two offline
receipt edits. The source and relocated layouts use exact tag exports of all
11 dependencies. No dependency checkout was modified or built. Detailed
measurements are in [the JSON evidence](facility-replay-verification.json).

| Acceptance | Measured result |
|---|---|
| Full gate, original layout | 52 checks, 0 failed, 1 not run |
| Full gate, independently relocated consumer and dependencies | 52 checks, 0 failed, 1 not run |
| Structure in both layouts | 29 checks, 0 failed; toolchain v0.3.8 |
| Pytest in each layout | 128 passed; 1 live test not run |
| Fresh versus committed result, both layouts | ResultStale PASS |
| Source alias and archived review paths | S29 PASS; 7 asset paths; alias recreated after relocation |
| Source → published crate | 12,266 → 12,276 prims; all source prims and identities retained |
| Source manifest census | 2,954 elements; 33 spaces; 2 levels; 6,212 ports; 2,987 meshes |
| Offline replay | 2 accepted edits; 14 driver components; 2 bodies; 0 differences |
| Repeat apply / native mutations | 0 / 0 |
| Core UsdValidation | All 8 loaded; 0 errors; 2 pre-existing classification warnings; 0 new warnings |
| Stock USD crate composition | 12,276 prims; 0 composition errors; complete fallbacks |
| Stock archived session composition | 12,273 prims; 0 composition errors |
| Crate / complete result tree | 1,659,444 / 1,967,525 bytes |
| Largest archived USDA layer | 10,640 bytes |
| Stock vanilla image | 960 × 600; 286,381 bytes; facility cutaway with replayed subjects |
| Owned renders | Overview and edited corner; 286,408 and 358,569 bytes |
| Sanitization | S25 PASS; flattened crate sweep 0 findings; old public org forms 0 |
| Live Revit execution | NOT RUN; no endpoint |
| Nix | One attempt; evaluation and builds NOT PROVEN |

The ten extra prims are four axis/proxy guides, the camera scope and two
cameras, and three diagnostic scopes. The two source warnings classify utility
intakes as IFC proxies. They are present before replay. Tests verify unchanged
source mesh points/topology outside the two subjects, every original port's
placement and connections, driver/derived separation, absent/duplicate/wrong
identity refusals, moved-path resolution by identity and stale-record refusal.

The image is rendered from a copied crate in an isolated stock USD process.
Roof visibility and subject colours come from the archived presentation layer.
All roof geometry remains in the crate. The source release contains the
unchanged base publication generated at v0.4.2, with hashes recorded in the
example manifest. Embree image hashes describe each committed image; fresh
renders are checked for valid non-uniform content, not identical pixels.

To reproduce, select the pinned checkout roots and interpreter from the README:

```sh
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" check.py
```

For relocation, copy the consumer without transient `out/` or `inputs/source`,
place all exact dependency exports under a differently nested directory, reset
the documented environment variables and run the same gate. It regenerates
the alias and compares fresh results against the committed publication.

## Deviations

- The base contains no pipe inside the office wing. The nearest chilled-water
  pipe at the wing boundary is used with a real office wall.
- Former synthetic identities are absent. Receipts were regenerated offline
  from source USD tessellations. Native solving, native receipt playback,
  exporter port mapping and export/re-import are NOT PROVEN; live is NOT RUN.
- The pipe edit changes diameter with fixed endpoints to preserve source port
  placement and connections. The fixture's solid-body size table is illustrative.
- Data centre v0.4.6 contains the unchanged base publication from generator v0.4.2.
- Toolchain v0.3.8 has 29 structure rules including S29.
- Images use 960 × 600: the first 1280 × 800 stock render exceeded the 400 KB
  cap. The published views meet both size limits.
- The one offline Nix attempt used local overrides for all direct inputs,
  restricted URI evaluation and a network sandbox. That sandbox denied access
  to the Nix daemon socket before evaluation. No build ran and no retry was made.

Remaining work requires a reachable native runtime: capture real raw receipts,
exercise exporter port identity mapping, solve the native model, export/re-import
and demonstrate convergence. Native compilation and installed-wheel verification
also remain unproven. This release adds no native-success claim.
