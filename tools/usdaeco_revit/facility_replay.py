"""Offline receipt fixtures from the pinned facility, never native evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom
from aeco_sync.identity import uuid_to_guid
from .host import RevitHost
from .replay import replay

TARGETS = {
    'wall': '46bb8ddc-66ac-5ee6-ba2f-72feb5bf8844',
    'pipe': 'e4b8ed4b-bb06-525a-b907-1cb5c1d03950',
}
DOCUMENT = 'offline-demo-datacentre-01'


def identities(stage):
    result = {}
    for prim in stage.TraverseAll():
        identity = prim.GetAttribute('aeco:id').Get()
        if identity:
            if identity in result:
                raise ValueError('Duplicate source identity: ' + identity)
            result[identity] = prim
    return result


def resolve_targets(stage, targets=TARGETS):
    index = identities(stage)
    if not set(targets.values()) <= index.keys():
        raise ValueError('Replay identity absent from pinned source')
    result = {name: index[identity] for name, identity in targets.items()}
    for name, prim in result.items():
        expected = 'IfcWall' if name == 'wall' else 'IfcPipeSegment'
        if not (prim.GetAttribute('aeco:class:ifc:code').Get() or '').startswith(expected):
            raise ValueError('Replay identity has the wrong classification')
    return result


def record_source(source):
    """Re-record two simple parameter edits from source tessellations offline.

    The base has no office pipes: the selected pipe is at the office boundary.
    Only wall height and pipe diameter change; source port topology is retained.
    """
    source = Path(source)
    manifest_path = source.with_name('dc.manifest.json')
    manifest = json.loads(manifest_path.read_text())
    stage = Usd.Stage.Open(str(source))
    targets = resolve_targets(stage)
    baseline = dict(touched=[], meshes={}, diagnostics=[], version='offline-v1',
                    document=DOCUMENT, status='snapshot', stamp='Offline source fixture; no native solve')
    cache = UsdGeom.XformCache()
    for name, prim in targets.items():
        mesh = UsdGeom.Mesh(prim.GetChild('Geom'))
        points = [list(p) for p in mesh.GetPointsAttr().Get()]
        if set(mesh.GetFaceVertexCountsAttr().Get()) != {3}:
            raise ValueError('Offline recording requires source triangles')
        lower = [min(p[i] for p in points) for i in range(3)]
        upper = [max(p[i] for p in points) for i in range(3)]
        drivers = {key: list(prim.GetAttribute(key).Get())
                   for key in ('aeco:axis:start', 'aeco:axis:end')}
        diameter = round(upper[0] - lower[0], 6)
        if name == 'wall':
            drivers['aeco:wall:height'] = upper[2] - lower[2]
        else:
            drivers['aeco:pipe:nominalDiameter'] = diameter
        row = dict(ref='offline-'+name, localRef=name, version='offline-element-v1',
                   document=DOCUMENT, ifcGuid=uuid_to_guid(TARGETS[name]), kind=name,
                   matrix=[list(r) for r in cache.GetLocalToWorldTransform(prim)],
                   drivers=drivers, derived={'aeco:axis:length': prim.GetAttribute('aeco:axis:length').Get()},
                   ports=[], joins={}, generated=[])
        if name == 'pipe':
            row['derived']['aeco:pipe:outerDiameter'] = diameter
        else:
            row['derived']['aeco:wall:thickness'] = round(upper[1]-lower[1], 6)
        # These are already-resolved USD port facts, not exporter connector IDs.
        row['sourcePorts'] = [dict(
            id=port.GetAttribute('aeco:id').Get(),
            matrix=[list(r) for r in UsdGeom.Xformable(port).GetLocalTransformation()],
            flow=port.GetAttribute('aeco:flowDirection').Get(), diameter=diameter,
            connectedIds=[stage.GetPrimAtPath(p).GetAttribute('aeco:id').Get()
                          for p in port.GetRelationship('aeco:connectedPorts').GetTargets()])
            for port in prim.GetChildren() if port.GetTypeName() == 'AecoPort']
        baseline['touched'].append(row)
        baseline['meshes'][row['ref']] = dict(verts=[c for p in points for c in p],
                                             faces=list(mesh.GetFaceVertexIndicesAttr().Get()))
    changed = copy.deepcopy(baseline)
    changed.update(version='offline-v2', status='committed')
    edits = []
    for row in changed['touched']:
        name = row['kind']
        prop = 'aeco:wall:height' if name == 'wall' else 'aeco:pipe:nominalDiameter'
        old = row['drivers'][prop]
        new = round(old + (.4 if name == 'wall' else .05), 6)
        row['drivers'][prop] = new
        row['version'] = 'offline-element-v2'
        vertices = changed['meshes'][row['ref']]['verts']
        for i in range(len(vertices)):
            if (name == 'wall' and i % 3 == 2) or (name == 'pipe' and i % 3 != 2):
                vertices[i] *= new / old
        if name == 'pipe':
            row['derived']['aeco:pipe:outerDiameter'] = new
            for port in row['sourcePorts']:
                port['diameter'] = new
        edits.append(dict(id=TARGETS[name], name=prop, value=new))
    return dict(mode='offline-source-recording', liveStatus='NOT RUN',
                sourceManifestSha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                sourceGenerator=manifest['generator']['version'], sourceCounts=manifest['counts'],
                sourcePrimCount=sum(1 for _ in stage.TraverseAll()),
                targets=TARGETS, edits=edits, baseline=baseline, changed=changed)


class RecordedClient:
    def __init__(self, record, stage):
        self.record = record
        self.requests = []
        index = identities(stage)
        resolve_targets(stage, record['targets'])
        self.changes = {str(index[e['id']].GetPath().AppendProperty(e['name'])): e['value']
                        for e in record['edits']}

    def exchange(self, request):
        self.requests.append(copy.deepcopy(request))
        if request['action'] == 'snapshot':
            return copy.deepcopy(self.record['baseline'])
        actual = {str(Sdf.Path(e['path']).AppendProperty(e['name'])): e['value'] for e in request['edits']}
        if request['action'] != 'sync' or actual != self.changes:
            raise ValueError('Offline recording received unexpected driver edits')
        return copy.deepcopy(self.record['changed'])


class OfflineSourceHost(RevitHost):
    """Use recorded USD port identities; native exporter mapping is not tested."""
    def normalize(self, raw):
        receipt = super().normalize(raw)
        index = identities(self.current)
        for row in receipt['touched']:
            for source in row.pop('sourcePorts'):
                prim = index[source['id']]
                if prim.GetParent().GetPath() != Sdf.Path(row['path']):
                    raise ValueError('Recorded port has a different owner')
                row['ports'].append(dict(
                    id=source['id'], path=str(prim.GetPath()), ref='offline-port-'+source['id'],
                    matrix=source['matrix'], flow=source['flow'], diameter=source['diameter'],
                    connected=[str(index[i].GetPath()) for i in source['connectedIds']]))
        return receipt


def run_replay(source, out, record):
    """Run the recorded fixture in the full stage; retain only owned opinions."""
    source = Path(source)
    if record != record_source(source):
        raise ValueError('Offline recording is stale relative to the pinned source')
    stage = Usd.Stage.Open(str(source))
    client = RecordedClient(record, stage)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # A small source wrapper avoids copying any facility layer into session data.
    seed = Sdf.Layer.CreateNew(str(out/'seed.usda'))
    seed.subLayerPaths = [os.path.relpath(source, out)]
    seeded = Usd.Stage.Open(seed)
    for key in ('defaultPrim', 'upAxis', 'metersPerUnit', 'fallbackPrimTypes'):
        seeded.SetMetadata(key, stage.GetMetadata(key))
    pipe = resolve_targets(seeded)['pipe']
    with Usd.EditContext(seeded, seed):
        pipe.ApplyAPI('AecoPipeTypeAPI')
        diameters = [
            row['drivers']['aeco:pipe:nominalDiameter']
            for receipt in (record['baseline'], record['changed'])
            for row in receipt['touched'] if row['kind'] == 'pipe']
        pipe.GetAttribute('aeco:pipeType:nominalDiameters').Set(diameters)
        pipe.GetAttribute('aeco:pipeType:outerDiameters').Set(diameters)
        pipe.GetAttribute('aeco:pipeType:innerDiameters').Set([0. for _ in diameters])
    seed.Save()
    session, summary = replay(out/'replay', seed.realPath, client=client, changes=client.changes,
                              host_type=OfflineSourceHost)
    # Keep geometry and derived measurements out of the resolved-driver layer.
    result = session.layer('result.revit.usda')
    derived = session.layer('derived.usda')
    for prim in resolve_targets(session.stage).values():
        paths = [prim.GetPath().AppendChild('Geom')]
        paths += [a.GetPath() for a in prim.GetAttributes()
                  if a.GetMetadata('aecoDerived') and result.GetAttributeAtPath(a.GetPath())]
        for path in paths:
            Sdf.CreatePrimInLayer(derived, path.GetParentPath() if path.IsPrimPath() else path.GetPrimPath())
            Sdf.CopySpec(result, path, derived, path)
            edit = Sdf.BatchNamespaceEdit()
            edit.Add(path, Sdf.Path.emptyPath)
            if not result.Apply(edit):
                raise ValueError('Cannot separate replay-derived opinion')
        with Usd.EditContext(session.stage, derived):
            axis = prim.GetChild('Axis')
            axis.GetAttribute('aeco:derived:tolerance').Set(1e-6)
    for layer in (session.root, session.intent, result, derived, session.layer('kind.usda'),
                  session.layer('diagnostics.revit.usda')):
        data = dict(layer.customLayerData)
        if 'aeco:sync:time' in data:
            data['aeco:sync:time'] = '2026-01-01T00:00:00+00:00'
        if 'aeco:sync:document' in data:
            data['aeco:sync:document'] = DOCUMENT
        layer.customLayerData = data
        layer.Save()
    summary['mode'] = 'offline-facility-receipt-replay'
    summary['sourceCounts'] = record['sourceCounts']
    summary['sourcePrimCount'] = record['sourcePrimCount']
    summary['matchedIdentities'] = len(resolve_targets(session.stage))
    summary['liveStatus'] = 'NOT RUN'
    return session, summary
