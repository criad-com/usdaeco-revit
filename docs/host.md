# The parametric host

Revit owns native wall joins, hosted openings, pipe routing and commit-time failure
processing. Drivers enter one transaction; resolved drivers, geometry, bindings and
diagnostics return together. The pack compiles inside the native REPL.

| Operation | Adapter behaviour |
|---|---|
| Free pipe end | Set LocationCurve |
| Connected end, keepConnected | Move the fitting; read back the connected set |
| Pipe diameter | Refuse sizes outside the published table before a native call |
| Connect / disconnect | Native connectors and fitting factories; read back take-outs |
| Wall move | MoveElement; read back joined neighbours and hosted openings |
| Joined wall endpoint | Refuse until the same intent removes the join |
| Wall height, flip, constraints, type, compound structure | Native parameters and type operations |
| Wall shortened past cutting opening | Commit-time error, rollback, retained intent |
| Camera create / move / optics / presets / type / delete | Supported script-pack subset; envelope checks before mutation |
| IFC reimport into Revit | DirectShapes; editable native wall/pipe reconstruction is outside scope |
| IFC camera Psets and space Status | Exporter limits remain documented; absence is not invented evidence |

See the released operation research
[§11.5](https://github.com/criad-com/usdaeco-core/blob/v0.8.4/docs/11-host-research-walls-pipes.md#115-revit--the-parametric-host).

## Transport configuration

AECO_REVIT_ENDPOINT selects the REPL endpoint. AECO_REVIT_DOCUMENT is the exact
path of an already resident document. AECO_REVIT_BACKGROUND=1 requires the exact
background document; it never opens, activates or closes a document.
AECO_REVIT_SCOPE=cameras selects the camera-only receipt. AECO_REVIT_WORKDIR selects
the remote directory for the canonical client’s hash-verified upload helper.

Every POST /eval follows GET /status, including reply pages. One endpoint lock
covers threads, processes, readiness and all pages. Busy observations are shared
across instances with at least 120 seconds between polls. A mutation is submitted
once. An ambiguous or corrupt outcome latches the endpoint stopped; inspection
must precede manual removal of the reported stop file.

`transport.ReplClient` provides exchange, evaluate, phase and upload. The builder
compatibility class `transport.Client` uses the same lock, polling and failure
latch; there is no second HTTP transport. The data-centre builder can import this
class and keep its phase driver separately. No builder checkout is changed here.

The [C# pack](../tools/usdaeco_revit/scripts/README.md) is a non-Nix enclave.
Each source hash is verified before submission; returned bytes are SHA-256 checked.
Committed native receipts remain journalled until sync acknowledges successful USD
publication. A failed publication cannot cause the native edit to be replayed.

## Identity and fallback types

IFC_GUID alone joins native elements to IFC/core identity. Mark is a label and may
be duplicated; it never supplies identity. Free ports follow the owner-GUID and
connector recipe. Connected ports expose both exporter candidates until an export
resolves ordering. Sensor children never receive a second element identity.

Initialization declares fallbackPrimTypes for generated AecoPort and AecoLevel
prims. Callers constructing a session themselves must include these root mappings;
sync preserves the input mappings and adds only its own diagnostic mapping.

## Live gate

`aeco-revit live-gate --stage <session-stage> --output <evidence-directory>` runs
the camera rollback suite; add `--case-set datacentre` for its 12-case set.
`--suite wallpipe` selects the archived wall/pipe assertions. The source CLI is
available through `bootstrap` plus `usdaeco_revit.cli.main` without installation.
No endpoint means NOT RUN, not native success. See [acceptance.md](acceptance.md).
