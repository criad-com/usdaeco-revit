from usdaeco_revit.runtime import python as run_python
"""C4 native IFC transactions, optional delegation and Revit receipt contracts."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import os
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom
from aeco_sync.cctv import SENSOR, PRESET, normalize_revit_camera
from aeco_sync.edits import Edit, collect, preflight, classify
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.readback import bind, publish
from usdaeco_ifc.host import IfcHost
from usdaeco_revit.host import RevitHost
from usdaeco_revit.transport import script_pack, ReplClient
from usdaeco_revit.convergence import compare_receipts
from aeco_sync import engine

CCTV = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition("AecoCctvSensorAPI") is not None
requires_cctv = pytest.mark.skipif(not CCTV, reason="Set AECO_CCTV_ROOT before registration for optional camera integration")
CASES = [c["id"] for c in json.loads((Path(__file__).parent / "fixtures/cases.json").read_text()) if c.get("family") == "camera"]


@requires_cctv
@pytest.mark.parametrize("case", CASES)
def test_ifc_camera_cases(tmp_path, case):
    from usdaeco_revit.gates.cctv import run_case
    assert run_case(tmp_path, case)["passed"]


def native_camera(dialect="dome", heads=1, presets=False):
    pan = "FOV Pan" if dialect == "dome" else "FOV Camera Rotation"
    tilt = "FOV Tilt" if dialect == "dome" else "FOV Camera Tilt"
    p = {pan: 10., tilt: 25., "FOV Actual Focal Length": 3., "FOV Desired Focal Length": 3.,
         "FOV Distance to Object": 15., "FOV Target Pixel Density": 125, "Corridor Format": False,
         "Detect": True, "Observe": True, "Recognize": True, "Identify": True, "Scenario": "DOOR"}
    for n in range(1, heads + 1):
        if heads > 1:
            p.update({f"FOV {n} Pan": n * 10., f"FOV {n} Tilt": 25., f"FOV {n} Actual Focal Length": 3.})
        if presets:
            p[f"Preset {n}"] = True
    tp = {"FOV Focal Length Minimum": 3., "FOV Focal Length Maximum": 8.5,
          "FOV Horizontal Maximum": 104., "FOV Horizontal Minimum": 34.,
          "FOV Vertical Maximum": 76., "FOV Vertical Minimum": 26.,
          "FOV Horizontal Resolution": 2592, "FOV Vertical Resolution": 1944,
          "Origin Horizontal": .133, "Origin Vertical": .035, "Placement ID": 17161}
    # Independent family equations at the wide endpoint; no calls to density.py.
    params = {"Horizontal Angle": math.radians(52), "Vertical Angle": math.radians(38), "Focal Length": 3., "Tilt": 25., "XEff": 6 * math.tan(math.radians(52))}
    for suffix, density in zip(("Det", "Obs", "Rec", "Id", "UD"), (26, 62, 125, 250, 125)):
        params["T_Res_" + suffix] = density
        params["RG_Length_" + suffix] = min(15., 2592 / (math.radians(104) * density))
    return dict(path="/Model/Level/Camera", ref="camera-uid", localRef="10", ifcGuid="1iQWvF2Ib4PwCs9WY7WYHZ",
                id="6c6a0e4f-0929-4477-a336-260887822463", version="element-v1", document="project-id", kind="camera",
                matrix=Gf.Matrix4d().SetTranslate(Gf.Vec3d(1, 2, 2.9)), drivers={}, derived={}, ports=[], joins={},
                type=dict(path="/_Types/Dome", ref="type-uid", ifcGuid="0BfVFTBKT8$uAhh8_KDPyj"),
                cameraParameters=p, typeParameters=tp, mirrored=False, level="Lobby", rotation=0., offsetFromHost=2.9,
                subInstances=[dict(role="fov", subId=str(100 + n), parameters=dict(params)) for n in range(heads)]
                             + [dict(role="symbol", subId="200", parameters={})])


@pytest.mark.parametrize("dialect,heads,presets,count", [("dome",1,False,1),("bullet",1,False,1),("dome",4,False,4),("dome",4,True,1)])
def test_revit_camera_dialects_heads_and_presets(dialect, heads, presets, count):
    row = normalize_revit_camera(native_camera(dialect, heads, presets))
    assert len(row["sensors"]) == len(row["type"]["sensors"]) == count
    sensor = row["sensors"][0]
    assert sensor["drivers"][SENSOR + "pan"] == 10.
    assert sensor["drivers"][SENSOR + "tilt"] == 25.
    assert sensor["refs"][0] == "camera-uid:fov:100"
    assert "camera-uid:symbol:200" in sensor["refs"]
    assert len(sensor["presets"]) == (heads if presets else 0)
    assert row["type"]["sensors"][0]["drivers"][SENSOR + "focalRange"] == [3.,8.5]


@requires_cctv
def test_convergence_checks_native_angles_clipped_radii_and_missing_evidence():
    row = normalize_revit_camera(native_camera())
    receipt = dict(touched=[row], meshes={})
    report = compare_receipts(receipt, receipt)
    assert report["cctvConverged"] and len(report["cameras"][0]["comparisons"]) == 7
    changed = copy.deepcopy(receipt)
    changed["touched"][0]["sensors"][0]["fov"][0]["parameters"]["RG_Length_Id"] += .002
    report = compare_receipts(changed, receipt)
    assert not report["cctvConverged"]
    assert any(d["code"] == "cctvDivergence" and d["severity"] == "info" and not d["blocking"] for d in report["diagnostics"])
    changed["touched"][0]["sensors"][0]["fov"] = []
    assert not compare_receipts(changed, receipt)["cctvConverged"]


@requires_cctv
def test_sensor_stock_attributes_are_always_derived(tmp_path):
    from usdaeco_revit.gates.cctv import fixture, SENSOR_PATH
    session = fixture(tmp_path)
    sensor = session.stage.GetPrimAtPath(SENSOR_PATH)
    for name in (*UsdGeom.Camera.GetSchemaAttributeNames(False), "xformOpOrder", "xformOp:rotateXYZ"):
        assert classify(sensor, name) == "derived", name
    assert classify(sensor, PRESET + "Preset_1:pan") == "section"


@requires_cctv
@pytest.mark.parametrize("case", ["C-zoom-clamped", "C-derived-authored"])
def test_camera_refused_before_any_host_apply(tmp_path, monkeypatch, case):
    from usdaeco_revit.gates.cctv import fixture, author
    session = fixture(tmp_path)
    author(session, case)
    def forbidden(*args):
        pytest.fail("Refused camera intent must not call the host")
    monkeypatch.setattr(IfcHost, "apply", forbidden)
    assert engine.apply(session)["accepted"] == 0
    client = type("NoNetwork", (), {"exchange": forbidden})()
    current = session.current()
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        for prim in current.TraverseAll():
            if prim.HasAPI("AecoCctvCameraAPI"):
                bind(session.stage.GetPrimAtPath(prim.GetPath()), "revit", "camera-uid", "10", "v1", "project-id")
    native = RevitHost(session, client=client, expected_path="fixture.rvt")
    diag = Diagnostics()
    accepted, _ = preflight(session, collect(session.stage, session.intent, session.current()), "revit", native.version(), diag)
    assert not accepted and len(diag.items) == 1


@requires_cctv
@pytest.mark.parametrize("case", [c for c in CASES if c not in ("C-zoom-clamped", "C-derived-authored")])
def test_revit_camera_operations_have_supported_wire_and_owner_binding(tmp_path, case):
    from usdaeco_revit.gates.cctv import fixture, author, CAMERA
    session = fixture(tmp_path)
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        bind(session.stage.GetPrimAtPath(CAMERA), "revit", "camera-uid", "10", "v1", "project-id")
    author(session, case)
    host = RevitHost(session, client=object(), expected_path="fixture.rvt")
    diag = Diagnostics()
    edits, _ = preflight(session, collect(session.stage, session.intent, session.current()), "revit", host.version(), diag)
    assert edits and not diag.items
    assert all(host.supports(e) for e in edits)
    request = host.request(edits, rollback_only=True)
    assert request["rollbackOnly"] and request["expectedPath"] == "fixture.rvt"
    assert all(e["ref"] == "camera-uid" for e in request["edits"] if e["operation"] != "create")


@requires_cctv
def test_revit_camera_publication_has_inherited_optics_and_no_sensor_identity(tmp_path):
    from usdaeco_revit.gates.cctv import fixture
    session = fixture(tmp_path)
    row = normalize_revit_camera(native_camera())
    result, _ = session.ensure_host("revit")
    receipt = dict(touched=[row],meshes={},stamp="offline native receipt")
    publish(session, receipt, result, "revit", "v1", "project-id")
    current = session.current()
    sensor = current.GetPrimAtPath(row["path"] + "/Sensor_0")
    assert sensor.GetAttribute("aeco:host:revit:ref").Get() == "camera-uid:fov:100"
    assert list(sensor.GetCustomDataByKey("aecoSync:bindingRefs")) == ["camera-uid:fov:100", "camera-uid:symbol:200"]
    assert not sensor.GetAttribute("aeco:id")
    assert not result.GetPropertyAtPath(sensor.GetPath().AppendProperty(SENSOR + "focalRange"))
    assert sensor.GetAttribute(SENSOR + "focalRange").Get() == Gf.Vec2d(3, 8.5)
    assert sensor.GetAttribute("focalLength").Get() == 3.


def test_script_pack_camera_operations_and_units_are_present():
    source = script_pack({}, "camera-test")
    for fragment in ("OST_SecurityDevices", '"AXIS Model"', "GetSubComponentIds", '"Offset from Host"',
                     "camera.Mirrored", "frame.BasisX.Y", "RotateElement(c.Doc, camera.Id, vertical, delta)",
                     "NewFamilyInstance(matrix.Origin, symbol, level", "StructuralType.NonStructural", "camera.Symbol =",
                     "c.Doc.Delete(camera.Id)", "cctvOutOfEnvelope", "CheckCameraFocal(camera.Symbol", "FOV Camera Rotation", "FOV Camera Tilt",
                     "FOV Focal Length Minimum", "FOV Focal Length Maximum", "RG_Length_Det", "XEff", "ViewDetailLevel.Fine", "CameraGuide(item, cameraDoc)"):
        assert fragment in source, fragment
    assert source.index("CheckCameraEdit(c, camera, edit); // direct") < source.index("bool set;")
    assert source.index('var editedRefs = edits.Where(e => S(e["operation"]) != "create")') < source.index('using (var group = new TransactionGroup')
    assert "OpenAndActivateDocument(" not in source and "OpenDocumentFile(" not in source


def test_revit_production_poll_floor_and_status_spacing(tmp_path):
    """Idle statuses are polled once per step without delay; after any instance
    observes a busy REPL, every instance waits the poll floor before polling."""
    now, times, answers = [0.], [], [{"status": "ready"}, {"status": "ready"}, {"busy": True}, {"status": "ready"}, {"status": "ready"}]
    def request(*args):
        times.append(now[0])
        return answers.pop(0)
    def sleep(seconds):
        assert seconds <= 60
        now[0] += seconds
    c = ReplClient("http://repl.example", request=request, clock=lambda: now[0], sleep=sleep, lock_directory=tmp_path)
    c._ready(); c._ready()          # idle: no spacing between steps (reply pages are not paced)
    assert times == [0, 0]
    c._ready()                      # busy once, then ready: the retry waits the 120 s floor
    assert times[2] == 0 and times[3] >= 120
    d = ReplClient("http://repl.example", request=request, clock=lambda: now[0], sleep=sleep, lock_directory=tmp_path)
    d._ready()                      # another instance right after: idle again, no wait
    assert times[4] == times[3]
    from usdaeco_revit.transport import http
    assert ReplClient("http://repl.example", request=http, poll_interval=0, lock_directory=tmp_path).poll_interval == 120


@requires_cctv
def test_camera_transaction_failure_restores_native_file_and_intent(tmp_path, monkeypatch):
    from usdaeco_revit.gates.cctv import fixture, author
    from aeco_sync.stack import digest
    from usdaeco_ifc import _ifc_cctv
    session = fixture(tmp_path)
    file = session.document("ifc")
    before = digest(file)
    author(session, "C-pan-tilt")
    intent = session.intent.ExportToString()
    original = _ifc_cctv.apply_edit
    def fail_after_native_edit(host, entity, edit):
        original(host, entity, edit)
        raise ValueError("seeded native refusal after parameter set")
    monkeypatch.setattr(_ifc_cctv, "apply_edit", fail_after_native_edit)
    result = engine.apply(session)
    assert result["accepted"] == 0 and result["pending"] == 2
    assert digest(file) == before and session.intent.ExportToString() == intent
    assert not (session.path.parent / "host.ifc.ifc").exists()


@requires_cctv
def test_camera_delegation_preserves_other_derived_geometry_and_is_deterministic(tmp_path):
    from usdaeco_revit.gates.cctv import fixture
    from aeco_sync.derive import derive_cameras
    session = fixture(tmp_path)
    layer = session.layer("derived.usda")
    with Usd.EditContext(session.stage, layer):
        UsdGeom.Cube.Define(session.stage, "/Other/Proxy").GetSizeAttr().Set(2.)
    current = session.current()
    derive_cameras(current, layer)
    before = layer.ExportToString()
    derive_cameras(current, layer)
    assert layer.ExportToString() == before
    assert session.stage.GetPrimAtPath("/Other/Proxy").GetAttribute("size").Get() == 2.


@requires_cctv
def test_camera_stage_composes_without_plugins(tmp_path):
    from usdaeco_revit.gates.cctv import fixture, SENSOR_PATH
    session = fixture(tmp_path)
    for layer in session.stage.GetLayerStack():
        if layer.realPath:
            layer.Save()
    env = {k:v for k,v in os.environ.items() if k not in ("PYTHONPATH", "AECO_CCTV_ROOT", "PXR_PLUGINPATH_NAME", "PXR_AR_DEFAULT_SEARCH_PATH")}
    code = "from pxr import Usd,UsdGeom; import sys; s=Usd.Stage.Open(sys.argv[1]); assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoCctvSensorAPI').isUnknown; assert s.GetPrimAtPath('/Model/Level').IsA(UsdGeom.Xform); p=s.GetPrimAtPath(sys.argv[2]); assert p.IsA(UsdGeom.Camera) and p.GetAttribute('focalLength').Get()==3.; assert p.GetChild('Sector').IsA(UsdGeom.Mesh); assert s.Flatten()"
    result = run_python(["-c",code,str(session.path),SENSOR_PATH],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


@requires_cctv
def test_independent_tier_a_preset_change_is_read_back_and_conflicts(tmp_path):
    from usdaeco_revit.gates.cctv import fixture, author, SENSOR_PATH
    from usdaeco_ifc import _ifc_cctv
    import ifcopenshell
    session = fixture(tmp_path)
    author(session, "C-preset")
    f = ifcopenshell.open(session.document("ifc"))
    camera = f.by_type("IfcAudioVisualAppliance")[0]
    pset = _ifc_cctv.pset(f, camera, _ifc_cctv.STANDARD)
    table = next(p for p in pset.HasProperties if p.Name == "PanTiltZoomPreset")
    pose = json.loads(table.DefinedValues[0].wrappedValue)
    pose["pan"] = 11.
    table.DefinedValues = [f.create_entity("IfcText", json.dumps(pose))]
    sensors = _ifc_cctv.load(camera, "Sensors")
    sensors[0]["presets"]["Preset_1"]["pan"] = 11.
    _ifc_cctv.persist_sensors(f, camera, sensors)
    file = tmp_path / "independent.ifc"
    f.write(str(file))
    result = engine.readback(session, "ifc", file)
    assert any(d["code"] == "sync:conflict" for d in result["diagnostics"])
    current = session.current()
    assert current.GetPrimAtPath(SENSOR_PATH).GetAttribute(PRESET + "Preset_1:pan").Get() == 11.
    assert session.status()["pending"] == 3


@requires_cctv
def test_live_camera_clone_preserves_type_inheritance(tmp_path):
    from usdaeco_revit.gates.cctv import fixture, CAMERA, SENSOR_PATH
    from usdaeco_revit.gates.revit import clone_session
    source = fixture(tmp_path / "source")
    clone = clone_session(source, tmp_path / "clone", preserve_inherits=True)
    camera = clone.stage.GetPrimAtPath(CAMERA)
    assert camera.GetInherits().GetAllDirectInherits()
    with Usd.EditContext(clone.stage, clone.intent):
        camera.GetInherits().SetInherits(["/_Types/Alternate"])
    assert list(clone.stage.GetPrimAtPath(SENSOR_PATH).GetAttribute(SENSOR + "pixels").Get()) == [3840, 2160]


@requires_cctv
def test_camera_live_request_selects_resident_background_fixture(tmp_path):
    from usdaeco_revit.gates.cctv import fixture
    session = fixture(tmp_path)
    host = RevitHost(session, client=object(), expected_path=r'C:\Models\cctv.rvt')
    request = host.request(action="snapshot")
    assert request["backgroundDocument"] is True
    source = script_pack(request, "background-camera")
    assert 'request["backgroundDocument"]' in source and 'd != foreground' in source
    assert 'OpenDocumentFile(' not in source and 'OpenAndActivateDocument(' not in source


def native_camera_receipt():
    """Four anonymous cameras with two catalog types for the live runner contract."""
    from aeco_sync.identity import uuid_to_guid
    rows = []
    for n in range(1, 5):
        row = native_camera(presets=n == 4)
        row.update(tag=f"Cam_{n}", ref=f"camera-{n}", localRef=str(n),
                   path=f"/Model/Level/Cam_{n}",
                   ifcGuid=uuid_to_guid(f"00000000-0000-4000-8000-{n:012d}"))
        if n == 4:
            row["type"] = dict(path="/_Types/PTZ", ref="ptz-type",
                               ifcGuid=uuid_to_guid("00000000-0000-4000-8000-000000000010"))
        rows.append(row)
    return dict(touched=rows, meshes={}, diagnostics=[], version="baseline-v1",
                document="project-id", status="snapshot", stamp="offline camera receipt", rollbackVerified=True)


@requires_cctv
@pytest.mark.parametrize("outcome", ["refused", "failed"])
def test_live_suite_retains_failed_case_diagnostics_and_continues(tmp_path, outcome):
    from aeco_sync.stack import Session
    from usdaeco_revit.gates.cctv import fixture
    from usdaeco_revit.gates.cctv_revit import run_suite, CASES
    source = fixture(tmp_path / "source")
    calls = []
    native = dict(severity="error", code="revit:seeded", message="Seeded native failure",
                  blocking=True, nativeSeverity="Error", definitionId="seeded-definition", hostRefs=["1"])
    class Client:
        def exchange(self, request):
            if request["action"] == "snapshot":
                return native_camera_receipt()
            calls.append(request)
            receipt = native_camera_receipt()
            # A committed receipt with blocking diagnostics fails the case assertion;
            # a native refusal raises HostRefused before returning a normalized receipt.
            receipt.update(status="refused" if outcome == "refused" else "committed", diagnostics=[native])
            return receipt
    output = tmp_path / "live"
    report = run_suite(source.path, output, client=Client(), expected_path="cctv.rvt")
    assert not report["fullAcceptance"] and report["status"] == "failed"
    assert list(report["cases"]) == list(CASES) and len(calls) == 7
    for case in CASES:
        entry = report["cases"][case]
        if case in ("C-zoom-clamped", "C-derived-authored"):
            assert entry["status"] == "pass" and entry["requests"] == 0
            continue
        assert entry["status"] == outcome and entry["error"]
        assert entry["rollbackVerified"] and entry["codes"] == ["revit:seeded"]
        assert entry["diagnostics"][0]["definitionId"] == "seeded-definition"
        inspected = Session(output / case / "stage.usda")
        layer = inspected.layer("diagnostics.revit.usda")
        assert 'revit:seeded' in layer.ExportToString() and 'seeded-definition' in layer.ExportToString()
        assert json.loads((output / case / "receipt.revit.json").read_text())["diagnostics"] == [native]
    assert json.loads((output / "acceptance.json").read_text()) == report


def ptz_only_camera():
    """PTZ receipt with no base pose/zoom controls, only four preset heads."""
    row = native_camera(presets=True)
    p = row["cameraParameters"]
    for field in ("Pan", "Tilt", "Desired Focal Length", "Actual Focal Length"):
        p.pop("FOV " + field, None)
    for n in range(1, 5):
        p.update({f"Preset {n}": n == 1, f"FOV {n} Pan": n * 10.,
                  f"FOV {n} Tilt": n * 15., f"FOV {n} Desired Focal Length": 3. + n})
    row["type"] = dict(path="/_Types/PTZ", ref="ptz-type", ifcGuid="000000000010200000000A")
    return row


def test_ptz_only_family_primary_controls_fall_back_to_head_one():
    row = normalize_revit_camera(ptz_only_camera())
    sensor = row["sensors"][0]
    assert len(row["sensors"]) == 1 and len(sensor["presets"]) == 4
    for field, expected in (("pan", 10.), ("tilt", 15.), ("focalLength", 4.)):
        assert sensor["drivers"][SENSOR + field] == expected
        assert sensor["presets"]["Preset_1"][field] == expected
    assert sensor["presets"]["Preset_4"]["focalLength"] == 7.


@pytest.mark.parametrize("dialect", ["dome", "bullet"])
def test_ptz_base_controls_take_priority_over_head_one(dialect):
    row = ptz_only_camera()
    pan, tilt = ("FOV Pan", "FOV Tilt") if dialect == "dome" else ("FOV Camera Rotation", "FOV Camera Tilt")
    row["cameraParameters"].update({pan: 35., tilt: 45., "FOV Actual Focal Length": 6.})
    sensor = normalize_revit_camera(row)["sensors"][0]
    assert [sensor["drivers"][SENSOR + f] for f in ("pan", "tilt", "focalLength")] == [35., 45., 6.]
    assert sensor["presets"]["Preset_1"]["focalLength"] == 4.


@requires_cctv
def test_dome_to_ptz_type_and_focal_intent_reads_back_target_family(tmp_path):
    from usdaeco_revit.gates.cctv import fixture
    from usdaeco_revit.gates.cctv_revit import author
    session = fixture(tmp_path)
    host = RevitHost(session, client=object(), expected_path="cctv.rvt")
    baseline = host.normalize(native_camera_receipt())
    result, _ = session.ensure_host("revit")
    publish(session, baseline, result, "revit", baseline["version"], baseline["document"])
    expected = author(session, baseline, "C-type-swap")
    edits = collect(session.stage, session.intent, session.current())
    request = host.request(edits, rollback_only=True)
    assert {e["name"] for e in request["edits"]} == {"inheritPaths", SENSOR + "focalLength"}
    assert len({e["ref"] for e in request["edits"]}) == 1
    native = ptz_only_camera()
    native.update({k: baseline["touched"][0][k] for k in ("path", "ref", "localRef", "ifcGuid")})
    native["type"] = baseline["touched"][3]["type"]
    native["cameraParameters"]["FOV 1 Desired Focal Length"] = next(iter(expected["attributes"].values()))
    receipt = host.normalize({**native_camera_receipt(), "touched": [native]})
    from aeco_sync.edits import clear
    clear(session.intent, edits)
    publish(session, receipt, result, "revit", "v2", "project-id")
    current = session.current()
    camera = current.GetPrimAtPath(expected["path"])
    assert camera.GetInherits().GetAllDirectInherits() == [Sdf.Path(receipt["touched"][0]["type"]["path"])]
    assert camera.GetChild("Sensor_0").GetAttribute(SENSOR + "focalLength").Get() == native["cameraParameters"]["FOV 1 Desired Focal Length"]


@requires_cctv
def test_duplicate_mark_warning_is_retained_without_failing_live_case(tmp_path, monkeypatch):
    from aeco_sync.stack import Session
    from usdaeco_revit.gates.cctv import fixture
    from usdaeco_revit.gates import cctv_revit
    source = fixture(tmp_path / "source")
    warning = dict(severity="warning", nativeSeverity="Warning", code="revit:duplicateMark",
                   message='Elements have duplicate "Mark" values', blocking=True,
                   definitionId="duplicate-mark-definition", hostRefs=["1", "2"], failingIds=["1", "2"])
    class Client:
        def exchange(self, request):
            receipt = native_camera_receipt()
            if request["action"] == "sync":
                receipt["status"] = "committed"
                for edit in request["edits"]:
                    row = next(r for r in receipt["touched"] if r["ref"] == edit["ref"])
                    row["cameraParameters"]["FOV " + edit["name"].split(":")[-1].capitalize()] = edit["value"]
            receipt["diagnostics"] = [warning]
            return receipt
    monkeypatch.setattr(cctv_revit, "CASES", ("C-pan-tilt", "C-zoom-clamped", "C-derived-authored"))
    output = tmp_path / "live"
    report = cctv_revit.run_suite(source.path, output, client=Client(), expected_path="cctv.rvt")
    assert report["status"] == "pass" and report["fullAcceptance"]
    entry = report["cases"]["C-pan-tilt"]
    assert entry["status"] == "pass" and entry["rollbackVerified"]
    diagnostic, = entry["diagnostics"]
    assert diagnostic["message"] == warning["message"]
    assert diagnostic["severity"] == "warning" and not diagnostic["blocking"]
    assert diagnostic["about"] == ["/Model/Level/Cam_1", "/Model/Level/Cam_2"]
    saved = Session(output / "C-pan-tilt/stage.usda")
    prim = saved.stage.GetPrimAtPath("/Sync/Diagnostics/revit/d0001")
    assert prim.GetAttribute("aeco:diag:message").Get() == warning["message"]
    assert prim.GetAttribute("aeco:diag:severity").Get() == "warning"
    assert prim.GetAttribute("aeco:diag:blocking").Get() is False
    assert json.loads(prim.GetCustomDataByKey("aecoSync:native"))["failingIds"] == ["1", "2"]


@requires_cctv
@pytest.mark.parametrize("elevation", [0., 4.])
def test_unbound_native_cameras_publish_their_level_for_creation(tmp_path, elevation):
    from aeco_sync.identity import guid_to_uuid
    from usdaeco_revit.gates.cctv import fixture
    from usdaeco_revit.gates.cctv_revit import author
    session = fixture(tmp_path)
    raw = native_camera_receipt()
    level_guid = "000000000010200000000B"
    for row in raw["touched"]:
        row["path"] = ""  # First snapshot: these cameras have no prior USD binding.
        row["matrix"] = Gf.Matrix4d().SetTranslate(Gf.Vec3d(1, 2, elevation + 2.9))
        row["levelBinding"] = dict(ref="native-level", localRef="20", ifcGuid=level_guid,
                                   matrix=Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 0, elevation)))
    host = RevitHost(session, client=object(), expected_path="cctv.rvt")
    receipt = host.normalize(raw)
    result, _ = session.ensure_host("revit")
    publish(session, receipt, result, "revit", "v1", "project-id")
    level_path = receipt["touched"][0]["levelBinding"]["path"]
    level = session.stage.GetPrimAtPath(level_path)
    assert level.GetTypeName() == "AecoLevel"
    assert level.GetAttribute("aeco:id").Get() == guid_to_uuid(level_guid)
    assert level.GetAttribute("aeco:elevation").Get() == elevation
    assert list(session.stage.GetMetadata("fallbackPrimTypes")["AecoLevel"]) == ["Xform"]
    for row in receipt["touched"]:
        camera = session.stage.GetPrimAtPath(row["path"])
        assert camera.GetParent() == level
        assert Gf.IsClose(UsdGeom.XformCache().GetLocalToWorldTransform(camera), row["matrix"], 1e-9)
    author(session, receipt, "C-create")
    native = RevitHost(session, client=object(), expected_path="cctv.rvt")
    request = native.request(collect(session.stage, session.intent, session.current()), rollback_only=True)
    created, = request["edits"]
    assert created["operation"] == "create"
    assert str(Sdf.Path(created["path"]).GetParentPath()) == level_path
    assert request["bindings"][level_path]["ref"] == "native-level"
    assert Gf.IsClose(Gf.Matrix4d(request["bindings"][level_path]["matrix"]), raw["touched"][0]["levelBinding"]["matrix"], 1e-9)
