"""Identity, source preservation and driver-only intent in the full facility."""
import copy
import json
import os
from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom
from usdaeco_revit.facility_replay import (
    TARGETS, RecordedClient, identities, record_source, resolve_targets, run_replay,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('AECO_DATACENTRE_ROOT', ROOT.parent/'usdaeco-datacentre'))/'dist/base/dc.usda'
RECORD = json.loads((ROOT/'examples/roundtrip/inputs/offline-receipts.json').read_text())


@pytest.fixture(scope='module')
def facility(tmp_path_factory):
    return run_replay(SOURCE, tmp_path_factory.mktemp('facility'), RECORD)


def test_recording_matches_pinned_manifest_and_source():
    assert record_source(SOURCE) == RECORD
    assert RECORD['sourceCounts'] == json.loads(SOURCE.with_name('dc.manifest.json').read_text())['counts']
    assert RECORD['liveStatus'] == 'NOT RUN'


def test_replay_retains_every_source_identity_and_prim(facility):
    session, summary = facility
    source = Usd.Stage.Open(str(SOURCE))
    final = session.current()
    assert identities(source).keys() == identities(final).keys()
    assert {p.GetPath() for p in source.TraverseAll()} <= {p.GetPath() for p in final.TraverseAll()}
    assert summary['matchedIdentities'] == 2
    assert summary['accepted'] == 2
    assert summary['driverDifferences'] == summary['repeatMutations'] == 0
    assert summary['driversCompared'] == 14 and summary['bodiesCompared'] == 2
    assert not final.GetPrimAtPath('/Model')


def test_source_topology_and_untouched_geometry_preserved(facility):
    session, _ = facility
    source = Usd.Stage.Open(str(SOURCE))
    final = session.current()
    for prim in source.Traverse():
        other = final.GetPrimAtPath(prim.GetPath())
        if prim.GetTypeName() == 'AecoPort':
            assert other.IsActive()
            assert prim.GetRelationship('aeco:connectedPorts').GetTargets() == other.GetRelationship('aeco:connectedPorts').GetTargets()
            assert UsdGeom.Xformable(prim).GetLocalTransformation() == UsdGeom.Xformable(other).GetLocalTransformation()
        if prim.IsA(UsdGeom.Mesh) and prim.GetParent().GetAttribute('aeco:id').Get() not in TARGETS.values():
            assert prim.GetAttribute('points').Get() == other.GetAttribute('points').Get()
            assert prim.GetAttribute('faceVertexIndices').Get() == other.GetAttribute('faceVertexIndices').Get()


def test_final_opinions_separate_drivers_from_derived(facility):
    session, _ = facility
    current = session.current()
    targets = resolve_targets(current)
    assert targets['wall'].GetAttribute('aeco:wall:height').Get() == 3.4
    assert targets['pipe'].GetAttribute('aeco:pipe:nominalDiameter').Get() == .25
    assert session.status()['pending'] == 0
    for prim in targets.values():
        mesh = prim.GetPath().AppendChild('Geom')
        assert not session.layer('result.revit.usda').GetPrimAtPath(mesh)
        assert session.layer('derived.usda').GetPrimAtPath(mesh)
        for attr in prim.GetAttributes():
            if attr.GetMetadata('aecoDerived'):
                assert not session.layer('result.revit.usda').GetAttributeAtPath(attr.GetPath())


@pytest.mark.parametrize('defect', ['absent', 'duplicate', 'wrong_classification'])
def test_bad_identity_mapping_is_rejected(defect):
    stage = Usd.Stage.Open(str(SOURCE))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        wall = resolve_targets(stage)['wall']
        if defect == 'absent':
            wall.GetAttribute('aeco:id').Set('other')
        elif defect == 'duplicate':
            stage.DefinePrim('/Duplicate', 'Xform').CreateAttribute('aeco:id', Sdf.ValueTypeNames.String).Set(TARGETS['wall'])
        else:
            wall.GetAttribute('aeco:class:ifc:code').Set('IfcDoor')
    with pytest.raises(ValueError):
        resolve_targets(stage)


def test_recording_resolves_moved_prim_by_identity():
    stage = Usd.Stage.Open(str(SOURCE))
    wall = resolve_targets(stage)['wall']
    moved = str(wall.GetPath())+'_Moved'
    # Edit an anonymous flattened stage; never edit the pinned source.
    stage = Usd.Stage.Open(stage.Flatten())
    editor = Usd.NamespaceEditor(stage)
    assert editor.MovePrimAtPath(wall.GetPath(), moved)
    assert editor.ApplyEdits()
    client = RecordedClient(RECORD, stage)
    assert moved+'.aeco:wall:height' in client.changes


def test_stale_receipt_and_unexpected_edit_refused(tmp_path):
    stale = copy.deepcopy(RECORD)
    stale['changed']['touched'][0]['drivers']['aeco:wall:height'] = 9.
    with pytest.raises(ValueError, match='stale'):
        run_replay(SOURCE, tmp_path, stale)
    client = RecordedClient(RECORD, Usd.Stage.Open(str(SOURCE)))
    with pytest.raises(ValueError, match='unexpected'):
        client.exchange({'action': 'sync', 'edits': []})
