"""Run foreground Revit scenarios with commit-time read-back inside rollback groups.

Each result layer is disposable evidence of the tentative native state. The
native document is restored in the same C# evaluation, before any reply paging.
Transport failure stops the suite immediately, without retries or restarts.
"""

import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import ifcopenshell
from pxr import Gf, Usd, UsdGeom

from aeco_sync.closure import plan
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.edits import collect, preflight, clear
from usdaeco_revit.transport import ReplClient
from usdaeco_revit.host import RevitHost, HostRefused, write_export
from usdaeco_revit.convergence import (
    export_port_agreement,
    publish_convergence,
    world_axis,
)
from aeco_sync.readback import bind, publish
from aeco_sync.stack import Session, create, facts, find_layer, value

CASES = (
    "P-free",
    "P-conn-keep",
    "P-size-ok",
    "P-size-bad",
    "P-disconnect",
    "W-joined",
    "W-neighbour",
    "W-type",
    "W-flip",
    "W-door",
    "X-converge",
)
EXPECTED = {
    "P-size-bad": "pipeSizeNotInTable",
    "W-joined": "sync:joinedEnd",
    "P-conn-keep": "sync:neighbourMoved",
    "W-neighbour": "sync:neighbourMoved",
}


def clone_session(source, directory, *, preserve_inherits=False):
    directory.mkdir(parents=True, exist_ok=False)
    model = directory / "model.usda"
    if preserve_inherits:
        from pxr import UsdUtils
        UsdUtils.FlattenLayerStack(source.current()).Export(str(model))
    else:
        source.current().Flatten().Export(str(model))
    seed = Path(source.document("ifc"))
    if not seed.is_file():
        raise ValueError("The seed session must retain its original IFC artifact")
    shutil.copyfile(seed, directory / "seed.ifc")
    session = create(model, directory / "seed.ifc", directory, source.policy)
    result, _ = session.ensure_host("revit")
    old_result = find_layer(source.stage, "result.revit.usda")
    if old_result and old_result.customLayerData:
        result.customLayerData = dict(old_result.customLayerData)
        result.Save()
    return session


def rows(receipt):
    return {r["tag"]: r for r in receipt["touched"] if r.get("tag")}


def fixture_guard(baseline):
    named = rows(baseline)
    required = {
        "P1b": "pipe",
        "P2": "pipe",
        "WA": "wall",
        "WB": "wall",
        "D1": "element",
    }
    if any(k not in named or named[k]["kind"] != kind for k, kind in required.items()):
        raise ValueError(
            "Foreground fixture lacks the required P1b, P2, WA, WB, D1 element tags/kinds"
        )
    if not named["WA"]["joins"].get("aeco:wall:joinAtEnd"):
        raise ValueError("WA must have its end joined to WB")
    if named["D1"].get("hostRef") != named["WA"]["ref"]:
        raise ValueError("D1 must be hosted by WA")
    if len(named["P1b"]["ports"]) != 2 or not all(
        p["connected"] for p in named["P1b"]["ports"]
    ):
        raise ValueError("P1b must be connected at both ends")
    if not any(
        abs(r["nominal"] - 0.065) < 1e-9 for r in named["P1b"].get("sizeTable", [])
    ):
        raise ValueError("The active P1b size table must contain DN65")
    if any(abs(r["nominal"] - 0.033) < 1e-9 for r in named["P1b"].get("sizeTable", [])):
        raise ValueError("The fixture must exclude the deliberately invalid 33 mm size")
    return named


def author_case(session, baseline, case):
    named = fixture_guard(baseline)
    stage = session.stage
    session.capture_base()

    def attr(tag, name, val):
        stage.GetPrimAtPath(named[tag]["path"]).GetAttribute(name).Set(val)

    def extend(tag, amount):
        d = named[tag]["drivers"]
        start, end = Gf.Vec3d(d["aeco:axis:start"]), Gf.Vec3d(d["aeco:axis:end"])
        attr(tag, "aeco:axis:end", end + (end - start).GetNormalized() * amount)

    def move_neighbour():
        m = Gf.Matrix4d(named["WB"]["matrix"])
        m.SetTranslateOnly(m.ExtractTranslation() + Gf.Vec3d(1, 0, 0))
        prim = stage.GetPrimAtPath(named["WB"]["path"])
        parent = UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
        prim.GetAttribute("xformOp:transform").Set(m * parent.GetInverse())

    with Usd.EditContext(stage, session.intent):
        if case == "P-free":
            pipe = named["P2"]
            endpoint = np.array(pipe["drivers"]["aeco:axis:end"])
            port = min(
                pipe["ports"],
                key=lambda p: np.linalg.norm(np.array(p["matrix"])[3, :3] - endpoint),
            )
            if port["connected"]:
                raise ValueError("P2's end must be free")
            extend("P2", 0.25)
        elif case == "P-conn-keep":
            extend("P1b", 0.25)
        elif case in ("P-size-ok", "P-size-bad"):
            attr(
                "P1b",
                "aeco:pipe:nominalDiameter",
                0.065 if case.endswith("ok") else 0.033,
            )
        elif case == "P-disconnect":
            stage.GetPrimAtPath(named["P1b"]["ports"][-1]["path"]).GetRelationship(
                "aeco:connectedPorts"
            ).SetTargets([])
        elif case == "W-joined":
            extend("WA", 0.25)
        elif case == "W-neighbour":
            move_neighbour()
        elif case == "W-type":
            wall = named["WA"]
            candidates = [
                t
                for t in baseline["wallTypes"]
                if t["ref"] != wall["type"]["ref"]
                and abs(t["width"] - wall["derived"]["aeco:wall:thickness"]) > 1e-6
            ]
            if not candidates:
                raise ValueError(
                    "Fixture needs an alternate basic wall type with a different width"
                )
            typ = sorted(candidates, key=lambda t: t["width"])[0]
            path = "/_Types/ScenarioWallType"
            with Usd.EditContext(stage, session.layer("kind.usda")):
                catalog = stage.CreateClassPrim(path)
                catalog.ApplyAPI("AecoTypeAPI")
                bind(
                    catalog,
                    "revit",
                    typ["ref"],
                    typ["localRef"],
                    typ["version"],
                    typ["document"],
                )
            stage.GetPrimAtPath(wall["path"]).GetInherits().SetInherits([path])
        elif case == "W-flip":
            attr(
                "WA",
                "aeco:wall:flipped",
                not named["WA"]["drivers"]["aeco:wall:flipped"],
            )
        elif case == "W-door":
            wall = named["WA"]
            start, end = [
                Gf.Vec3d(wall["drivers"]["aeco:axis:" + p]) for p in ("start", "end")
            ]
            target = end - (end - start).GetNormalized() * 0.5
            door = Gf.Matrix4d(named["D1"]["matrix"]).ExtractTranslation()
            local_door = Gf.Matrix4d(wall["matrix"]).GetInverse().Transform(door)
            if local_door[0] >= target[0]:
                raise ValueError("Shortening WA would not pass the tagged door")
            attr("WA", "aeco:axis:start", target)
        elif case == "X-converge":
            extend("P1b", 0.5)
            attr("P1b", "aeco:pipe:nominalDiameter", 0.065)
            attr("P1b", "aeco:pipe:outerDiameter", 0.09)  # S4's derived refusal
            extend("WA", 0.5)  # S4's joined-end refusal
            move_neighbour()
        else:
            raise ValueError(case)
    session.intent.Save()


def assert_case(case, baseline, receipt, report):
    before, after = rows(baseline), rows(receipt) if receipt else {}
    codes = [d["code"] for d in report["diagnostics"]]
    if case in EXPECTED:
        assert EXPECTED[case] in codes, report
    if case in ("P-size-bad", "W-joined"):
        assert (
            report["accepted"] == 0
            and report["pending"] == 1
            and report["requests"] == 0
        ), report
        return
    if case == "W-door":
        errors = [
            d
            for d in report["diagnostics"]
            if d["severity"] == "error" and d.get("definitionId")
        ]
        assert errors and any(
            d.get("resolutions") and len(d.get("failingIds", [])) >= 2 for d in errors
        ), report
        assert (
            report["accepted"] == 0
            and report["pending"] == 1
            and report["rollbackVerified"]
        ), report
        return
    assert report["accepted"] > 0 and report["rollbackVerified"], report
    if case in ("P-free", "P-conn-keep"):
        tag = "P2" if case == "P-free" else "P1b"
        assert after[tag]["derived"]["aeco:axis:length"] == pytest_approx(
            before[tag]["derived"]["aeco:axis:length"] + 0.25
        ), report
    elif case == "P-size-ok":
        assert (
            abs(after["P1b"]["drivers"]["aeco:pipe:nominalDiameter"] - 0.065) < 1e-9
        ), report
        assert (
            len([r for r in receipt["touched"] if r.get("origin") == "generated"]) >= 1
        ), report
    elif case == "P-disconnect":
        reference = before["P1b"]["ports"][-1]["ref"]
        assert not next(p for p in after["P1b"]["ports"] if p["ref"] == reference)[
            "connected"
        ], report
    elif case == "W-neighbour":
        assert (
            np.max(np.abs(world_axis(after["WA"]) - world_axis(before["WA"]))) > 0.1
        ), report
    elif case == "W-type":
        assert (
            abs(
                after["WA"]["derived"]["aeco:wall:thickness"]
                - before["WA"]["derived"]["aeco:wall:thickness"]
            )
            > 1e-6
        ), report
        assert after["WA"]["joins"] == before["WA"]["joins"], report
    elif case == "W-flip":
        assert (
            after["WA"]["drivers"]["aeco:wall:flipped"]
            != before["WA"]["drivers"]["aeco:wall:flipped"]
        ), report
    elif case == "X-converge":
        assert report["accepted"] == 3 and report["pending"] == 2, report
        assert "sync:derivedAuthored" in codes and "sync:joinedEnd" in codes, report


def pytest_approx(number):
    # pytest is part of the acceptance environment; keep assertions readable.
    import pytest

    return pytest.approx(number, abs=1e-4)


def run_case(source, baseline, directory, case, client, expected_path):
    session = clone_session(source, directory)
    author_case(session, baseline, case)
    native = RevitHost(session, client=client, expected_path=expected_path)
    diag = Diagnostics()
    edits = collect(session.stage, session.intent, session.current())
    accepted, noops = preflight(session, edits, "revit", native.version(), diag)
    accepted, closure = plan(session, accepted, diag)
    assert all(
        native.supports(e) for e in accepted
    ), "Scenario contains an unsupported operation"
    receipt, requests, rollback_verified = None, 0, True
    if accepted:
        requests += 1
        try:
            receipt = native.exchange(
                native.request(
                    accepted,
                    closure,
                    rollback_only=True,
                    export_ifc=case == "X-converge",
                )
            )
            rollback_verified = receipt["rollbackVerified"]
        except HostRefused:
            accepted = []
            rollback_verified = native.raw_receipt.get("rollbackVerified", False)
    diag.items.extend(native.diagnostics())
    result, dlayer = session.ensure_host("revit")
    clear(session.intent, accepted + noops)
    convergence = None
    if receipt:
        if case == "X-converge":
            export = write_export(receipt, directory / "export.ifc")
            agreement = export_port_agreement(receipt, ifcopenshell.open(str(export)))
            assert (
                agreement["matched"] == agreement["total"] and agreement["total"] > 0
            ), agreement
            for item in receipt["touched"]:
                for port in item.get("ports", []):
                    port.update(agreement["resolved"][port["ref"]])
            diag.items = [
                d for d in diag.items if d["code"] != "revit:portIdentityUnresolved"
            ]
        publish(session, receipt, result, "revit", receipt["version"], native.document)
        if case == "X-converge":
            convergence = publish_convergence(session, receipt, export)
            diag.items.extend(convergence["diagnostics"])
    diag.write(
        session,
        dlayer,
        "revit",
        receipt["version"] if receipt else native.version(),
        native.document,
    )
    for layer in (
        session.intent,
        result,
        dlayer,
        session.layer("derived.usda"),
        session.layer("kind.usda"),
    ):
        layer.Save()
    report = {
        "accepted": len(accepted),
        "pending": session.status()["pending"],
        "requests": requests,
        "rollbackVerified": rollback_verified,
        "diagnostics": diag.items,
        "generated": (
            len([r for r in receipt["touched"] if r.get("origin") == "generated"])
            if receipt
            else 0
        ),
    }
    if convergence:
        report["convergence"] = convergence
    assert_case(case, baseline, receipt, report)
    if convergence:
        assert convergence["driversConverged"], convergence
    report["status"] = "pass"
    return report


def run_suite(stage_path, output, *, client=None, expected_path=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "cases": {}, "portGuidAgreement": None, "live": True}
    report_path = output / "acceptance.json"
    try:
        source = clone_session(Session(stage_path), output / "baseline")
        client = client or ReplClient()
        native = RevitHost(source, client=client, expected_path=expected_path)
        baseline = native.readback(None)
        fixture_guard(baseline)
        result, _ = source.ensure_host("revit")
        publish(source, baseline, result, "revit", baseline["version"], native.document)
        result.Save()
        source.layer("derived.usda").Save()
        # Baseline port agreement uses an actual independent export, all inside a rollback group.
        exported = native.exchange(
            native.request(action="snapshot", rollback_only=True, export_ifc=True)
        )
        export_path = write_export(exported, output / "baseline" / "export.ifc")
        agreement = export_port_agreement(exported, ifcopenshell.open(str(export_path)))
        report["portGuidAgreement"] = {
            k: v for k, v in agreement.items() if k != "resolved"
        }
        assert agreement["matched"] == agreement["total"] == 13, report[
            "portGuidAgreement"
        ]
        for item in baseline["touched"]:
            for port in item.get("ports", []):
                port.update(agreement["resolved"][port["ref"]])
        publish(source, baseline, result, "revit", baseline["version"], native.document)
        result.Save()
        for case in CASES:
            report["cases"][case] = run_case(
                source, baseline, output / case, case, client, native.expected_path
            )
            report_path.write_text(json.dumps(value(report), indent=2) + "\n")
        report["status"] = "pass"
    except Exception as exc:
        report["status"] = "incomplete"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report_path.write_text(json.dumps(value(report), indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        help="Seed session initialized from the fixture's IFC export",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory for disposable evidence sessions",
    )
    args = parser.parse_args()
    report = run_suite(args.stage, args.output)
    print(json.dumps(value(report), indent=2))


if __name__ == "__main__":
    from aeco_sync import register_plugins

    register_plugins()
    main()
