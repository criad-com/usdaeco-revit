# Prototype kind plugin

`usdAecoKindProto` is promoted from the supplied S4/S5 prototype plugin in the
spikes repository. Its generated schema and descriptor are copied with these
four duplicate definitions removed: AecoAxisAPI, AecoDerivedGeometryAPI,
AecoHostBindingAPI and AecoSyncDiagnostic. Those now belong to core v0.7 and
usdAecoSync. No usdAecoMeta plugin is used or included.

The seven retained APIs are Pipe, PipeType, PipePort, PipeSystem, PipeFitting,
Wall and BuildUp. Property defaults and derived metadata are unchanged. Plugin
metadata adds version 0.0.0 and a core >=0.7 dependency. This filtered fixture
prevents duplicate schema identifiers in the registry without editing the shared
spikes checkout. Replace it with usdAecoPipe/usdAecoWall/usdAecoBuildUp when their
independent implementations are available.

`port_guid_vectors.json` contains 13 known-answer port GUID vectors from the S3
native IFC export. Only opaque GUIDs and connector indexes are retained; no
source paths, project labels or element names are included. These exercise the
exporter's byte ordering and both possible connected-pair processing orders.
