"""Host interface over one guarded native request and a cached receipt."""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from pxr import Gf, Sdf, UsdGeom
from aeco_sync.hosts.base import Host, MutationReceipt
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.identity import guid_to_uuid, uuid_to_guid
from aeco_sync.stack import PREFIX, find_layer, value
from .transport import ReplClient
from .identity import Binding, port_identity, camera_creation_context


class HostRefused(RuntimeError):
    pass


class RevitHost(Host):
    name = "revit"
    file_backed = False

    def __init__(self, session=None, document=None, *, client=None, expected_path=None,
                 background=None, cameras_only=None, validate_all=False, ids=None):
        self._options = (document, client, expected_path, background, cameras_only)
        self.receipt = None
        if session is not None:
            self.open(session)

    @classmethod
    def initialize(cls, model, document, directory=None, policy="keepConnected", *, kind_import=False):
        session = super().initialize(model, document, directory, policy, kind_import=kind_import)
        from pxr import Vt
        fallback = dict(session.stage.GetMetadata("fallbackPrimTypes") or {})
        fallback.update({"AecoPort": Vt.TokenArray(["Xform"]), "AecoLevel": Vt.TokenArray(["Xform"])})
        session.root.pseudoRoot.SetInfo("fallbackPrimTypes", fallback)
        session.root.Save()
        return session

    def capabilities(self):
        return frozenset({"attribute", "relationship", "activation", "primMetadata", "create"})

    def diagnostics(self):
        return list(self._diagnostics.items)

    def close(self):
        self.receipt = None

    def open(self, session):
        document, client, expected_path, background, cameras_only = self._options
        self.session = session
        self.current = session.current()
        self.client = client or ReplClient()
        self.expected_path = (
            expected_path or document or os.environ.get("AECO_REVIT_DOCUMENT", "")
        )
        if not self.expected_path:
            raise ValueError(
                "Set AECO_REVIT_DOCUMENT to the exact native file path"
            )
        self.background = (os.environ.get("AECO_REVIT_BACKGROUND") == "1"
                           or Path(str(self.expected_path).replace("\\", "/")).name.lower() == "cctv.rvt") if background is None else background
        self.cameras_only = os.environ.get("AECO_REVIT_SCOPE") == "cameras" if cameras_only is None else cameras_only
        result = find_layer(session.stage, "result.revit.usda")
        self.document = (
            result.customLayerData.get(PREFIX + "document", "") if result else ""
        ) or os.environ.get("AECO_REVIT_DOCUMENT_GUID", "")
        self._version = session.version("revit")
        self._diagnostics = Diagnostics()
        self.receipt = None
        self.journal = session.path.parent / "receipt.revit.json"
        return self

    def version(self):
        # No host call for preflight, including pipeSizeNotInTable. C# compares
        # this token with a live fingerprint before it starts a transaction.
        return self._version

    def supports(self, edit):
        from aeco_sync.cctv import camera_of, CONTROLS, SENSOR, PRESET, rigid_z
        if edit.operation == "create" and edit.value.get("kind") == "camera":
            data = edit.value
            import re
            return (len(data["inherits"]) == 1 and bool(data["sensors"])
                    and not data["relationships"]
                    and all(name in {SENSOR + field for field in CONTROLS} or re.fullmatch(r"aeco:cctvPreset:Preset_[1-4]:(pan|tilt|focalLength)", name)
                            for sensor in data["sensors"] for name in sensor["drivers"])
                    and set(data["attributes"]) <= {"aeco:id", "aeco:cctv:scenario", "xformOp:transform", "xformOpOrder"}
                    and rigid_z(data["attributes"].get("xformOp:transform", Gf.Matrix4d(1))))
        prim = self.current.GetPrimAtPath(edit.path)
        if not prim:
            return False
        camera = camera_of(prim)
        if camera:
            if edit.operation == "activation":
                return prim == camera and edit.value is False
            if edit.operation == "primMetadata":
                return prim == camera and edit.name == "inheritPaths" and len(edit.value) == 1
            if edit.operation != "attribute":
                return False
            if edit.name == "xformOpOrder":
                return prim == camera and list(edit.value) == ["xformOp:transform"]
            if edit.name == "xformOp:transform":
                mirrored = Gf.Matrix4d(UsdGeom.Xformable(camera).GetLocalTransformation()).GetDeterminant() < 0
                return prim == camera and rigid_z(edit.value, mirrored)
            if edit.name.startswith(SENSOR):
                field = edit.name[len(SENSOR):]
                return field in CONTROLS and (field != "roll" or edit.value in (0, 90))
            if edit.name.startswith(PRESET):
                import re
                return bool(re.fullmatch(r"aeco:cctvPreset:Preset_[1-4]:(pan|tilt|focalLength)", edit.name))
            return edit.name == "aeco:cctv:scenario"
        pipe, wall = prim.HasAPI("AecoPipeAPI"), prim.HasAPI("AecoWallAPI")
        if edit.operation == "primMetadata":
            return wall and edit.name == "inheritPaths" and len(edit.value) == 1
        if edit.operation == "relationship":
            return (
                edit.name == "aeco:connectedPorts"
                or wall
                and edit.name in ("aeco:wall:joinAtStart", "aeco:wall:joinAtEnd")
            )
        if edit.operation != "attribute":
            return False
        if edit.name in ("aeco:axis:start", "aeco:axis:end"):
            return (pipe or wall) and prim.GetAttribute(
                "aeco:axis:curve"
            ).Get() == "line"
        if edit.name == "xformOp:transform":
            return pipe or wall or prim.HasAPI("AecoPipeFittingAPI")
        return (
            pipe
            and edit.name == "aeco:pipe:nominalDiameter"
            or wall
            and edit.name
            in {
                "aeco:wall:flipped",
                "aeco:wall:height",
                "aeco:wall:baseOffset",
                "aeco:wall:topOffset",
                "aeco:wall:locationLine",
                "aeco:wall:allowJoinAtStart",
                "aeco:wall:allowJoinAtEnd",
            }
        )

    def bindings(self):
        bindings = {}
        cache = UsdGeom.XformCache()
        for prim in self.current.TraverseAll():
            from aeco_sync.cctv import is_sensor
            if prim.IsAbstract() and is_sensor(prim):
                # Catalog sensors share their symbol binding as evidence; they
                # cannot replace the symbol's canonical path in the request.
                continue
            reference = prim.GetAttribute("aeco:host:revit:ref")
            if reference and reference.Get():
                b = {
                    key: prim.GetAttribute(f"aeco:host:revit:{key}").Get() or ""
                    for key in ("ref", "localRef", "version", "document")
                }
            else:
                reference = prim.GetAttribute("aeco:host:ifc:ref")
                if (
                    not reference
                    or not reference.Get()
                    or prim.GetTypeName() == "AecoPort"
                ):
                    continue
                b = {
                    "ref": reference.Get(),
                    "ifcGuid": reference.Get(),
                    "document": self.document,
                }
            identity = prim.GetAttribute("aeco:id")
            if identity and identity.Get():
                b.setdefault("ifcGuid", uuid_to_guid(identity.Get()))
            if prim.IsA(UsdGeom.Xformable):
                b["matrix"] = value(cache.GetLocalToWorldTransform(prim))
                b["parentMatrix"] = value(
                    cache.GetLocalToWorldTransform(prim.GetParent())
                )
            bindings[str(prim.GetPath())] = b
        return bindings

    def request(
        self,
        edits=(),
        closure=None,
        *,
        action="sync",
        rollback_only=False,
        export_ifc=False,
    ):
        wire = {
            "action": action,
            "edits": [e.wire() for e in edits],
            "closure": closure.wire() if closure else {"paths": [], "disconnect": []},
            "policy": closure.policy if closure else self.session.policy,
            "bindings": self.bindings(),
            "document": self.document,
            "expectedPath": str(self.expected_path),
            "expectedVersion": self.version() if action == "sync" else "",
            "rollbackOnly": rollback_only,
            "exportIfc": export_ifc,
        }
        for edit in wire["edits"]:
            if edit["operation"] == "create" and edit["value"].get("kind") == "camera":
                edit["value"].update(camera_creation_context(self.session.stage, edit["path"]))
                edit["value"]["mark"] = Sdf.Path(edit["path"]).name
        if self.background:
            wire["backgroundDocument"] = True
        if self.cameras_only:
            wire.update(camerasOnly=True, compactReply=True)
        return wire

    def _journal(self, receipt):
        fd, name = tempfile.mkstemp(prefix=".revit-receipt-", dir=self.journal.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(receipt, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.journal)
        finally:
            if Path(name).exists():
                Path(name).unlink()

    def exchange(self, request):
        raw = self.client.exchange(request)
        self.raw_receipt = raw
        required = {
            "touched",
            "meshes",
            "diagnostics",
            "version",
            "document",
            "status",
            "stamp",
        }
        if not required.issubset(raw):
            raise ValueError("Incomplete Revit receipt")
        if raw["status"] not in ("refused", "snapshot", "committed"):
            raise ValueError("Unknown Revit transaction status")
        if raw["status"] == "committed" and not request["rollbackOnly"]:
            self._journal(
                raw
            )  # preserve native success even if normalization/publish fails
        self._diagnostics.items = [self._diagnostic(d, raw) for d in raw["diagnostics"]]
        if raw["status"] == "refused":
            raise HostRefused(
                "Revit refused the request; native errors and intent are retained"
            )
        if request["rollbackOnly"] and not raw.get("rollbackVerified"):
            raise HostRefused("Scenario failed its rollback fingerprint check")
        if not raw["version"] or not raw["document"]:
            raise ValueError("Revit receipt lacks native document/version tokens")
        self.document = raw["document"]
        self.receipt = self.normalize(raw)
        if not request["rollbackOnly"]:
            self._version = raw["version"]
        return self.receipt

    def apply(self, edits):
        closure = edits.closure
        if self.journal.exists():
            raise HostRefused(
                "A native receipt awaits reconciliation; read back Revit before applying again"
            )
        for edit in edits:
            from aeco_sync.cctv import camera_of, envelope
            camera = camera_of(self.session.stage.GetPrimAtPath(edit.path))
            if camera and edit.operation != "activation":
                problem = envelope(camera)
                if edit.name.endswith(":focalLength"):
                    sensor = self.session.stage.GetPrimAtPath(edit.path)
                    lo, hi = sensor.GetAttribute("aeco:cctvSensor:focalRange").Get()
                    import math
                    if not math.isfinite(edit.value) or edit.value != 0 and not lo <= edit.value <= hi:
                        problem = "Requested focal length is outside the camera type envelope"
                if problem:
                    self._diagnostics.add("error", "cctvOutOfEnvelope", problem, [edit.property_path], True, "preflight")
                    raise HostRefused("Camera envelope refused before any host call")
            if not self.supports(edit):
                raise HostRefused(f"Unsupported Revit operation: {edit.property_path}")
            if edit.name == "aeco:pipe:nominalDiameter":
                table = (
                    self.current.GetPrimAtPath(edit.path)
                    .GetAttribute("aeco:pipeType:nominalDiameters")
                    .Get()
                    or []
                )
                if not any(abs(float(n) - float(edit.value)) < 1e-9 for n in table):
                    self._diagnostics.add(
                        "error",
                        "pipeSizeNotInTable",
                        "Size is not in the published table",
                        [edit.property_path],
                        True,
                        "preflight",
                    )
                    raise HostRefused("Diameter refused before any host call")
        receipt = self.exchange(self.request(edits, closure))
        return MutationReceipt(tuple(item["ref"] for item in receipt["touched"]), self.version())

    def readback(self, touched=None):
        if touched is None:
            return self.exchange(self.request(action="snapshot"))
        if self.receipt is None:
            raise ValueError(
                "No completed native receipt; call readback(None) for a snapshot"
            )
        return self.receipt

    def validate(self):
        # Failures, commit warnings and native gap checks are in the same receipt.
        return []

    def acknowledge(self):
        if self.journal.exists():
            os.replace(self.journal, self.journal.with_name("receipt.revit.last.json"))

    def _diagnostic(self, item, raw):
        row = {
            "severity": "warning",
            "code": "revit:unknown",
            "message": "",
            "phase": "apply",
            "blocking": False,
            "about": [],
            "hostRefs": [],
            **item,
        }
        # Native warnings remain visible evidence, including duplicate Marks.
        # A warning cannot become a transaction veto through a stale flag.
        severity = str(row.get("nativeSeverity", row["severity"])).lower()
        if severity in ("warn", "warning"):
            row.update(severity="warning", blocking=False)
        paths = {}
        for prim in self.current.TraverseAll():
            for key in ("ref", "localRef"):
                attr = prim.GetAttribute(f"aeco:host:revit:{key}")
                if attr and attr.Get():
                    paths[attr.Get()] = str(prim.GetPath())
        for record in raw["touched"]:
            if record.get("path"):
                paths[record["ref"]] = record["path"]
                paths[record.get("localRef", "")] = record["path"]
        row["about"] = sorted(
            set(row["about"] + [paths[r] for r in row["hostRefs"] if r in paths])
        )
        return row

    def normalize(self, raw):
        receipt = copy.deepcopy(raw)
        records = receipt["touched"]
        by_ref, by_id, port_by_id, by_ifc = {}, {}, {}, {}
        for prim in self.current.TraverseAll():
            from aeco_sync.cctv import is_sensor
            if is_sensor(prim):
                # Native type refs also occur on catalog sensors. A second
                # read-back must still bind the class, never Class/Sensor_0.
                continue
            attr = prim.GetAttribute("aeco:id")
            if attr and attr.Get():
                by_id[attr.Get()] = str(prim.GetPath())
                if prim.GetTypeName() == "AecoPort":
                    port_by_id[attr.Get()] = str(prim.GetPath())
            ref = prim.GetAttribute("aeco:host:revit:ref")
            if ref and ref.Get():
                by_ref[ref.Get()] = str(prim.GetPath())
            ifc_ref = prim.GetAttribute("aeco:host:ifc:ref")
            if ifc_ref and ifc_ref.Get():
                by_ifc[ifc_ref.Get()] = str(prim.GetPath())
        root = self.current.GetDefaultPrim()
        root_path = (
            str(root.GetPath())
            if root
            else next(
                (
                    str(p.GetPath())
                    for p in self.current.GetPseudoRoot().GetChildren()
                    if p.IsA(UsdGeom.Xformable)
                ),
                "/Model",
            )
        )
        for item in records:
            if item.get("active") is False:
                item["path"] = item.get("path") or by_ref.get(item["ref"], "")
                continue
            binding = Binding.from_record(item)
            item["id"] = binding.id
            container = root_path
            level = item.get("levelBinding") if item.get("kind") == "camera" else None
            if level:
                level["id"] = guid_to_uuid(level["ifcGuid"])
                level["path"] = (
                    by_ref.get(level["ref"])
                    or by_id.get(level["id"])
                    or root_path + "/Level_" + level["id"].replace("-", "_")
                )
                by_ref[level["ref"]] = level["path"]
                container = level["path"]
            item["path"] = (
                by_ref.get(item["ref"])
                or by_id.get(item["id"])
                or item.get("path")
                or container + "/Element_" + item["id"].replace("-", "_")
            )
            by_ref[item["ref"]] = item["path"]
        # Generated neighbours use the existing spatial container of a connected owner.
        for item in records:
            if item.get("origin") == "generated" and item["id"] not in by_id:
                peer_paths = [
                    by_ref[p["ownerRef"]]
                    for port in item.get("ports", [])
                    for p in port.get("refs", [])
                    if p["ownerRef"] in by_ref
                    and not by_ref[p["ownerRef"]].startswith(root_path + "/Element_")
                ]
                if peer_paths:
                    item["path"] = (
                        str(Sdf.Path(peer_paths[0]).GetParentPath())
                        + "/Element_"
                        + item["id"].replace("-", "_")
                    )
                    by_ref[item["ref"]] = item["path"]
        for item in records:
            if item.get("active") is False:
                continue
            owner = Binding.from_record(item)
            for port in item.get("ports", []):
                peers = port.get("refs", [])
                if len(peers) > 1:
                    raise ValueError(
                        "Multiple physical peers cannot identify one exporter port"
                    )
                peer = peers[0] if peers else {}
                identity = port_identity(
                    owner,
                    port["connectorId"],
                    peer_guid=peer.get("ifcGuid"),
                    peer_connector=peer.get("connectorId"),
                )
                evidence = next(
                    (
                        g
                        for g in identity["exporterCandidates"]
                        if guid_to_uuid(g) in port_by_id
                    ),
                    None,
                )
                if evidence:
                    identity = port_identity(
                        owner,
                        port["connectorId"],
                        peer_guid=peer.get("ifcGuid"),
                        peer_connector=peer.get("connectorId"),
                        exported_guid=evidence,
                    )
                port.update(identity)
                port["path"] = (
                    by_ref.get(port["ref"])
                    or port_by_id.get(port["id"])
                    or item["path"] + "/Port_" + str(port["connectorId"])
                )
                port["version"], port["document"] = item["version"], item["document"]
                by_ref[port["ref"]] = port["path"]
                if not port["identityResolved"]:
                    self._diagnostics.add(
                        "info",
                        "revit:portIdentityUnresolved",
                        "Connected port export order needs an IFC snapshot; native binding and candidates retained",
                        [port["path"]],
                        phase="readback",
                        host_refs=[port["ref"]],
                    )
            typ = item.get("type")
            if typ:
                typ["id"] = guid_to_uuid(typ["ifcGuid"])
                typ["path"] = (
                    by_ref.get(typ["ref"])
                    or by_ifc.get(typ["ifcGuid"])
                    or "/_Types/Revit_" + typ["id"].replace("-", "_")
                )
                by_ref[typ["ref"]] = typ["path"]
                item["inherits"] = [typ["path"]]
            if item.get("kind") == "camera":
                from aeco_sync.cctv import normalize_revit_camera
                normalize_revit_camera(item)
        for item in records:
            item["joins"] = {
                name: [by_ref[r] for r in refs]
                for name, refs in item.get("joins", {}).items()
            }
            for name in ("aeco:axis:start", "aeco:axis:end", "aeco:axis:arcPoint"):
                if name in item.get("drivers", {}):
                    item["drivers"][name] = Gf.Vec3d(*item["drivers"][name])
            for port in item.get("ports", []):
                port["connected"] = [by_ref[r] for r in port["connected"]]
            if item.get("hostRef"):
                item["hostPath"] = by_ref.get(item["hostRef"], "")
        receipt["diagnostics"] = self._diagnostics.items
        return receipt


def write_export(receipt, destination):
    entry = receipt["ifc"]
    data = base64.b64decode(entry["base64"], validate=True)
    if (
        len(data) != entry["bytes"]
        or hashlib.sha256(data).hexdigest() != entry["sha256"]
    ):
        raise ValueError("IFC snapshot checksum mismatch")
    destination = Path(destination)
    destination.write_bytes(data)
    return destination
