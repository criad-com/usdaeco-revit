from aeco_sync.hosts.base import Operations
from usdaeco_revit.runtime import python as run_python
"""Camera gate selection, ancestry and bounded transport evidence."""
import gzip
import json
import copy

import pytest
from pxr import Usd, UsdGeom

from usdaeco_revit.transport import ReplStopped
from usdaeco_revit.host import RevitHost
from usdaeco_revit.identity import camera_creation_context
from aeco_sync.readback import publish
from aeco_sync.edits import collect
from usdaeco_revit.gates.cctv import fixture
from usdaeco_revit.gates.cctv_revit import author, camera_rows, CASES, DC_CASES, run_suite
from test_cctv import native_camera_receipt
from test_revit import WireRepl, client


@pytest.mark.parametrize("limit, succeeds", [(4096, True), (100, False)])
def test_compressed_receipt_checksum_and_expansion_limit(tmp_path, limit, succeeds):
    expected = {"data": "camera" * 500}
    class Compressed(WireRepl):
        def __call__(self, method, url, payload, timeout):
            result = super().__call__(method, url, payload, timeout)
            if method == "POST" and "AecoRevit.Transport" in payload["code"]:
                header = json.loads(result["result"])
                header["encoding"] = "gzip"
                result["result"] = json.dumps(header)
            return result
    wire = Compressed(expected)
    wire.body = gzip.compress(wire.body)
    transport = client(tmp_path, wire, max_reply=limit)
    if succeeds:
        assert transport.exchange({}) == expected
    else:
        with pytest.raises(ReplStopped, match="Expanded reply"):
            transport.exchange({})
    assert sum(method == "POST" and "AecoRevit.Transport" in payload["code"] for method, payload in wire.calls) == 1


def test_explicit_background_camera_route_keeps_legacy_default(tmp_path, monkeypatch):
    session = fixture(tmp_path)
    monkeypatch.setenv("AECO_REVIT_BACKGROUND", "1")
    monkeypatch.setenv("AECO_REVIT_SCOPE", "cameras")
    request = RevitHost(session, client=object(), expected_path="demo.rvt").request(action="snapshot")
    assert request["backgroundDocument"] and request["camerasOnly"] and request["compactReply"]
    foreground = RevitHost(session, client=object(), expected_path="demo.rvt", background=False, cameras_only=False).request()
    assert "backgroundDocument" not in foreground and "camerasOnly" not in foreground


@pytest.mark.parametrize("case, depth", [("C-create", 1), ("D-live-nested-create", 2)])
def test_datacentre_create_resolves_space_and_transformed_mount(tmp_path, case, depth):
    session = fixture(tmp_path)
    host = RevitHost(session, client=object(), expected_path="demo.rvt")
    raw = native_camera_receipt()
    for row in raw["touched"]:
        row["levelBinding"] = dict(ref="level-ref", localRef="1", document="project-id", version="v1", ifcGuid="000000000010200000000B")
    # Publish a native level, then attach the seed's real space under it.
    baseline = host.normalize(raw)
    result, _ = session.ensure_host("revit")
    publish(session, baseline, result, "revit", baseline["version"], "project-id")
    level_path = baseline["touched"][0]["levelBinding"]["path"]
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        session.stage.DefinePrim(level_path + "/Room", "AecoSpace")
    expected = author(session, baseline, case, "datacentre")
    assert expected["spaceDepth"] == depth
    context = camera_creation_context(session.stage, expected["path"])
    assert context["levelPath"] == level_path
    prim = session.stage.GetPrimAtPath(expected["path"])
    if depth == 2:
        assert prim.GetParent().GetName() == "CameraMount"
        assert list(UsdGeom.Xformable(prim.GetParent()).GetLocalTransformation().ExtractTranslation()) == [.2, .3, .4]
    edits = collect(session.stage, session.intent, session.current())
    assert len(edits) == 1 and edits[0].operation == "create"
    assert edits[0].path == expected["path"]


def test_case_sets_select_without_comment_tags():
    receipt = native_camera_receipt()
    receipt["touched"][0]["tag"] = ""
    assert len(camera_rows(receipt)) == 3
    assert len(camera_rows(receipt, "datacentre")) == 4
    assert len(CASES) == 9 and len(DC_CASES) == 12


@pytest.mark.parametrize("cases", [["unknown"], ["C-create", "C-create"], []])
def test_invalid_case_selection_never_contacts_host(tmp_path, cases):
    with pytest.raises(ValueError, match="unique cases"):
        run_suite("missing.usda", tmp_path, cases=cases, client=object())


def test_repeated_readback_keeps_native_symbol_bound_to_catalog_class(tmp_path):
    session = fixture(tmp_path)
    host = RevitHost(session, client=object(), expected_path="demo.rvt")
    baseline = host.normalize(native_camera_receipt())
    layer, _ = session.ensure_host("revit")
    publish(session, baseline, layer, "revit", baseline["version"], "project-id")
    for _ in range(2):
        host = RevitHost(session, client=object(), expected_path="demo.rvt")
        normalized = host.normalize(native_camera_receipt())
        assert [r["type"]["path"] for r in normalized["touched"]] == [r["type"]["path"] for r in baseline["touched"]]
        assert all(not p.endswith("/Sensor_0") for p in host.bindings() if session.stage.GetPrimAtPath(p).IsAbstract())
        publish(session, normalized, layer, "revit", normalized["version"], "project-id")
    assert not session.stage.GetPrimAtPath(baseline["touched"][0]["type"]["path"]).HasAPI("AecoCctvSensorAPI")


def test_invalid_native_threshold_keeps_other_optical_comparisons():
    from aeco_sync.cctv import normalize_revit_camera
    from usdaeco_revit._cctv_convergence import compare_camera
    from test_cctv import native_camera
    row = normalize_revit_camera(native_camera())
    row["sensors"][0]["fov"][0]["parameters"]["T_Res_UD"] = 0
    result = compare_camera(row, row)
    assert not result["converged"]
    assert len(result["comparisons"]) == 6
    assert any("T_Res_UD must be finite and positive" in v for v in result["missing"])


def test_empty_native_intent_applies_without_transport(tmp_path, monkeypatch):
    from aeco_sync import engine
    session = fixture(tmp_path)
    host = RevitHost(session, client=object(), expected_path="demo.rvt")
    baseline = host.normalize(native_camera_receipt())
    layer, _ = session.ensure_host("revit")
    publish(session, baseline, layer, "revit", baseline["version"], "project-id")
    calls = []
    class NoExchange:
        def exchange(self, request):
            calls.append(request)
            raise AssertionError("An empty repeat must not submit a request")
    def unexpected_adapter(*args, **kwargs):
        raise AssertionError("Repeat must reuse the explicitly configured adapter")
    monkeypatch.setattr(engine, "adapter", unexpected_adapter)
    configured = RevitHost(session, client=NoExchange(), expected_path="demo.rvt")
    repeated = engine.apply(session, "revit", native=configured)
    assert repeated["mutations"] == 0 and repeated["pending"] == 0 and not calls


@pytest.mark.parametrize("count, missing", [(6, None), (8, None), (6, "baseline"), (6, "convergence")])
def test_live_datacentre_census_uses_seed_session(tmp_path, count, missing):
    """Exercise both live census assertions with offline native receipts."""
    from aeco_sync.identity import uuid_to_guid
    source = fixture(tmp_path / "seed")
    raw = native_camera_receipt()
    for n in range(len(raw["touched"]) + 1, count + 1):
        row = copy.deepcopy(raw["touched"][0])
        row.update(tag=f"Camera {n}", ref=f"camera-{n}", localRef=str(n),
                   path=f"/Model/Level/Cam_{n}",
                   ifcGuid=uuid_to_guid(f"00000000-0000-4000-8000-{n:012d}"))
        raw["touched"].append(row)
    raw["restoredVersion"] = raw["version"]
    native = RevitHost(source, client=object(), expected_path="demo.rvt")
    baseline = native.normalize(raw)
    layer, _ = source.ensure_host("revit")
    publish(source, baseline, layer, "revit", baseline["version"], "project-id")
    layer.Save()
    # Replace the one-camera IFC fixture with the synthetic native seed.
    with Usd.EditContext(source.stage, source.layer("kind.usda")):
        source.stage.GetPrimAtPath("/Model/Level/Camera").SetActive(False)
    source.layer("kind.usda").Save()

    class OfflineClient:
        def exchange(self, request):
            response = copy.deepcopy(raw)
            phase = "convergence" if request["rollbackOnly"] else "baseline"
            if missing == phase:
                response["touched"].pop()
            return response

    options = dict(client=OfflineClient(), expected_path="demo.rvt", case_set="datacentre",
                   cases=["D-live-converge-all"])
    if missing == "baseline":
        with pytest.raises(ValueError, match=f"expected {count}, got {count - 1}"):
            run_suite(source.path, tmp_path / "run", **options)
        return
    report = run_suite(source.path, tmp_path / "run", **options)
    assert report["expectedCameras"] == report["cameras"] == count
    assert report["censusSource"] == "seedSession"
    case = report["cases"]["D-live-converge-all"]
    assert case["expectedCameras"] == count
    assert report["status"] == ("failed" if missing else "pass"), report
    assert len(case["convergence"]) == count - bool(missing)
    assert not report["fullAcceptance"]  # A smoke subset never claims a live suite pass.
