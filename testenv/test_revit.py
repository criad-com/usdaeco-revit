from aeco_sync.hosts.base import Operations
from usdaeco_revit.runtime import python as run_python
"""Offline transport, native wire, identity and layer transaction regression tests."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time

import pytest
from pxr import Gf, Usd, UsdGeom

from aeco_sync import engine
from aeco_sync.closure import Closure, plan
from aeco_sync.edits import Edit, author, collect
from aeco_sync.diagnostics import Diagnostics
from usdaeco_revit.transport import ReplClient, ReplStopped, ReplBusy, script_pack
from usdaeco_revit.host import RevitHost, HostRefused, write_export
from usdaeco_revit.identity import Binding, port_identity
from aeco_sync.readback import bind, publish
from aeco_sync.stack import facts


class WireRepl:
    """Exercise the actual page assembler against a truncating fake REPL."""

    def __init__(self, receipt):
        self.body = json.dumps(receipt).encode()
        self.calls = []
        self.pending = []
        self.corrupt = False
        self.response_error = None

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, payload))
        if method == "GET":
            return self.pending.pop(0) if self.pending else {"status": "ready"}
        if self.response_error:
            raise self.response_error
        code = payload["code"]
        if "AecoRevit.Transport" in code:
            key = re.search(r'"(usdaeco-reply-[a-f0-9]+)"\);$', code)[1]
            header = {
                "key": key,
                "bytes": len(self.body),
                "sha256": hashlib.sha256(self.body).hexdigest(),
            }
            return {"result": json.dumps(header)}
        offset, length = map(
            int, re.search(r"ToBase64String\(bytes, (\d+), (\d+)\)", code).groups()
        )
        chunk = self.body[offset : offset + length]
        if self.corrupt:
            chunk = chunk[:-1]
        encoded = base64.b64encode(chunk).decode()
        assert len(encoded) < 5000
        return {"result": encoded}


def client(tmp_path, fake, **kwargs):
    kwargs.setdefault("poll_interval", 0)  # deterministic offline transport; production minimum is 120 s
    return ReplClient(
        "http://repl.example", request=fake, lock_directory=tmp_path, **kwargs
    )


def test_pages_status_before_every_post_and_only_one_mutation(tmp_path):
    expected = {"mesh": list(range(10000)), "unicode": "ø × 三" * 2000}
    wire = WireRepl(expected)
    c = client(tmp_path, wire)
    assert c.exchange({"edits": []}) == expected
    assert (
        len(
            [
                p
                for method, p in wire.calls
                if method == "POST" and "AecoRevit.Transport" in p["code"]
            ]
        )
        == 1
    )
    assert all(
        wire.calls[i - 1][0] == "GET"
        for i, (method, _) in enumerate(wire.calls)
        if method == "POST"
    )


def test_busy_backs_off_before_post(tmp_path):
    wire = WireRepl({"ok": True})
    wire.pending = [{"error": "Too many pending"}, {"pendingCount": 2}, {"busy": True}]
    sleeps = []
    c = client(tmp_path, wire, sleep=sleeps.append)
    assert c.exchange({}) == {"ok": True}
    assert sleeps == [1, 2, 4]
    assert [method for method, _ in wire.calls[:4]] == ["GET"] * 4


def test_busy_deadline_never_posts(tmp_path):
    calls = []

    def request(method, *args):
        calls.append(method)
        return {"error": "Too many pending"}

    c = client(tmp_path, request, busy_timeout=0)
    with pytest.raises(ReplBusy):
        c.exchange({})
    assert calls == ["GET"]
    assert not c.stop_path.exists()


@pytest.mark.parametrize(
    "failure", [TimeoutError("timeout"), ValueError("invalid response JSON")]
)
def test_uncertain_post_stops_across_client_instances_without_retry(tmp_path, failure):
    wire = WireRepl({})
    wire.response_error = failure
    c = client(tmp_path, wire)
    with pytest.raises(ReplStopped):
        c.exchange({})
    calls = len(wire.calls)
    with pytest.raises(ReplStopped):
        client(tmp_path, wire).exchange({})
    assert len(wire.calls) == calls == 2


def test_status_timeout_stops_before_any_post(tmp_path):
    methods = []
    now = [0.0]

    def request(method, *args):
        methods.append(method)
        now[0] += 45
        raise TimeoutError("status timeout")

    with pytest.raises(ReplStopped):
        client(
            tmp_path,
            request,
            clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        ).exchange({})
    assert methods == ["GET"] * 4
    assert now[0] > 180


def test_slow_status_recovers_without_declaring_a_wedge(tmp_path):
    wire = WireRepl({"ok": True})
    now = [0.0]
    failures = [2]

    def request(method, url, payload, timeout):
        if method == "GET":
            assert timeout >= 45
            now[0] += 45
            if failures[0]:
                failures[0] -= 1
                raise TimeoutError("slow status")
        return wire(method, url, payload, timeout)

    c = client(
        tmp_path,
        request,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert c.exchange({}) == {"ok": True}
    assert not c.stop_path.exists()


def test_truncated_receipt_stops_without_replaying_mutation(tmp_path):
    wire = WireRepl({"a": "b" * 10000})
    wire.corrupt = True
    with pytest.raises(ReplStopped, match="Truncated"):
        client(tmp_path, wire).exchange({})
    assert len([p for m, p in wire.calls if m == "POST"]) == 2


def test_concurrent_clients_serialize_entire_exchange(tmp_path):
    wire = WireRepl({"long": "x" * 10000})
    sessions = []

    def slow(method, url, payload, timeout):
        if payload:
            sessions.append(payload["session"])
        time.sleep(0.001)
        return wire(method, url, payload, timeout)

    results = []
    workers = [
        threading.Thread(
            target=lambda: results.append(client(tmp_path, slow).exchange({}))
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
    assert len(results) == 2
    assert sum(a != b for a, b in zip(sessions, sessions[1:])) == 1


@pytest.mark.parametrize(
    "vector",
    json.loads((Path(__file__).parent / "fixtures/port_guid_vectors.json").read_text()),
)
def test_native_binding_resolves_exporter_port_vectors(vector):
    owner = Binding(
        "native-owner", "123", "element-v1", "project-guid", vector["elementGuid"]
    )
    result = port_identity(
        owner,
        vector["connectorId"],
        peer_guid=vector.get("peerGuid"),
        peer_connector=vector.get("peerConnectorId"),
        exported_guid=vector["expected"],
    )
    assert result["ifcGuid"] == vector["expected"] and result["identityResolved"]
    assert result["ref"] == "native-owner:" + str(vector["connectorId"])


def test_connected_identity_does_not_guess_export_order():
    vector = next(
        v
        for v in json.loads(
            (Path(__file__).parent / "fixtures/port_guid_vectors.json").read_text()
        )
        if "peerGuid" in v
    )
    owner = Binding("owner", "1", "v", "doc", vector["elementGuid"])
    result = port_identity(
        owner,
        vector["connectorId"],
        peer_guid=vector["peerGuid"],
        peer_connector=vector["peerConnectorId"],
    )
    assert len(result["exporterCandidates"]) == 2
    assert not result["identityResolved"] and result["ifcGuid"] == ""
    with pytest.raises(ValueError, match="recipe"):
        port_identity(
            owner,
            vector["connectorId"],
            peer_guid=vector["peerGuid"],
            peer_connector=vector["peerConnectorId"],
            exported_guid="not-a-candidate",
        )


class ReplyClient:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def exchange(self, request):
        self.requests.append(request)
        return copy.deepcopy(self.reply)


def receipt(end=3, *, generated=False):
    matrix = [[float(r == c) for c in range(4)] for r in range(4)]
    row = {
        "ref": "native-pipe",
        "localRef": "101",
        "version": "element-v2",
        "document": "project-guid",
        "ifcGuid": "2KRgI0pKDAlf0OXUCd$Qia",
        "path": "/Pipe",
        "matrix": matrix,
        "kind": "pipe",
        "drivers": {
            "aeco:axis:start": [0.0, 0.0, 0.0],
            "aeco:axis:end": [0.0, 0.0, float(end)],
        },
        "derived": {"aeco:axis:length": float(end)},
        "ports": [],
        "joins": {},
        "generated": [],
    }
    if generated:
        row.update(
            ref="native-fitting",
            localRef="102",
            path="",
            kind="fitting",
            origin="generated",
            ifcGuid="0KJfVVJ$r0T82VbLQMRdgf",
            drivers={"aeco:pipeFitting:origin": "generated"},
            derived={},
        )
    return {
        "touched": [row],
        "meshes": {
            row["ref"]: {
                "verts": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                "faces": [0, 1, 2],
            }
        },
        "diagnostics": [],
        "version": "native-v2",
        "document": "project-guid",
        "status": "committed",
        "stamp": "Revit test receipt",
    }


@pytest.fixture
def revit_session(session):
    result, _ = session.ensure_host("revit")
    with Usd.EditContext(session.stage, result):
        bind(
            session.stage.GetPrimAtPath("/Pipe"),
            "revit",
            "native-pipe",
            "101",
            "element-v1",
            "project-guid",
        )
    result.customLayerData = facts("revit", "project-guid", "native-v1")
    result.Save()
    return session


def host(session, reply=None):
    return RevitHost(
        session,
        client=ReplyClient(reply or receipt()),
        expected_path=r"C:\Models\sample.rvt",
    )


def test_engine_bad_size_has_zero_revit_calls(revit_session, monkeypatch):
    native = host(revit_session)
    monkeypatch.setattr(engine, "adapter", lambda *args, **kwargs: native)
    author(revit_session, "/Pipe", ["diameter=.033"])
    result = engine.apply(revit_session, "revit")
    assert result["accepted"] == 0 and result["pending"] == 1
    assert [d["code"] for d in result["diagnostics"]] == ["pipeSizeNotInTable"]
    assert native.client.requests == []


def test_live_receipt_publishes_without_ifc_save_and_keeps_element_version(
    revit_session, monkeypatch
):
    native = host(revit_session)
    monkeypatch.setattr(engine, "adapter", lambda *args, **kwargs: native)
    author(revit_session, "/Pipe", ["length=3"])
    result = engine.apply(revit_session, "revit")
    assert result["accepted"] == 1 and result["pending"] == 0, result
    assert len(native.client.requests) == 1
    request = native.client.requests[0]
    assert request["expectedVersion"] == "native-v1"
    assert request["bindings"]["/Pipe"]["version"] == "element-v1"
    # Keep the owning stage alive when using USD handles.
    current_stage = revit_session.current()
    current = current_stage.GetPrimAtPath("/Pipe")
    assert current.GetAttribute("aeco:axis:length").Get() == 3
    assert current.GetAttribute("aeco:host:revit:version").Get() == "element-v2"
    assert revit_session.version("revit") == "native-v2"
    assert not native.journal.exists()
    assert not (revit_session.path.parent / "host.revit.ifc").exists()


def test_failure_details_survive_intent_rollback_and_usd_diagnostics(
    revit_session, monkeypatch
):
    reply = receipt()
    reply.update(status="refused", touched=[], meshes={})
    reply["diagnostics"] = [
        {
            "severity": "error",
            "code": "revit:failure-guid",
            "message": "Hosted instance does not cut its wall",
            "phase": "commit",
            "blocking": True,
            "hostRefs": ["101"],
            "definitionId": "failure-guid",
            "nativeSeverity": "Error",
            "failingIds": ["101", "200"],
            "resolutions": ["DeleteElements"],
            "resolutionCount": 1,
            "rolledBack": True,
        }
    ]
    native = host(revit_session, reply)
    monkeypatch.setattr(engine, "adapter", lambda *args, **kwargs: native)
    author(revit_session, "/Pipe", ["length=3"])
    result = engine.apply(revit_session, "revit")
    assert result["accepted"] == 0 and result["pending"] == 1
    assert result["diagnostics"][0]["about"] == ["/Pipe"]
    p = revit_session.stage.GetPrimAtPath("/Sync/Diagnostics/revit/d0001")
    details = json.loads(p.GetCustomDataByKey("aecoSync:native"))
    assert details["resolutionCount"] == 1 and details["rolledBack"]


def test_native_success_survives_publish_failure_as_recoverable_journal(
    revit_session, monkeypatch
):
    native = host(revit_session)
    monkeypatch.setattr(engine, "adapter", lambda *args, **kwargs: native)

    def fail(*args):
        raise RuntimeError("publish failure")

    monkeypatch.setattr(engine, "publish", fail)
    author(revit_session, "/Pipe", ["length=3"])
    result = engine.apply(revit_session, "revit")
    assert result["accepted"] == 0 and result["pending"] == 1
    assert json.loads(native.journal.read_text())["status"] == "committed"
    with pytest.raises(HostRefused, match="reconciliation"):
        native.apply(Operations([], Closure()))
    assert len(native.client.requests) == 1


def test_generated_fitting_is_materialized_with_mesh_and_identity(revit_session):
    native = host(revit_session, receipt(generated=True))
    normalized = native.exchange(native.request())
    publish(
        revit_session,
        normalized,
        revit_session.layer("result.revit.usda"),
        "revit",
        native.version(),
        native.document,
    )
    p = revit_session.stage.GetPrimAtPath(normalized["touched"][0]["path"])
    assert p.HasAPI("AecoElementAPI") and p.HasAPI("AecoPipeFittingAPI")
    assert p.GetAttribute("aeco:id").Get()
    assert p.GetAttribute("aeco:pipeFitting:origin").Get() == "generated"
    assert p.GetChild("Geom").IsA(UsdGeom.Mesh)


def test_snapshot_interface_has_no_file_host_dependency(revit_session, monkeypatch):
    reply = receipt()
    reply["status"] = "snapshot"
    native = host(revit_session, reply)
    monkeypatch.setattr(engine, "adapter", lambda *args, **kwargs: native)
    result = engine.readback(revit_session, "revit", None)
    assert result["touched"] == ["native-pipe"]
    assert native.client.requests[0]["action"] == "snapshot"


def test_ifc_transfer_checks_bytes_and_hash(tmp_path):
    data = b"ISO-10303-21;"
    entry = {
        "base64": base64.b64encode(data).decode(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert write_export({"ifc": entry}, tmp_path / "export.ifc").read_bytes() == data
    entry["sha256"] = "bad"
    with pytest.raises(ValueError, match="checksum"):
        write_export({"ifc": entry}, tmp_path / "bad.ifc")
    assert not (tmp_path / "bad.ifc").exists()


def test_script_pack_is_packaged_and_guards_native_mutations():
    source = script_pack({"edits": []}, "test-key")
    assert "ProceedWithRollBack" in source
    assert "MoveElement(c.Doc, peers[0].Owner.Id, target - original)" in source
    assert "if (peers.Count == 0)" in source
    assert "SetFailuresPreprocessor" in source
    assert (
        "DocumentChanged += changed" in source
        and "DocumentChanged -= changed" in source
    )
    assert "StoreIFCGUID" in source and "Face face" in source
    assert "OpenAndActivateDocument(" not in source
    assert 'request["backgroundDocument"]' in source
    assert "d != foreground" in source


def test_disallowing_wall_join_in_same_request_allows_axis_edit(revit_session):
    stage = revit_session.stage
    with Usd.EditContext(stage, revit_session.layer("kind.usda")):
        wall = stage.GetPrimAtPath("/Pipe")
        wall.ApplyAPI("AecoWallAPI")
        wall.GetRelationship("aeco:wall:joinAtEnd").SetTargets(["/Neighbour"])
    edits = [
        Edit("/Pipe", "aeco:axis:end", [0, 0, 3], [0, 0, 2], "axis"),
        Edit("/Pipe", "aeco:wall:allowJoinAtEnd", False, True, "semantic"),
    ]
    diag = Diagnostics()
    accepted, closure = plan(revit_session, edits, diag)
    assert accepted == edits and not diag.items


def test_wall_type_swap_uses_resolved_inherits_wire_paths(revit_session):
    stage = revit_session.stage
    with Usd.EditContext(stage, revit_session.layer("kind.usda")):
        stage.GetPrimAtPath("/Pipe").ApplyAPI("AecoWallAPI")
        t = stage.CreateClassPrim("/_Types/Alternative")
        t.ApplyAPI("AecoTypeAPI")
        bind(t, "revit", "type-uid", "202", "type-v1", "project-guid")
    with Usd.EditContext(stage, revit_session.intent):
        stage.GetPrimAtPath("/Pipe").GetInherits().SetInherits(["/_Types/Alternative"])
    edits = collect(stage, revit_session.intent, revit_session.current())
    edit = next(e for e in edits if e.name == "inheritPaths")
    assert edit.wire()["value"] == ["/_Types/Alternative"]
    assert host(revit_session).supports(edit)
    assert host(revit_session).bindings()["/_Types/Alternative"]["ref"] == "type-uid"


def test_generated_ports_compose_in_plugin_free_process(revit_session):
    import os
    import subprocess
    import sys

    reply = receipt(generated=True)
    row = reply["touched"][0]
    row["ports"] = [
        {
            "ref": "native-fitting:0",
            "connectorId": 0,
            "localRef": "0",
            "matrix": np_identity(),
            "origin": [0, 0, 0],
            "radius": 0.025,
            "diameter": 0.05,
            "connected": [],
            "refs": [],
        }
    ]
    native = host(revit_session, reply)
    normalized = native.exchange(native.request())
    layer = revit_session.layer("result.revit.usda")
    publish(
        revit_session, normalized, layer, "revit", native.version(), native.document
    )
    layer.Save()
    port_path = normalized["touched"][0]["ports"][0]["path"]
    script = """import sys
from pxr import Usd, UsdGeom
s=Usd.Stage.Open(sys.argv[1])
assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoPort').isUnknown
assert s.GetPrimAtPath(sys.argv[2]).IsA(UsdGeom.Xform)
assert s.Flatten()
"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME", "AECO_KIND_PLUGIN")
    }
    process = run_python(
        ["-c", script, str(revit_session.path), port_path],
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr


def np_identity():
    return [[float(r == c) for c in range(4)] for r in range(4)]
