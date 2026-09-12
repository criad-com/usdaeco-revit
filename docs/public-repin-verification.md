# Public re-pin verification

Version 0.1.5 changes all eleven direct family inputs to public release tags.
The source gate used the exact tagged revisions recorded in dependencies.json.
Supported core, axis, sync and Python dependency ranges remain unchanged.
The [machine-readable receipt](public-repin-verification.json) records the
checks, tag lookups, artifact hashes and fresh render measurements.

| Acceptance | Measured result |
|---|---|
| Release metadata | 0.1.5 in library.json, pyproject.toml and the source package |
| Direct family inputs | 11 tag URLs; 11 matching tagged source revisions |
| Public tag lookup | 8 direct tags verified; 3 NOT PROVEN; 2 recursive tags verified |
| Requirement ranges widened | 0 |
| Source smoke | 15 passed |
| Complete gate | 52 checks, 0 failed, 1 not run |
| Structure | 29 checks, 0 failed; toolchain v0.3.10, including S05 |
| Pytest through the complete gate | 128 passed, 1 live test not run |
| Core UsdValidation | All 8 loaded; 0 errors, 2 existing proxy classification warnings |
| Offline replay | 2 accepted edits, 14 driver components and 2 bodies; 0 differences |
| Repeat / native mutations | 0 / 0 |
| Published facility | 12,266 source prims retained; 12,276 published prims |
| Rebuilt crate / archived USD layers | 1 / 9, all byte-identical to v0.1.4 |
| Source publication | 3 USD layers and source manifest byte-identical |
| Crate / complete result tree | 1,659,444 / 1,967,525 bytes |
| Fresh render proof | 3 valid 960 × 600 images; committed image bytes retained |
| ResultStale / stock composition | PASS / 0 composition errors |
| Publication term sweep / old public-owner refs | 0 findings / 0 refs |
| Whitespace check | git diff --check PASS |
| Nix | 1 offline attempt; local evaluation reached builds; packaging NOT PROVEN |
| Live execution | NOT RUN; native solve and export/re-import remain NOT PROVEN |

| Input | Selected tag |
|---|---|
| usdaeco-toolchain | v0.3.10 |
| usdaeco-core | v0.9.5 |
| usdaeco-axis | v0.1.5 |
| usdaeco-sync | v0.5.5 |
| usdaeco-datacentre | v0.4.9 |
| usdaeco-scenarios | v0.8.0 |
| usdaeco-cctv | v0.5.6 |
| usdaeco-buildup | v0.2.5 |
| usdaeco-wall | v0.2.5 |
| usdaeco-pipe | v0.2.5 |
| usdaeco-ifc | v0.2.2 |

Toolchain v0.3.10 selects processing toolchain v0.4.0 recursively and retains
a core v0.9.2 compatibility fixture. Both recursive tags resolved publicly.
No family input in this repository uses a commit hash as its ref.

Run the README environment setup, then the documented publication step:

```sh
env -u PYTHONPATH "$AECO_PYTHON" examples/roundtrip/run.py --publish
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" check.py
```

The rebuilt crate and all nine archived USD layers match v0.1.4 byte-for-byte.
The offline receipts, expected findings, source layers and source manifest also
retain their bytes. The publication changes only the manifest's tags and source
revisions, the result README's source tag, and that README's inventory hash.
This agrees with the data centre's v0.4.9 changelog: all 31 published dist files
retain their bytes. No dependency behaviour change was observed in this replay.

## Deviations

- Unauthenticated public Git lookups failed for scenarios v0.8.0, CCTV v0.5.6
  and IFC v0.2.2. Their prescribed tags and exact source revisions are retained;
  public resolution of these three inputs remains NOT PROVEN. The other eight
  direct tag lookups succeeded. Source validation does not establish outsider
  access, so online resolution remains a release review item.
- The publisher regenerated all three images. Sampling changed their bytes,
  with mean absolute RGB differences of 0.4681, 0.4688 and 0.3691 on the 0–255
  channel scale. After verifying identical stages and valid fresh renders,
  the committed image bytes were retained and their inventory refreshed. S28
  independently rendered the committed crate in a process without family plugins.
- The single `nix flake check --offline --no-write-lock-file` attempt used local
  overrides for all eleven direct inputs, restricted URI evaluation and disabled
  substitutes and the flake registry. It evaluated the local package, integration
  check, development shell and example app, then started dependency builds.
  After 87.06 seconds Nix exited with `interrupted by the user` (exit 1).
  No completed integration build was recorded; packaging remains NOT PROVEN.
  No second attempt was made.
- Historical verification and native summaries retain the versions that produced
  them under dependencies.json fixtures. They are archived evidence. Current live
  execution remains NOT RUN; no native runtime was available for this release.

Remaining release work is online input resolution and a completed Nix build.
Native solve, exporter port mapping, export/re-import, native compilation and
installed-wheel verification retain their existing unproven status.
