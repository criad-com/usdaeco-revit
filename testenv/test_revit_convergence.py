from aeco_sync.hosts.base import Operations
from usdaeco_revit.runtime import python as run_python
"""Independent geometry/IFC evidence for export convergence, without a live host."""

import copy
import numpy as np
import pytest
import ifcopenshell
from ifcopenshell.api import run
from pxr import Gf, Usd

from aeco_sync.identity import uuid_to_guid
from usdaeco_revit.convergence import (
    ExportIfcHost,
    compare_receipts,
    metrics,
    world_axis,
)


def cube():
    return {
        "verts": [
            0,
            0,
            0,
            1,
            0,
            0,
            1,
            1,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            1,
            1,
            1,
            0,
            1,
            1,
        ],
        "faces": [
            0,
            2,
            1,
            0,
            3,
            2,
            4,
            5,
            6,
            4,
            6,
            7,
            0,
            1,
            5,
            0,
            5,
            4,
            1,
            2,
            6,
            1,
            6,
            5,
            2,
            3,
            7,
            2,
            7,
            6,
            3,
            0,
            4,
            3,
            4,
            7,
        ],
    }


def sample():
    return {
        "touched": [
            {
                "ref": "a",
                "id": "same-id",
                "path": "/Pipe",
                "kind": "pipe",
                "matrix": np.eye(4).tolist(),
                "drivers": {
                    "aeco:axis:start": [0, 0, 0],
                    "aeco:axis:end": [0, 0, 1],
                    "aeco:pipe:nominalDiameter": 0.05,
                },
            }
        ],
        "meshes": {"a": cube()},
    }


def test_volume_and_bounds_are_stable_under_translation_and_reflection():
    transform = np.eye(4)
    transform[3, :3] = [1e7, 2e7, -3e7]
    transform[0, 0] = -1
    result = metrics(cube(), transform)
    assert result["volume"] == pytest.approx(1)
    assert result["min"] == [1e7 - 1, 2e7, -3e7]
    assert result["max"] == [1e7, 2e7 + 1, -3e7 + 1]


def test_convergence_uses_world_axes_and_ignores_triangle_order():
    a = sample()
    b = copy.deepcopy(a)
    b["touched"][0]["matrix"][3][0] = 4
    for end in ("start", "end"):
        b["touched"][0]["drivers"]["aeco:axis:" + end][0] -= 4
    b["meshes"]["a"]["verts"] = [
        n - 4 if i % 3 == 0 else n for i, n in enumerate(b["meshes"]["a"]["verts"])
    ]
    faces = np.array(b["meshes"]["a"]["faces"]).reshape(-1, 3)
    b["meshes"]["a"]["faces"] = faces[::-1].flatten().tolist()
    report = compare_receipts(a, b)
    assert report["driversConverged"] and report["driversCompared"] == 7
    assert report["maxDriverError"] == 0 and not report["diagnostics"]
    assert report["bodies"][0]["extentError"] == 0
    assert report["bodies"][0]["volumeError"] == 0


def test_body_divergence_is_information_and_driver_difference_is_measured():
    a, b = sample(), sample()
    b["touched"][0]["drivers"]["aeco:pipe:nominalDiameter"] = 0.065
    b["meshes"]["a"]["verts"][0] -= 0.02
    report = compare_receipts(a, b)
    assert not report["driversConverged"]
    assert report["maxDriverError"] == pytest.approx(0.015)
    assert report["bodies"][0]["extentError"] == pytest.approx(0.02)
    assert report["diagnostics"][0]["code"] == "bodyDivergence"
    assert report["diagnostics"][0]["severity"] == "info"
    assert not report["diagnostics"][0]["blocking"]


def test_missing_axis_or_element_cannot_pass_convergence():
    a, b = sample(), sample()
    del b["touched"][0]["drivers"]["aeco:axis:end"]
    report = compare_receipts(a, b)
    assert report["missingDrivers"] and not report["driversConverged"]
    b["touched"] = []
    report = compare_receipts(a, b)
    assert report["missingElements"] == ["same-id"] and not report["driversConverged"]


def solid_circle_export(session, tmp_path):
    f = ifcopenshell.file(schema="IFC4")
    run("root.create_entity", f, ifc_class="IfcProject", name="Export fixture")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS"})
    context = run("context.add_context", f, context_type="Model")
    body = run(
        "context.add_context",
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=context,
    )
    pipe = run("root.create_entity", f, ifc_class="IfcPipeSegment")
    pipe.GlobalId = uuid_to_guid(
        session.stage.GetPrimAtPath("/Pipe").GetAttribute("aeco:id").Get()
    )
    profile = f.create_entity("IfcCircleProfileDef", ProfileType="AREA", Radius=25.0)
    # Exercise the extrusion's position as well as the product's placement.
    position = f.createIfcAxis2Placement3D(
        f.createIfcCartesianPoint((100.0, 0.0, 0.0)), None, None
    )
    solid = f.createIfcExtrudedAreaSolid(
        profile, position, f.createIfcDirection((0.0, 0.0, 1.0)), 2000.0
    )
    rep = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])
    run("geometry.assign_representation", f, product=pipe, representation=rep)
    matrix = np.eye(4)
    matrix[:3, 3] = [2.0, 3.0, 4.0]
    run("geometry.edit_object_placement", f, product=pipe, matrix=matrix, is_si=True)
    path = tmp_path / "solid-circle.ifc"
    f.write(str(path))
    return path, pipe.GlobalId


def test_ifc_host_reads_millimetre_solid_circle_without_material_profiles(
    session, tmp_path
):
    path, guid = solid_circle_export(session, tmp_path)
    reader = ExportIfcHost(session, path)
    result = reader.readback([guid])
    row = result["touched"][0]
    assert row["derived"]["aeco:pipe:outerDiameter"] == pytest.approx(0.05)
    assert row["drivers"]["aeco:pipe:nominalDiameter"] == pytest.approx(0.05)
    assert row["nominalSource"] == "uniqueCatalogOuterDiameter"
    assert np.allclose(world_axis(row), [[2.1, 3.0, 4.0], [2.1, 3.0, 6.0]])
    assert metrics(result["meshes"][guid], row["matrix"])["volume"] == pytest.approx(
        np.pi * 0.025**2 * 2, rel=0.015
    )
    with pytest.raises(ValueError, match="read-only"):
        reader.apply(Operations([], None))


def test_missing_nominal_is_not_fabricated_from_outer_diameter(session, tmp_path):
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        prim = session.stage.GetPrimAtPath("/Pipe")
        prim.GetAttribute("aeco:pipeType:outerDiameters").Set([0.055, 0.07])
    path, guid = solid_circle_export(session, tmp_path)
    result = ExportIfcHost(session, path).readback([guid])
    assert "aeco:pipe:nominalDiameter" not in result["touched"][0]["drivers"]


def test_exported_wall_axis_in_model_context_is_not_a_unit_length_fallback(
    session, tmp_path
):
    path, guid = solid_circle_export(session, tmp_path)
    f = ifcopenshell.open(str(path))
    pipe = f.by_guid(guid)
    wall = run("root.create_entity", f, ifc_class="IfcWall")
    wall.GlobalId = guid
    wall.ObjectPlacement, wall.Representation = (
        pipe.ObjectPlacement,
        pipe.Representation,
    )
    f.remove(pipe)  # Keep the representation transferred to this synthetic wall.
    context = f.by_type("IfcGeometricRepresentationContext", include_subtypes=False)[0]
    axis_context = run(
        "context.add_context",
        f,
        context_type="Model",
        context_identifier="Axis",
        target_view="GRAPH_VIEW",
        parent=context,
    )
    line = f.createIfcPolyline(
        [
            f.createIfcCartesianPoint((0.0, 0.0)),
            f.createIfcCartesianPoint((8000.0, 0.0)),
        ]
    )
    axis = f.createIfcShapeRepresentation(axis_context, "Axis", "Curve2D", [line])
    run("geometry.assign_representation", f, product=wall, representation=axis)
    f.write(str(path))
    row = ExportIfcHost(session, path).readback([guid])["touched"][0]
    assert row["derived"]["aeco:axis:length"] == pytest.approx(8)
    assert np.allclose(world_axis(row), [[2.0, 3.0, 4.0], [10.0, 3.0, 4.0]])
