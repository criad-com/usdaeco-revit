# Acceptance — 0.1.2

Measured on 2026-09-11 from source with core v0.9.1, axis v0.1.0, sync v0.5.0,
IfcOpenShell 0.8.5 and OpenUSD 26.8. The result contract pins toolchain v0.3.1;
the source gate used v0.3.2 for its MIT licence checks, as recorded in
[dependencies.json](../dependencies.json). Machine-readable evidence is in
[acceptance.json](acceptance.json).

| Row | Result |
|---|---|
| check.py | 47 checks, 0 failed |
| Structure | 28/28 PASS; 14 applicable rules, 14 schema rules not applicable |
| pytest | 119 passed; 1 live test skipped |
| Core validators | All 8 imported and loaded through UsdValidation; published crate: 0 errors, 2 ExactWithoutTolerance warnings |
| S27 | PASS; 14 prims after relocation, 0 composition errors, complete stock fallbacks, no external assets; fresh result matches committed result |
| S28 | PASS; independent stock Embree render with no family plugins; 1280 × 800, non-uniform |
| Result inventory | 11 files, 74,223 bytes / 10,000,000-byte cap |
| Flattened stage | example.usdc: 4,399 bytes; final converged synthetic view |
| Own layers | 8 USDA files, 9,929 bytes total; largest 4,362 bytes / 2,000,000-byte cap |
| Vanilla image | 59,036 bytes / 400,000-byte cap; 1280 × 800 / 1600-pixel cap |
| Synthetic convergence | 2 accepted edits, 14 drivers, 2 illustrative bodies; 0 differences; repeat 0 mutations |
| Final values | Wall height 2.6 m, pipe length 2.5 m; published driver and body values checked |
| Archived wall/pipe audit | 11/11 case summaries; 42 drivers on 11 elements; 0 recorded driver differences |
| Archived camera evidence | 12/12 cases; historical evidence only |
| Current live gate | NOT RUN |
| Sanitization | S25 PASS, including retained result layers; decoded crate term sweep PASS |
| Licence | MIT; S01 PASS with toolchain v0.3.2; runtime dependency licences named in README |
| Nix | One offline attempt; public axis v0.1.0 input returned HTTP 404 |

The published [result](../examples/roundtrip/result/README.md) includes the
intent, result, diagnostics and derived layers. Intent is empty after accepted
edits, and diagnostics contain no findings. Its synthetic provenance uses a
fixed example clock and document token. Normalized crate contents and all
authored text layers match a fresh run; render hashes record each image and
are not expected to match across runs.

## Deviations

- Toolchain v0.3.1 requires Apache text in S01 and rejects the MIT copyright
  attribution in S25. Verification therefore used the released v0.3.2 fixes;
  publication, normalization and vanilla-render code are unchanged from v0.3.1.
  The requested v0.3.1 pin remains in the flake and dependencies, with the
  v0.3.2 substitution recorded under observations.
- Dependency checkouts advanced beyond their pins during verification. The
  final gate used local release archives for core v0.9.1, axis v0.1.0, sync
  v0.5.0, IFC v0.1.0, wall/pipe v0.1.2, plus the frozen build-up v0.1.2 and
  CCTV v0.4.8 sources. Archives are transient verification inputs and are not
  vendored. No shared dependency checkout was changed or built.
- The source mode is an explicit synthetic override. The archive contains
  summary findings, without raw wall/pipe request/reply payloads. This release
  publishes the final converged receipt replay; a new native export/re-import,
  replay of recorded native payloads and a pinned full-facility round trip
  remain NOT PROVEN. Live rows remain NOT RUN.
- The two existing derived axis guides declare exactness without a tolerance.
  Core reports two ExactWithoutTolerance warnings; no validator is skipped or
  severity changed.
- The single `nix flake check --offline --no-write-lock-file` attempt failed
  while resolving the public axis input (HTTP 404). Nix packaging and an
  installed wheel remain NOT PROVEN; tests use source metadata without package
  installation. The native script enclave was not compiled or executed.
