"""IFC export read-back and geometric convergence, without vertex correspondence.

The export reader specializes the IFC host's read-back for Revit's millimetre
IFC4 exports, including solid circles without material profile sets. It does not
regenerate those exports or infer a nominal diameter from an outside diameter
unless an explicit catalog provides a unique match.
"""

import numpy as np
from pathlib import Path
import ifcopenshell.geom
import ifcopenshell.util.element as element_util
import ifcopenshell.util.placement as placement_util
import ifcopenshell.util.representation as representation_util
import ifcopenshell.util.shape as shape_util
import ifcopenshell.util.system as system_util
import ifcopenshell.util.unit as unit_util
from pxr import Gf

from usdaeco_ifc.host import IfcHost
from usdaeco_ifc._ifc_utils import prim_for, index_ids
from aeco_sync.closure import JOIN_NAMES
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.identity import guid_to_uuid
from aeco_sync.readback import publish
from aeco_sync.stack import PREFIX, digest


class ExportIfcHost(IfcHost):
    """The IFC host's read-only route for native exports; no authoring fallback."""

    def __init__(self, session, document):
        # The WP4 authoring host deliberately requires metres. This read-only
        # export specialization accepts the file's declared units without
        # changing that authoring contract or normalizing its source IFC.
        self.session = session
        self.document = Path(document).resolve()
        self.f = ifcopenshell.open(str(self.document))
        self.current = session.current()
        self.idx = index_ids(self.current)
        self._diagnostics = Diagnostics()
        self._version = digest(self.document)

    def validate(self):
        return list(self._diagnostics.items)

    def supports(self, edit):
        return False

    def apply(self, edits):
        raise ValueError(
            "Use the IFC authoring host for edits; export convergence is read-only"
        )

    def readback(self, touched):
        scale = unit_util.calculate_unit_scale(self.f)
        records, meshes = [], {}
        paths = {
            e.GlobalId: str(p.GetPath())
            for e in self.f.by_type("IfcRoot")
            if (p := prim_for(self.idx, e))
        }
        for reference in touched:
            e = self.f.by_guid(reference)
            prim = prim_for(self.idx, e)
            if prim is None or not e.ObjectPlacement or not e.Representation:
                continue
            matrix = placement_util.get_local_placement(e.ObjectPlacement).copy()
            matrix[:3, 3] *= scale
            kind = (
                "pipe"
                if e.is_a("IfcPipeSegment")
                else (
                    "wall"
                    if e.is_a("IfcWall")
                    else "fitting" if e.is_a("IfcPipeFitting") else "element"
                )
            )
            row = {
                "ref": reference,
                "path": str(prim.GetPath()),
                "localRef": "#" + str(e.id()),
                "id": guid_to_uuid(reference),
                "kind": kind,
                "matrix": Gf.Matrix4d(*matrix.T.flatten().tolist()),
                "drivers": {},
                "derived": {},
                "ports": [],
                "joins": {},
                "generated": [],
            }
            extrusions = shape_util.get_base_extrusions(e) or []
            extrusion = extrusions[0] if len(extrusions) == 1 else None
            if kind == "pipe" and extrusion:
                position = (
                    placement_util.get_axis2placement(extrusion.Position)
                    if extrusion.Position
                    else np.eye(4)
                )
                start = position[:3, 3] * scale
                direction = position[:3, :3] @ np.array(
                    extrusion.ExtrudedDirection.DirectionRatios
                )
                end = start + direction * float(extrusion.Depth) * scale
                row["drivers"].update(
                    {
                        "aeco:axis:start": Gf.Vec3d(*start),
                        "aeco:axis:end": Gf.Vec3d(*end),
                    }
                )
                row["derived"]["aeco:axis:length"] = float(np.linalg.norm(end - start))
                profile = extrusion.SweptArea
                if profile.is_a("IfcCircleProfileDef"):
                    outer = 2 * float(profile.Radius) * scale
                    row["derived"]["aeco:pipe:outerDiameter"] = outer
                    if profile.is_a("IfcCircleHollowProfileDef"):
                        row["derived"]["aeco:pipe:innerDiameter"] = (
                            outer - 2 * float(profile.WallThickness) * scale
                        )
                    nominal = (
                        element_util.get_psets(e)
                        .get("Pset_PipeSegmentTypeCommon", {})
                        .get("NominalDiameter")
                    )
                    if nominal is not None:
                        row["drivers"]["aeco:pipe:nominalDiameter"] = (
                            float(nominal) * scale
                        )
                    else:
                        table = (
                            prim.GetAttribute("aeco:pipeType:nominalDiameters").Get()
                            or []
                        )
                        outs = (
                            prim.GetAttribute("aeco:pipeType:outerDiameters").Get()
                            or []
                        )
                        matches = [
                            float(n)
                            for n, od in zip(table, outs)
                            if abs(float(od) - outer) < 1e-6
                        ]
                        if len(matches) == 1:
                            row["drivers"]["aeco:pipe:nominalDiameter"] = matches[0]
                            row["nominalSource"] = "uniqueCatalogOuterDiameter"
            elif kind == "wall":
                # Native IFC4 exports put Axis in a Model context; the authoring
                # host uses Plan. Read the representation itself, never a 1 m fallback.
                axis = next(
                    (
                        r
                        for r in e.Representation.Representations
                        if r.RepresentationIdentifier == "Axis"
                    ),
                    None,
                )
                if axis:
                    for curve in representation_util.resolve_representation(axis).Items:
                        points = (
                            [p.Coordinates for p in curve.Points]
                            if curve.is_a("IfcPolyline")
                            else (
                                curve.Points.CoordList
                                if curve.is_a("IfcIndexedPolyCurve")
                                else []
                            )
                        )
                        if len(points) == 2:
                            start, end = [
                                np.array(list(p) + [0.0] * (3 - len(p))) * scale
                                for p in points
                            ]
                            row["drivers"].update(
                                {
                                    "aeco:axis:start": Gf.Vec3d(*start),
                                    "aeco:axis:end": Gf.Vec3d(*end),
                                }
                            )
                            row["derived"]["aeco:axis:length"] = float(
                                np.linalg.norm(end - start)
                            )
                            break
                if extrusion:
                    row["drivers"]["aeco:wall:height"] = float(extrusion.Depth) * scale
                layers = element_util.get_material(e, should_skip_usage=True)
                if layers and layers.is_a("IfcMaterialLayerSet"):
                    row["derived"]["aeco:wall:thickness"] = (
                        sum(float(l.LayerThickness) for l in layers.MaterialLayers)
                        * scale
                    )
                row["joins"] = {name: [] for name in JOIN_NAMES}
                for relation in list(e.ConnectedTo) + list(e.ConnectedFrom):
                    if not relation.is_a("IfcRelConnectsPathElements"):
                        continue
                    first = relation.RelatingElement == e
                    other = (
                        relation.RelatedElement if first else relation.RelatingElement
                    )
                    end = (
                        relation.RelatingConnectionType
                        if first
                        else relation.RelatedConnectionType
                    )
                    name = {
                        "ATSTART": JOIN_NAMES[0],
                        "ATEND": JOIN_NAMES[1],
                        "ATPATH": JOIN_NAMES[2],
                    }.get(end)
                    if name and other.GlobalId in paths:
                        row["joins"][name].append(paths[other.GlobalId])
            for port in system_util.get_ports(e):
                port_path = paths.get(port.GlobalId)
                if not port_path:
                    continue
                pm = placement_util.get_local_placement(port.ObjectPlacement).copy()
                pm[:3, 3] *= scale
                peer = system_util.get_connected_port(port)
                local = np.linalg.inv(matrix) @ pm
                row["ports"].append(
                    {
                        "path": port_path,
                        "ref": port.GlobalId,
                        "localRef": "#" + str(port.id()),
                        "id": guid_to_uuid(port.GlobalId),
                        "matrix": Gf.Matrix4d(*local.T.flatten().tolist()),
                        "connected": (
                            [paths[peer.GlobalId]]
                            if peer and peer.GlobalId in paths
                            else []
                        ),
                        "diameter": row["drivers"].get(
                            "aeco:pipe:nominalDiameter", 0.0
                        ),
                    }
                )
            settings = ifcopenshell.geom.settings()
            settings.set("weld-vertices", True)
            settings.set("unify-shapes", True)
            try:
                shape = ifcopenshell.geom.create_shape(settings, e)
                meshes[reference] = {
                    "verts": list(shape.geometry.verts),
                    "faces": list(shape.geometry.faces),
                }
            except Exception as exc:
                self._diagnostics.add(
                    "error",
                    "ifcopenshell:geometry",
                    str(exc),
                    [prim.GetPath()],
                    True,
                    "readback",
                    [reference],
                )
                raise
            records.append(row)
        return {
            "touched": records,
            "meshes": meshes,
            "diagnostics": self._diagnostics.items,
            "version": self.version(),
            "stamp": "ifcopenshell export read-back " + ifcopenshell.version,
        }


from aeco_sync.convergence import metrics, world_axis, compare_receipts


def export_port_agreement(native, f):
    """Match each owner/connector to its actual exported port, then check the recipe."""
    from .identity import Binding, port_identity

    exported = {}
    for e in f.by_type("IfcDistributionElement"):
        for port in system_util.get_ports(e):
            connector = (port.Name or "").rsplit("_", 1)[-1]
            if connector.isdigit():
                exported[(e.GlobalId, int(connector))] = port.GlobalId
    matched, total, mismatches = 0, 0, []
    resolved = {}
    for row in native["touched"]:
        if not row.get("ports"):
            continue
        owner = Binding(
            **{
                k: row.get(k, "")
                for k in ("ref", "localRef", "version", "document", "ifcGuid")
            }
        )
        for port in row["ports"]:
            total += 1
            guid = exported.get((row["ifcGuid"], port["connectorId"]))
            peer = port.get("refs", [{}])[0] if port.get("refs") else {}
            if guid:
                try:
                    identity = port_identity(
                        owner,
                        port["connectorId"],
                        peer_guid=peer.get("ifcGuid"),
                        peer_connector=peer.get("connectorId"),
                        exported_guid=guid,
                    )
                    resolved[port["ref"]] = identity
                    matched += 1
                    continue
                except ValueError:
                    pass
            mismatches.append(port["ref"])
    return {
        "matched": matched,
        "total": total,
        "mismatches": mismatches,
        "resolved": resolved,
    }


def publish_convergence(session, native, ifc_path):
    reader = ExportIfcHost(session, ifc_path)
    refs = [e.GlobalId for e in reader.f.by_type("IfcElement")]
    receipt = reader.readback(refs)
    result, diagnostics_layer = session.ensure_host("ifc")
    publish(session, receipt, result, "ifc", receipt["version"], ifc_path)
    report = compare_receipts(native, receipt)
    diag = Diagnostics()
    diag.items = receipt["diagnostics"] + report["diagnostics"]
    diag.write(session, diagnostics_layer, "ifc", receipt["version"], ifc_path)
    result.customLayerData = {
        **result.customLayerData,
        PREFIX + "comparisonRoute": "revit-export",
    }
    result.Save()
    diagnostics_layer.Save()
    session.layer("derived.usda").Save()
    session.ensure_host("revit")
    return report
