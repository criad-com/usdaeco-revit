"""Revit native bindings and explicit resolution of exporter port ambiguity."""

from dataclasses import dataclass, asdict
from aeco_sync.identity import connected_candidates, free_port, guid_to_uuid, mint_id


@dataclass(frozen=True)
class Binding:
    ref: str
    localRef: str
    version: str
    document: str
    ifcGuid: str = ""

    def __post_init__(self):
        if not self.ref or not self.document:
            raise ValueError("UniqueId and durable document GUID are required")

    @property
    def id(self):
        return (
            guid_to_uuid(self.ifcGuid)
            if self.ifcGuid
            else mint_id("revit", self.ref, document=self.document)
        )

    def wire(self):
        return asdict(self)

    @classmethod
    def from_record(cls, record):
        """Only IFC_GUID joins routes. Mark is a human/spec label, never identity."""
        return cls(**{key: record.get(key, "") for key in
                      ("ref", "localRef", "version", "document", "ifcGuid")})


def camera_creation_context(stage, path):
    """Resolve the nearest AecoLevel through spaces and intermediate transforms."""
    from pxr import Sdf, UsdGeom
    from aeco_sync.stack import value
    parent = stage.GetPrimAtPath(Sdf.Path(path).GetParentPath())
    ancestor = parent
    while ancestor and not ancestor.IsPseudoRoot():
        if ancestor.GetTypeName() == "AecoLevel":
            return dict(levelPath=str(ancestor.GetPath()),
                        parentMatrix=value(UsdGeom.XformCache().GetLocalToWorldTransform(parent)))
        ancestor = ancestor.GetParent()
    raise ValueError(f"Camera {path} has no AecoLevel ancestor")


def port_identity(
    owner, connector, *, peer_guid=None, peer_connector=None, exported_guid=None
):
    """Never guess export order for a connected port.

    An existing USD/IFC port identifies the candidate exactly. Without that
    evidence the native recipe supplies a stable provisional id and returns the
    candidates explicitly, until an export resolves its order.
    """
    ref = f"{owner.ref}:{connector}"
    candidates = []
    if owner.ifcGuid:
        if peer_guid:
            candidates = list(
                connected_candidates(
                    owner.ifcGuid, connector, peer_guid, peer_connector
                )
            )
        elif peer_connector is None:
            candidates = [free_port(owner.ifcGuid, connector)]
    if exported_guid:
        if exported_guid not in candidates:
            raise ValueError(f"Port {ref} does not agree with the exporter recipe")
        guid = exported_guid
    else:
        guid = candidates[0] if len(candidates) == 1 else None
    return {
        "ref": ref,
        "id": (
            guid_to_uuid(guid)
            if guid
            else mint_id("revit", ref, document=owner.document, kind="port")
        ),
        "ifcGuid": guid or "",
        "exporterCandidates": candidates,
        "identityResolved": guid is not None,
    }
