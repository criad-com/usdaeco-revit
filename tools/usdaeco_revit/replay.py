"""Audit archived live summaries and exercise an explicitly synthetic receipt."""
import copy
import json
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, Vt
from aeco_sync import engine
from aeco_sync.convergence import compare_receipts
from aeco_sync.hosts.base import host_class
from aeco_sync.identity import uuid_to_guid
from aeco_sync.readback import publish
from aeco_sync.stack import facts

IDENTITIES = {'Wall': '03e27eda-39f5-458d-b83e-72dfec506bc4',
              'Pipe': '946ea480-cd43-4abc-9018-85e327fdab24'}

def box(x, y, z):
    return {'verts': [0,0,0,x,0,0,x,y,0,0,y,0,0,0,z,x,0,z,x,y,z,0,y,z],
            'faces': [0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,1,2,6,1,6,5,2,3,7,2,7,6,3,0,4,3,4,7]}

def synthetic_receipt(changed=False):
    """Illustrative geometry and native handles; never labelled live evidence."""
    rows, meshes = [], {}
    for name in IDENTITIES:
        matrix = Gf.Matrix4d(1)
        if name == 'Pipe':
            matrix = Gf.Matrix4d(0,1,0,0, 0,0,1,0, 1,0,0,0, 0,-.3,1,1)
        kind = name.lower()
        length = 2.5 if changed else 2.
        drivers = {'aeco:axis:start': [0.,0.,0.],
                   'aeco:axis:end': [4.,0.,0.] if kind=='wall' else [0.,0.,length]}
        if kind=='wall': drivers['aeco:wall:height'] = 2.6 if changed else 2.4
        else: drivers['aeco:pipe:nominalDiameter'] = .05
        rows.append(dict(ref='synthetic-'+kind, localRef=name, version='element-v2' if changed else 'element-v1',
                         document='synthetic-document', ifcGuid=uuid_to_guid(IDENTITIES[name]),
                         path='/Model/'+name, matrix=[list(row) for row in matrix], kind=kind,
                         drivers=drivers, derived={'aeco:axis:length': 4. if kind=='wall' else length},
                         ports=[], joins={}, generated=[]))
        meshes['synthetic-'+kind] = box(4., .2, drivers['aeco:wall:height']) if kind=='wall' else box(.05,.05,length)
    return dict(touched=rows,meshes=meshes,diagnostics=[],version='v2' if changed else 'v1',
                document='synthetic-document',status='committed' if changed else 'snapshot',stamp='Synthetic offline replay')

class SyntheticClient:
    def __init__(self):
        self.requests = []
    def exchange(self, request):
        self.requests.append(copy.deepcopy(request))
        if request['action']=='snapshot': return synthetic_receipt()
        expected = {('/Model/Wall','aeco:wall:height'): 2.6,
                    ('/Model/Pipe','aeco:axis:end'): [0.,0.,2.5]}
        actual = {(e['path'],e['name']): e['value'] for e in request['edits']}
        if actual != expected:
            raise ValueError('Synthetic replay received an unexpected operation set')
        return synthetic_receipt(True)

def seed(directory):
    directory = Path(directory);directory.mkdir(parents=True,exist_ok=True)
    model = directory/'model.usda'
    stage = Usd.Stage.CreateNew(str(model))
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/Model').GetPrim())
    UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
    for name, identity in IDENTITIES.items():
        prim=UsdGeom.Xform.Define(stage,'/Model/'+name).GetPrim()
        for api in ('AecoElementAPI','AecoAxisAPI','Aeco'+name+'API'):
            prim.ApplyAPI(api)
        prim.GetAttribute('aeco:id').Set(identity)
    stage.GetRootLayer().Save()
    document = directory/'opaque-document.txt';document.write_text('Synthetic native document token\n')
    return model,document

def replay(directory, model=None, *, client=None, changes=None, host_type=None):
    directory=Path(directory)
    if model is None:
        model, document = seed(directory/'seed')
    else:
        directory.mkdir(parents=True,exist_ok=True)
        document=directory/'offline-document.txt'
        document.write_text('Offline receipt replay; no native document\n')
    host_type=host_type or host_class('revit')
    session=host_type.initialize(model,document,directory/'session')
    client=client or SyntheticClient()
    native=host_type(session,client=client,expected_path='offline-wallpipe.rvt')
    baseline=native.readback(None)
    result,_=session.ensure_host('revit')
    publish(session,baseline,result,'revit',native.version(),native.document)
    result.customLayerData=facts('revit',native.document,native.version());result.Save()
    native.current=session.current()
    changes=changes or {'/Model/Wall.aeco:wall:height':2.6,
                        '/Model/Pipe.aeco:axis:end':Gf.Vec3d(0,0,2.5)}
    fields=[Sdf.Path(path) for path in changes]
    session.capture_base(fields)
    with Usd.EditContext(session.stage,session.intent):
        for path,value in changes.items():
            session.stage.GetAttributeAtPath(path).Set(value)
    session.intent.Save()
    applied=engine.apply(session,'revit',native=native)
    if applied['accepted']!=2 or applied['pending']:
        raise AssertionError(applied)
    published=session.current()
    expected=native.receipt
    observed=dict(touched=[],meshes={})
    for row in expected['touched']:
        prim=published.GetPrimAtPath(row['path'])
        observed['touched'].append(dict(row, drivers={n:prim.GetAttribute(n).Get() for n in row['drivers']},
                                        matrix=UsdGeom.XformCache().GetLocalToWorldTransform(prim)))
        mesh=UsdGeom.Mesh(prim.GetChild('Geom'))
        observed['meshes'][row['ref']] = dict(verts=[float(c) for p in mesh.GetPointsAttr().Get() for c in p],
                                            faces=list(mesh.GetFaceVertexIndicesAttr().Get()))
    comparison=compare_receipts(expected,observed)
    if not comparison['driversConverged'] or comparison['diagnostics']:
        raise AssertionError(comparison)
    repeats=engine.apply(session,'revit',native=native)
    if repeats['mutations'] or len(client.requests)!=2:
        raise AssertionError('Repeat synthetic replay made another native request')
    summary=dict(mode='synthetic-receipt-replay', accepted=applied['accepted'],
                 driverDifferences=len(comparison['driverDifferences']),
                 repeatMutations=repeats['mutations'], nativeMutations=0,
                 driversCompared=comparison['driversCompared'], bodiesCompared=len(comparison['bodies']))
    (directory/'comparison.json').write_text(json.dumps(comparison,indent=2,default=list)+'\n')
    native.close()
    return session, summary

def audit(recording):
    record=json.loads(Path(recording).read_text())
    cases=record['cases']
    if len(cases)!=11 or any(r['status']!='pass' or not r['rollbackVerified'] for r in cases.values()):
        raise ValueError('Archived wall/pipe acceptance is incomplete')
    convergence=record['convergence']
    if not convergence['driversConverged'] or convergence['driverDifferences'] or convergence['missingDrivers'] or convergence['missingElements']:
        raise ValueError('Archived driver convergence failed')
    return dict(mode='archived-live-summary-audit', cases=len(cases), driversCompared=convergence['driversCompared'],
                elements=convergence['elements'], driverDifferences=len(convergence['driverDifferences']),
                liveStatus='NOT RUN', rawReceiptReplay='NOT PROVEN')
