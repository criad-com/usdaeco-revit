# Revit: the parametric host

## 1 The problem

A coordinator can move a wall or pipe in a review model without preserving the
authoring model’s joins, connected ports or type rules. Re-entering those edits
in the authoring tool costs time and makes the two models drift. This integration
returns resolved native values and diagnostics after an explicit driver edit.

## 2 The data as it arrives

The source is a resident native document plus an IFC export and durable native bindings.
USD carries identity, classification, axes, sections and connection relationships.
The host consumes JSON operation records; it never consumes edited meshes.
The IFC GUID decodes losslessly to the core UUID. Binding handles are caches.

## 3 The model in USD

There are no new typed prims or APIs in this integration. Referents, ports and
spatial containers come from core. Wall and pipe kinds come from their driver
libraries, with camera drivers from the CCTV library. The native host returns
plain meshes carrying AecoDerivedGeometryAPI; all derived opinions have their own layers.

```mermaid
flowchart LR
  I[USD intent drivers] --> S[Sync preflight]
  S --> H[Revit native transaction]
  H --> R[Result and diagnostic layers]
  R --> C[IFC reimport and convergence]
```

## 4 Workflow

1. Configure the checkout paths in the README and the host runtime in [host.md](host.md).
2. Initialize a session from model.usda and its native document using the `revit` entry point.
3. Author wall or pipe drivers in intent.usda; apply them through sync.
4. Read the returned values, export and compare by core identity.
5. Repeat apply; converged intent causes zero native mutations.

Run `env -u PYTHONPATH "$AECO_PYTHON" examples/roundtrip/run.py` for the complete example.

## 5 Validation

| Rule or check | Severity | Catches |
|---|---|---|
| Structure S01–S29, applicable scopes | error | Missing contract files, pin drift, private terms, invalid images |
| Sync preflight | error | Derived edits, stale intent, unsupported operations, invalid pipe sizes |
| Native closure and readback | error | Missing accepted driver values or separated connected ports |
| Repeat apply | error | Unnecessary native mutations |
| Convergence | error / info | Driver differences / tessellation body differences |
| Native runtime | PASS or NOT RUN | Whether the native host was actually exercised |

## 6 The example on the demo data centre

The complete base facility is composed through `inputs/source` from data centre
v0.4.9. Its unchanged publication was generated at v0.4.2. The source manifest
supplies the census: 12,266 source prims, 2,954 elements, 33 spaces, two levels,
6,212 ports and 2,987 meshes. Every source identity and prim is retained.

The recording was regenerated offline from two source elements: the office wall
`wall_l1_h_010` and the nearest chilled-water pipe `rt_clg_fws_main_r_Seg_9` at
the office boundary. Resolution uses `aeco:id`, never these display paths. The
base contains no pipes inside the office wing. Two edits change wall height
3.0 → 3.4 m and nominal pipe diameter 0.20 → 0.25 m. The fixture scales the
source tessellations; it does not claim a native solve. Source port identities,
placements and symmetric connection relationships are retained.

The offline replay accepts two edits, compares 14 driver components and two
bodies with zero differences, and repeats with zero mutations. Resolved drivers,
derived measurements/bodies, seed, intent and presentation remain separate
review layers. The flattened crate contains the whole facility with a roof
cutaway; cyan marks the wall and orange marks the pipe. See the
[example](../examples/roundtrip/README.md) and
[verification](facility-replay-verification.md).

## 7 Trade-offs and alternatives

A native transaction preserves joins, hosted openings and routing preferences. An IFC import into Revit produces DirectShapes and does not recreate editable native walls and pipes.
Host-specific behaviour is exposed as diagnostics. Geometry agreement uses bounds
and volume, not triangle ordering. Cross-route identity uses IFC_GUID, never Mark.

## 8 Out of scope and open questions

Live rows are NOT RUN unless an endpoint is configured. The archived release has summary evidence but no raw wall/pipe receipt payloads; native replay is not proven.
Exact solid evaluation and automatic geometry pushback remain outside this release.
Native compilation and an installed wheel are separate from the source-based gates.

## 9 Status

Version 0.1.5, using toolchain v0.3.10 and data centre v0.4.9; all eleven public release pins are recorded in dependencies.json; supported ranges are unchanged.
Current results and deviations are in [public re-pin verification](public-repin-verification.md);
[acceptance.md](acceptance.md) preserves earlier release evidence.
