from aeco_sync.hosts.base import Operations
from usdaeco_revit.runtime import python as run_python
import pytest
from pxr import Gf, Usd, UsdGeom
from usdaeco_revit.identity import Binding, camera_creation_context
from usdaeco_revit.host import RevitHost
from usdaeco_revit.transport import script_pack
from aeco_sync.identity import guid_to_uuid
from aeco_sync.edits import collect
from aeco_sync.readback import bind
from usdaeco_revit.gates.cctv import fixture, OPTICS, POSE


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("elevation", [0., 4.])
def test_creation_resolves_level_through_spatial_ancestry(tmp_path, depth, elevation):
    session = fixture(tmp_path)
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        level = session.stage.GetPrimAtPath("/Model/Level")
        UsdGeom.Xformable(level).MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 0, elevation)))
        bind(level, "revit", "level-native", "20", "v1", "project-id")
        parent = level
        for i in range(depth):
            parent = session.stage.DefinePrim(parent.GetPath().AppendChild(f"Space_{i}"), "AecoSpace")
            UsdGeom.Xformable(parent).MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(1, 2, .5)))
    session.capture_base([])
    with Usd.EditContext(session.stage, session.intent):
        camera = UsdGeom.Xform.Define(session.stage, parent.GetPath().AppendChild("NewCamera")).GetPrim()
        camera.ApplyAPI("AecoElementAPI")
        camera.ApplyAPI("AecoCctvCameraAPI")
        camera.GetInherits().SetInherits(["/_Types/Dome"])
    host = RevitHost(session, client=object(), expected_path="fixture.rvt")
    request = host.request(collect(session.stage, session.intent, session.current()))
    data = request["edits"][0]["value"]
    assert data["levelPath"] == "/Model/Level"
    assert request["bindings"][data["levelPath"]]["ref"] == "level-native"
    assert Gf.Matrix4d(data["parentMatrix"]).ExtractTranslation() == Gf.Vec3d(depth, depth * 2, elevation + .5 * depth)
    assert data["mark"] == "NewCamera"


@pytest.mark.parametrize("mark", ["sec.cam.door.1", "different spec label", "000000000010200000000B"])
def test_ifc_guid_is_identity_and_mark_remains_label(mark):
    record = dict(ref="native-camera", localRef="10", version="v1", document="project-id",
                  ifcGuid="1iQWvF2Ib4PwCs9WY7WYHZ", mark=mark)
    binding = Binding.from_record(record)
    assert binding.id == guid_to_uuid(record["ifcGuid"])
    assert "mark" not in binding.wire()


def test_mark_without_ifc_guid_uses_native_identity():
    record = dict(ref="native-camera", document="project-id", mark="1iQWvF2Ib4PwCs9WY7WYHZ")
    assert Binding.from_record(record).id != guid_to_uuid(record["mark"])


def test_creation_without_level_has_named_refusal():
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim("/Site/Space", "AecoSpace")
    with pytest.raises(ValueError, match="no AecoLevel ancestor"):
        camera_creation_context(stage, "/Site/Space/Camera")


def test_script_uses_explicit_level_and_independent_identity_parameters():
    source = script_pack({}, "offline-identity")
    assert 'Lookup(c, levelPath) as Level' in source
    assert 'FromMatrix(data["parentMatrix"]).Multiply(matrix)' in source
    assert 'mark.Set(S(data["mark"]))' in source
    assert 'id.Set(IfcCompress(Guid.Parse(S(data["attributes"]["aeco:id"]))))' in source
