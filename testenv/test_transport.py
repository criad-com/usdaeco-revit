"""Canonical builder calls share mutation and paging guarantees."""
import base64
import hashlib
import json
import re
import pytest
from usdaeco_revit.transport import Client, ReplClient, ReplStopped, script_pack
from aeco_sync.hosts.base import host_class
from usdaeco_revit.host import RevitHost

class BuilderWire:
    def __init__(self, body=b'phase result'):
        self.body=body; self.calls=[]; self.corrupt=False
    def __call__(self, method, url, payload, timeout):
        self.calls.append((method,payload))
        if method=='GET':return {'status':'ready'}
        code=payload['code']
        if 'phaseBytes.Length' in code:
            return {'result':json.dumps(dict(bytes=len(self.body),sha256=hashlib.sha256(self.body).hexdigest()))}
        if 'ToBase64String' in code:
            offset,length=map(int,re.search(r', (\d+), (\d+)\);',code).groups())
            return {'result':base64.b64encode(self.body[offset:offset+length-(1 if self.corrupt else 0)]).decode()}
        return {'result':'released'}

def test_builder_phase_uses_one_execution_and_checked_pages(tmp_path):
    wire=BuilderWire(b'x'*12001)
    c=Client('http://repl.example',request=wire,lock_directory=tmp_path)
    assert c.phase('var value = "result";\nvalue;') == 'x'*12001
    posts=[p for m,p in wire.calls if m=='POST']
    assert sum('phaseBytes.Length' in p['code'] for p in posts)==1
    assert all(wire.calls[i-1][0]=='GET' for i,(m,p) in enumerate(wire.calls) if m=='POST')

def test_builder_page_corruption_latches_endpoint(tmp_path):
    wire=BuilderWire();wire.corrupt=True
    c=Client('http://repl.example',request=wire,lock_directory=tmp_path)
    with pytest.raises(ReplStopped):c.phase('"result";')
    calls=len(wire.calls)
    with pytest.raises(ReplStopped):c.evaluate('return "another mutation";')
    assert len(wire.calls)==calls

def test_upload_uses_configured_workdir_and_verifies_bytes(tmp_path,monkeypatch):
    monkeypatch.setenv('AECO_REVIT_ENDPOINT','http://repl.example')
    monkeypatch.setenv('AECO_REVIT_WORKDIR','exchange')
    data=b'payload'*100000
    calls=[]
    def wire(method,url,payload,timeout):
        calls.append((method,payload))
        if method=='GET':return {'status':'ready'}
        code=payload['code']
        return {'result':hashlib.sha256(data).hexdigest() if 'HashData' in code else 'ok'}
    c=ReplClient(request=wire,lock_directory=tmp_path)
    result=c.upload(data,'plan.json')
    assert result['bytes']==len(data)
    assert sum('FileMode.Append' in p['code'] for m,p in calls if m=='POST')==2
    assert all(calls[i-1][0]=='GET' for i,(m,p) in enumerate(calls) if m=='POST')
    assert all('exchange' in p['code'] for m,p in calls if m=='POST')
    with pytest.raises(ValueError):c.upload(data,'../plan.json')

def test_upload_checksum_mismatch_stops(tmp_path):
    wire=lambda method,*args: {'status':'ready'} if method=='GET' else {'result':'wrong-hash'}
    c=ReplClient('http://repl.example',request=wire,lock_directory=tmp_path,workdir='exchange')
    with pytest.raises(ReplStopped,match='checksum'):c.upload(b'bytes','plan.json')

def test_discovered_revit_implements_contract(tmp_path, monkeypatch):
    assert host_class('revit') is RevitHost
    assert not RevitHost.__abstractmethods__
    assert isinstance(RevitHost().capabilities(),frozenset)
    from pxr import Plug, Usd
    from usdaeco_revit.plugins import register_plugins
    registry = Plug.Registry()
    core = registry.GetPluginWithName('usdAeco')
    assert core.metadata['aeco']['version'] == '0.9.5'
    assert registry.GetPluginWithName('usdAecoAxis').metadata['aeco']['version'] == '0.1.5'
    assert registry.GetPluginForType(Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoAxisAPI')).name == 'usdAecoAxis'
    stage = Usd.Stage.CreateInMemory()
    prim = stage.DefinePrim('/Element', 'Xform')
    assert prim.ApplyAPI('AecoAxisAPI')
    assert prim.GetAttribute('aeco:axis:length').GetMetadata('aecoDerived') is True
    old = tmp_path / 'usdAeco'
    old.mkdir()
    metadata = dict(core.metadata['aeco'], version='0.8.4')
    (old / 'plugInfo.json').write_text(json.dumps({'Plugins': [{'Info': {'aeco': metadata}}]}))
    with pytest.raises(RuntimeError, match='requires usdAeco'):
        register_plugins(core=tmp_path)
    monkeypatch.setenv('AXIS_PLUGIN_DIR', str(tmp_path / 'missing-axis'))
    with pytest.raises(RuntimeError, match='usdAecoAxis'):
        register_plugins()


def test_script_hash_tampering_fails_before_transport(tmp_path,monkeypatch):
    import usdaeco_revit.transport as transport
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/00_bad.csx').write_text('changed')
    (tmp_path/'scripts/manifest.json').write_text(json.dumps({'00_bad.csx':'incorrect'}))
    monkeypatch.setattr(transport,'files',lambda package:tmp_path)
    with pytest.raises(RuntimeError,match='checksum'):script_pack({},'probe')
