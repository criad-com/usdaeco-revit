"""Synthetic camera fixture and the C4 IFC acceptance cases (no vendor assets)."""
import json
import math
import copy
from pathlib import Path
import numpy as np
import ifcopenshell
from ifcopenshell.api import run
from pxr import Gf, Sdf, Usd, UsdGeom, Vt
from aeco_sync import engine
from aeco_sync.cctv import SENSOR, PRESET, set_values
from usdaeco_ifc import _ifc_cctv as native
from aeco_sync.identity import guid_to_uuid
from aeco_sync.readback import bind
from aeco_sync.stack import create, digest, value

CAMERA = "/Model/Level/Camera"
SENSOR_PATH = CAMERA + "/Sensor_0"
OPTICS = {SENSOR + "focalRange": [3., 8.5], SENSOR + "hfovRange": [104., 34.],
          SENSOR + "vfovRange": [76., 26.], SENSOR + "pixels": [2592, 1944],
          SENSOR + "panRange": [-180., 180.], SENSOR + "tiltRange": [0., 90.],
          SENSOR + "motorised": True, SENSOR + "offset": [0.133, 0., 0.035]}
POSE = {SENSOR + "pan": 0., SENSOR + "tilt": 25., SENSOR + "roll": 0.,
        SENSOR + "focalLength": 3., SENSOR + "range": 15., SENSOR + "targetDensity": 125.}


def fixture(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    f = ifcopenshell.file(schema="IFC4X3")
    project = run("root.create_entity", f, ifc_class="IfcProject", name="Camera sync fixture")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    f.by_type("IfcUnitAssignment")[0].Units += (run("unit.add_si_unit", f, unit_type="PLANEANGLEUNIT"),)
    model = run("context.add_context", f, context_type="Model")
    body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    level = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Lobby")
    for parent, child in ((project, site), (site, building), (building, level)):
        run("aggregate.assign_object", f, products=[child], relating_object=parent)
    run("geometry.edit_object_placement", f, product=level, matrix=np.eye(4))
    types = []
    for name, pixels in (("Dome", [2592, 1944]), ("Alternate", [3840, 2160])):
        typ = run("root.create_entity", f, ifc_class="IfcAudioVisualApplianceType", predefined_type="CAMERA", name=name)
        native.store(f, typ, "Type", {"aeco:cctvType:outdoor": False, "aeco:cctvType:irRange": 0.})
        native.store(f, typ, "Sensors", [dict(name="Sensor_0", drivers={**OPTICS, SENSOR + "pixels": pixels})])
        rep = run("geometry.add_wall_representation", f, context=body, length=.2, height=.1, thickness=.15)
        style = run("style.add_style", f, name="Camera body")
        run("style.add_surface_style", f, style=style, ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": .3, "Green": .3, "Blue": .3}})
        run("style.assign_representation_styles", f, shape_representation=rep, styles=[style])
        run("geometry.assign_representation", f, product=typ, representation=rep)
        types.append(typ)
    e = run("root.create_entity", f, ifc_class="IfcAudioVisualAppliance", predefined_type="CAMERA", name="Camera")
    run("type.assign_type", f, related_objects=[e], relating_type=types[0])
    run("spatial.assign_container", f, products=[e], relating_structure=level)
    matrix = np.eye(4)
    matrix[:3, 3] = [1., 2., 2.9]
    run("geometry.edit_object_placement", f, product=e, matrix=matrix)
    native.persist_sensors(f, e, [dict(name="Sensor_0", drivers=POSE, presets={"Preset_1": dict(pan=0., tilt=25., focalLength=3., dwell=6., home=True)})])
    native.store(f, e, "Drivers", {"aeco:cctv:scenario": "DOOR", "aeco:cctv:mount": "ceiling"})
    file = directory / "baseline.ifc"
    f.write(str(file))
    stage = Usd.Stage.CreateNew(str(directory / "model.usda"))
    root = UsdGeom.Xform.Define(stage, "/Model").GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    stage.SetMetadata("fallbackPrimTypes", {"AecoLevel": Vt.TokenArray(["Xform"])})
    lp = stage.DefinePrim("/Model/Level", "AecoLevel")
    lp.GetAttribute("aeco:id").Set(guid_to_uuid(level.GlobalId))
    bind(lp, "ifc", level.GlobalId, "", digest(file), file)
    for typ in types:
        tp = stage.CreateClassPrim("/_Types/" + typ.Name)
        tp.ApplyAPI("AecoTypeAPI")
        tp.ApplyAPI("AecoCctvCameraTypeAPI")
        bind(tp, "ifc", typ.GlobalId, "", digest(file), file)
        sp = UsdGeom.Camera.Define(stage, tp.GetPath().AppendChild("Sensor_0")).GetPrim()
        sp.ApplyAPI("AecoCctvSensorAPI")
        set_values(sp, native.type_data(typ)[0]["drivers"])
        bind(sp, "ifc", typ.GlobalId, "", digest(file), file)
    cp = UsdGeom.Xform.Define(stage, CAMERA).GetPrim()
    cp.ApplyAPI("AecoElementAPI")
    cp.ApplyAPI("AecoCctvCameraAPI")
    cp.GetAttribute("aeco:id").Set(guid_to_uuid(e.GlobalId))
    cp.GetInherits().SetInherits(["/_Types/Dome"])
    bind(cp, "ifc", e.GlobalId, "", digest(file), file)
    stage.GetRootLayer().Save()
    session = create(directory / "model.usda", file, directory / "sync")
    engine.readback(session, "ifc", file)
    return session


def author(session, case):
    cp = session.stage.GetPrimAtPath(CAMERA)
    sp = session.stage.GetPrimAtPath(SENSOR_PATH)
    session.capture_base()
    with Usd.EditContext(session.stage, session.intent):
        if case == "C-move":
            matrix = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 30.))
            matrix.SetTranslateOnly(Gf.Vec3d(1.5, 2., 3.1))
            UsdGeom.Xformable(cp).MakeMatrixXform().Set(matrix)
        elif case == "C-pan-tilt":
            sp.GetAttribute(SENSOR + "pan").Set(35.)
            sp.GetAttribute(SENSOR + "tilt").Set(40.)
        elif case == "C-zoom-clamped":
            sp.GetAttribute(SENSOR + "focalLength").Set(99.)
        elif case == "C-preset":
            sp.GetAttribute(PRESET + "Preset_1:pan").Set(65.)
            sp.GetAttribute(PRESET + "Preset_1:tilt").Set(30.)
            sp.GetAttribute(PRESET + "Preset_1:focalLength").Set(5.)
        elif case == "C-type-swap":
            cp.GetInherits().SetInherits(["/_Types/Alternate"])
        elif case == "C-create":
            new = UsdGeom.Xform.Define(session.stage, "/Model/Level/NewCamera").GetPrim()
            new.ApplyAPI("AecoElementAPI")
            new.ApplyAPI("AecoCctvCameraAPI")
            new.GetInherits().SetInherits(["/_Types/Dome"])
            matrix = Gf.Matrix4d().SetTranslate(Gf.Vec3d(4., 2., 2.9))
            UsdGeom.Xformable(new).MakeMatrixXform().Set(matrix)
            set_values(new.GetChild("Sensor_0"), POSE)
        elif case == "C-delete":
            cp.SetActive(False)
        elif case == "C-derived-authored":
            sp.GetAttribute("focalLength").Set(99.)
        elif case == "X-converge-cctv":
            sp.GetAttribute(SENSOR + "focalLength").Set(5.)
        else:
            raise ValueError(case)
    session.intent.Save()


def run_case(directory, case, host="ifc"):
    session = fixture(directory)
    if host != "ifc":
        engine.readback(session, host, session.document("ifc"))
    baseline_hash = digest(session.document(host))
    author(session, case)
    result = engine.apply(session, host)
    codes = sorted({d["code"] for d in result["diagnostics"]})
    refusal = {"C-zoom-clamped": "cctvOutOfEnvelope", "C-derived-authored": "sync:derivedAuthored"}.get(case)
    if refusal:
        assert codes == [refusal] and result["accepted"] == 0 and result["pending"] == 1, result
        assert digest(session.document(host)) == baseline_hash
    else:
        assert result["accepted"] > 0 and not result["pending"] and not codes, result
    current = session.current()
    path = "/Model/Level/NewCamera" if case == "C-create" else CAMERA
    cp = current.GetPrimAtPath(path)
    sp = cp.GetChild("Sensor_0")
    f = ifcopenshell.open(session.document(host))
    camera = f.by_guid(cp.GetAttribute(f"aeco:host:{host}:ref").Get()) if cp.IsActive() else None
    if case == "C-move":
        from ifcopenshell.util.placement import get_local_placement
        m = get_local_placement(camera.ObjectPlacement)
        assert np.allclose(m[:3, 3], [1.5, 2., 3.1], atol=1e-6)
        assert abs(math.atan2(m[1, 0], m[0, 0]) - math.pi / 6) < 1e-6
    if case == "C-pan-tilt":
        assert sp.GetAttribute(SENSOR + "pan").Get() == 35.
        assert sp.GetAttribute(SENSOR + "tilt").Get() == 40.
        assert native.uel.get_psets(camera)[native.STANDARD]["TiltHorizontal"] == -math.radians(40.)
    if case == "C-preset":
        assert sp.GetAttribute(PRESET + "Preset_1:pan").Get() == 65.
        standard = native.pset(f, camera, native.STANDARD)
        table = next(p for p in standard.HasProperties if p.Name == "PanTiltZoomPreset")
        assert json.loads(table.DefinedValues[0].wrappedValue)["tilt"] == -30.
    if case == "C-type-swap":
        assert list(sp.GetAttribute(SENSOR + "pixels").Get()) == [3840, 2160]
        assert native.uel.get_type(camera).Name == "Alternate"
    if case == "C-create":
        assert len(f.by_type("IfcAudioVisualAppliance")) == 2
        assert sp.GetAttribute(SENSOR + "tilt").Get() == 25.
        assert cp.GetAttribute("aeco:id").Get() == guid_to_uuid(camera.GlobalId)
        assert camera.Representation
    if case == "C-delete":
        assert not cp.IsActive() and not f.by_type("IfcAudioVisualAppliance")
    if case == "X-converge-cctv":
        assert sp.GetAttribute("focalLength").Get() == 5.
        assert sp.GetAttribute(SENSOR + "hfov").Get() > 34.
        from usdaeco_ifc.host import IfcHost
        from aeco_sync.convergence import compare_receipts
        receipt = IfcHost(session, session.document(host)).readback([camera.GlobalId])
        observed = copy.deepcopy(receipt)
        sensor = observed["touched"][0]["sensors"][0]
        # Independent native-formula fixture, interpolated at 5 mm. The actual
        # Revit values are supplied by the live script in cctv_revit.py.
        angles = []
        for wide, tele in ((104.,34.), (76.,26.)):
            width = 6 * math.tan(math.radians(wide)/2)
            width += (17 * math.tan(math.radians(tele)/2) - width) * (5-3)/(8.5-3)
            angles.append(math.atan(width / 10))
        p = dict(zip(("Horizontal Angle", "Vertical Angle"), angles))
        p.update({"Focal Length": 3., "Tilt": 25.})
        # IFC's preset remains at 3 mm; this fixture compares the current pose.
        sensor["presets"] = {}
        observed["touched"][0]["sensors"][0]["fov"] = [dict(subId="fixture", parameters=p)]
        receipt["touched"][0]["sensors"][0]["presets"] = {}
        for suffix, density in zip(("Det", "Obs", "Rec", "Id", "UD"), (26,62,125,250,125)):
            p["T_Res_" + suffix] = density
            p["RG_Length_" + suffix] = min(15., 2592/(2 * angles[0] * density))
        comparison = compare_receipts(observed, receipt)
        assert comparison["cctvConverged"] and len(comparison["cameras"][0]["comparisons"]) == 7
    if cp.IsActive():
        assert cp.GetChild("Geom").HasAPI("AecoDerivedGeometryAPI")
        assert sp.GetChild("Sector").HasAPI("AecoDerivedGeometryAPI")
    repeat = engine.apply(session, host) if not refusal else None
    if repeat:
        assert repeat["mutations"] == 0 and repeat["inSync"], repeat
    row = dict(id=case, host=host, mutations=result["mutations"], repeatMutations=repeat["mutations"] if repeat else None, nativeEvidence=result["nativeEvidence"], passed=True, codes=codes, pending=result["pending"], accepted=result["accepted"])
    row["nativeState"] = native_state(camera)
    if case == "X-converge-cctv":
        row["convergence"] = comparison["cameras"][0]
        row["convergenceEvidence"] = "independent formula fixture; live observations require Revit"
    return row


def native_state(camera):
    if camera is None:
        return None
    from ifcopenshell.util.placement import get_local_placement
    from ifcopenshell.util.unit import calculate_unit_scale
    matrix = get_local_placement(camera.ObjectPlacement)
    matrix[:3, 3] *= calculate_unit_scale(camera.file)
    typ = native.uel.get_type(camera)
    return dict(matrix=matrix.tolist(), drivers=native.load(camera, "Drivers", {}),
                sensors=native.load(camera, "Sensors", []), type=typ.Name)
