# Changelog

## 0.1.4

- Publish the offline wall/pipe receipt replay inside the complete pinned base
  facility, matched by source identity; preserve all source prims and ports.
- Re-record the fixture offline from source tessellations; separate driver and
  derived layers and retain an explicit NOT RUN live row.
- Add a stock USD facility cutaway, a corner view, source-alias portability and
  full-facility identity, geometry and topology regression checks.
- Pin toolchain v0.3.8 and data centre v0.4.6; retain every other dependency pin.
- public names → github.com/criad-com.
- Verify both layouts at 52 checks, 0 failed, 1 not run; structure 29/0;
  pytest 128 passed and one live test not run. ResultStale passes in both layouts.
  The one Nix attempt stopped before evaluation; see the verification report.

## 0.1.3

- Re-pin to train aeco-0.7.0: all 11 direct dependencies and flake inputs;
  retain historical evidence under separate fixture declarations.
- Admit IFC v0.2.0 in the Python dependency range and update core/axis
  version assertions for the pinned releases.
- Refresh round-trip pin metadata; all 14 prims, the crate, authored USD
  layers and committed images remain unchanged.
- Verify 47 checks, 0 failed; structure 28/0; pytest 119 passed, 1 live test
  not run. One Nix attempt could not resolve the local nixpkgs input.

## 0.1.2

- Publish the converged offline round trip as a self-contained USD crate, editable
  session layers and a stock USD render, using the toolchain v0.3.1 result contract.
- Check committed-result freshness, plugin-free composition and rendering, and
  explicitly load and run every core validator against the published stage.
- Use fixed provenance for the synthetic example and adopt the MIT licence.
- Keep live execution NOT RUN and archived native receipt replay NOT PROVEN.

## 0.1.1

- Re-pin to core v0.9.1, axis v0.1.0 and data centre v0.4.1.
- Adapt plugin registration locally for sync v0.5.0; retain its engine and host contracts.
- Re-run integration gates and refresh release evidence.

## 0.1.0

- Extract the Revit host and its native protocol from sync v0.4.5.
- Implement sync v0.5.0 lifecycle, receipt and entry-point discovery contracts.
- Add source-based checks, regression cases and a round-trip example.
