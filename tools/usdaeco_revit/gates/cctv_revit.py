"""Live camera rollback cases on an exact resident background document.

No document open/activation calls. A fresh native snapshot supplies catalog,
level and element bindings. The transport checks status before every call and
waits at least 120 seconds after a busy status, across processes and reply pages.
"""
import json
import os
import argparse
import tempfile
from pathlib import Path
from pxr import Gf, Usd, UsdGeom
from aeco_sync.cctv import SENSOR, PRESET, is_sensor, set_values
from aeco_sync.closure import plan
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.edits import collect, preflight, clear
from usdaeco_revit.host import RevitHost, HostRefused
from usdaeco_revit.transport import ReplClient
from usdaeco_revit.convergence import compare_receipts
from aeco_sync.readback import publish
from aeco_sync.stack import Session, value
from .revit import clone_session

# Exercise level/type bindings first; every case starts from the same baseline.
CASES = ("C-create", "C-move", "C-pan-tilt", "C-zoom-clamped", "C-preset", "C-type-swap", "C-delete", "C-derived-authored", "X-converge-cctv")

DC_CASES = CASES + ("D-live-nested-create", "D-live-repeat", "D-live-converge-all")


def camera_rows(receipt, case_set="c4"):
    return sorted([r for r in receipt["touched"] if r.get("kind") == "camera" and r.get("active") is not False
                   and (case_set == "datacentre" or r.get("tag") in ("Cam_1", "Cam_2", "Cam_3", "Cam_4"))], key=lambda r: (r.get("typeName", ""), r.get("tag", ""), r["ref"]))


def author(session, baseline, case, case_set="c4"):
    rows = camera_rows(baseline, case_set)
    row = next((r for r in rows if bool(r["sensors"][0].get("presets")) == (case == "C-preset")), rows[0])
    if case_set == "datacentre" and case != "C-preset":
        row = next((r for r in rows if "dome" in r.get("typeName", "").lower()), row)
    camera = session.stage.GetPrimAtPath(row["path"])
    sensor = camera.GetChild(row["sensors"][0]["name"])
    # Capture drivers for this camera, excluding full-facility mesh arrays.
    session.capture_base([str(a.GetPath()) for p in Usd.PrimRange(camera)
                          for a in p.GetAttributes() if a.GetName().startswith((SENSOR, PRESET, "xformOp:"))
                          and not a.GetMetadata("aecoDerived")])
    expected = dict(path=row["path"], attributes={})
    def attr(prim, name, val):
        prim.GetAttribute(name).Set(val)
        expected["attributes"][str(prim.GetPath()) + "." + name] = val
    with Usd.EditContext(session.stage, session.intent):
        if case == "D-live-converge-all":
            pass
        elif case in ("C-move", "D-live-repeat"):
            old = UsdGeom.Xformable(camera).GetLocalTransformation()
            matrix = Gf.Matrix4d(old) * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1), 10))
            matrix.SetTranslateOnly(old.ExtractTranslation() + Gf.Vec3d(.1,0,.1))
            UsdGeom.Xformable(camera).MakeMatrixXform().Set(matrix)
            expected["matrix"] = matrix
        elif case == "C-pan-tilt":
            for field in ("pan", "tilt"):
                lo, hi = sensor.GetAttribute(SENSOR + field + "Range").Get()
                target = (lo + hi) / 2 if (lo,hi) != (0,0) else 15.
                if target == sensor.GetAttribute(SENSOR + field).Get():
                    target += 1.
                attr(sensor, SENSOR + field, target)
        elif case == "C-zoom-clamped":
            attr(sensor, SENSOR + "focalLength", sensor.GetAttribute(SENSOR + "focalRange").Get()[1] + 1.)
        elif case == "C-derived-authored":
            attr(sensor, "focalLength", sensor.GetAttribute("focalLength").Get() + 1.)
        elif case == "C-preset":
            if not row["sensors"][0].get("presets"):
                raise ValueError("Fixture must contain a PTZ camera with presets")
            name = next(iter(row["sensors"][0]["presets"]))
            attr(sensor, PRESET + name + ":pan", 20.)
            attr(sensor, PRESET + name + ":tilt", 30.)
            attr(sensor, PRESET + name + ":focalLength", sensor.GetAttribute(SENSOR + "focalRange").Get()[0])
        elif case == "C-type-swap":
            alternate = next((r for r in rows if r["type"]["ref"] != row["type"]["ref"] and (case_set == "c4" or r["sensors"][0].get("presets"))), None)
            if not alternate:
                raise ValueError("Fixture must contain two camera types")
            target = session.stage.GetPrimAtPath(alternate["type"]["path"]).GetChild("Sensor_0")
            # Set zoom to a value in both envelopes so ordering cannot clamp it.
            old_range, new_range = sensor.GetAttribute(SENSOR + "focalRange").Get(), target.GetAttribute(SENSOR + "focalRange").Get()
            lo, hi = max(old_range[0], new_range[0]), min(old_range[1], new_range[1])
            if lo > hi:
                raise ValueError("Fixture needs overlapping zoom envelopes for its type-swap case")
            attr(sensor, SENSOR + "focalLength", (lo + hi)/2)
            camera.GetInherits().SetInherits([alternate["type"]["path"]])
            expected["type"] = alternate["type"]["ref"]
        elif case in ("C-create", "D-live-nested-create"):
            parent = camera.GetParent()
            if case_set == "datacentre":
                # The native export contains rooms, but camera containment may
                # point at a storey. Exercise a real space on that same level.
                from usdaeco_revit.identity import camera_creation_context
                current = session.current()
                spaces = [p for p in current.Traverse() if p.GetTypeName() == "AecoSpace"]
                parent_path = next((p.GetPath() for p in spaces
                    if camera_creation_context(session.stage, str(p.GetPath()) + "/Probe")["levelPath"] == row["levelBinding"]["path"]), None)
                if parent_path is None:
                    raise ValueError("Seed needs a space beneath the camera's bound native level")
                if case == "D-live-nested-create":
                    # This grouping transform is fixture context, never host intent.
                    parent_path = parent_path.AppendChild("CameraMount")
                    with Usd.EditContext(session.stage, session.layer("kind.usda")):
                        mount = UsdGeom.Xform.Define(session.stage, parent_path)
                        mount.MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(.2, .3, .4)))
                    session.layer("kind.usda").Save()
                parent = session.stage.GetPrimAtPath(parent_path)
                expected["level"] = row["levelBinding"]["ref"]
                expected["spaceDepth"] = 2 if case == "D-live-nested-create" else 1
            path = parent.GetPath().AppendChild("SyncCameraCreated")
            new = UsdGeom.Xform.Define(session.stage, path).GetPrim()
            new.ApplyAPI("AecoElementAPI"); new.ApplyAPI("AecoCctvCameraAPI")
            new.GetInherits().SetInherits([row["type"]["path"]])
            cache = UsdGeom.XformCache()
            world = cache.GetLocalToWorldTransform(camera)
            world.SetTranslateOnly(world.ExtractTranslation() + Gf.Vec3d(.5,0,0))
            matrix = world * cache.GetLocalToWorldTransform(parent).GetInverse()
            UsdGeom.Xformable(new).MakeMatrixXform().Set(matrix)
            for s in row["sensors"]:
                set_values(new.GetChild(s["name"]), s["drivers"])
            expected.update(path=str(path), matrix=matrix, sensors=row["sensors"])
        elif case == "C-delete":
            camera.SetActive(False)
        elif case == "X-converge-cctv":
            lo, hi = sensor.GetAttribute(SENSOR + "focalRange").Get()
            attr(sensor, SENSOR + "focalLength", (lo+hi)/2)
    session.intent.Save()
    return expected


def run_suite(stage_path, output, *, client=None, expected_path=None, case_set=None, cases=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    case_set = case_set or os.environ.get("AECO_REVIT_CASE_SET", "c4")
    if case_set not in ("c4", "datacentre"):
        raise ValueError("Unknown camera case set: " + case_set)
    selected = tuple(cases) if cases is not None else (DC_CASES if case_set == "datacentre" else CASES)
    allowed = DC_CASES if case_set == "datacentre" else CASES
    if not selected or len(set(selected)) != len(selected) or any(c not in allowed for c in selected):
        raise ValueError("Select unique cases from the requested camera case set")
    report = dict(status="running", live=True, fullAcceptance=False, caseSet=case_set, cases={})
    try:
        source = clone_session(Session(stage_path), output / "baseline", preserve_inherits=True)
        seed_stage = source.current()
        expected_cameras = (sum(p.HasAPI("AecoCctvCameraAPI") for p in seed_stage.Traverse())
                            if case_set == "datacentre" else 4)
        if not expected_cameras:
            raise ValueError("Camera seed contains no active cameras; publish native camera read-back before running the gate")
        report.update(expectedCameras=expected_cameras,
                      censusSource="seedSession" if case_set == "datacentre" else "c4Fixture")
        print(f"== stage: {case_set} live seed expects {expected_cameras} cameras", flush=True)
        client = client or ReplClient()
        options = dict(background=True, cameras_only=case_set == "datacentre")
        native = RevitHost(source, client=client, expected_path=expected_path, **options)
        baseline = native.readback(None)
        rows = camera_rows(baseline, case_set)
        if len(rows) != expected_cameras or len({r["ref"] for r in rows}) != expected_cameras:
            raise ValueError(f"Native camera census disagrees with seed: expected {expected_cameras}, got {len(rows)} rows / {len({r['ref'] for r in rows})} unique cameras")
        if not any(s.get("presets") for r in rows for s in r.get("sensors", [])):
            raise ValueError("Camera seed needs a PTZ camera with presets and catalog optics")
        report.update(cameras=len(rows), baselineVersion=baseline["version"])
        (output / "baseline/receipt.revit.json").write_text(json.dumps(value(baseline), indent=2) + "\n")
        result, _ = source.ensure_host("revit")
        publish(source, baseline, result, "revit", baseline["version"], native.document)
        result.Save(); source.layer("derived.usda").Save()
        for case in selected:
            session = clone_session(source, output / case, preserve_inherits=True)
            print("== stage: " + case + " [revit rollback]", flush=True)
            host = RevitHost(session, client=client, expected_path=native.expected_path, **options)
            diag = Diagnostics()
            entry = dict(accepted=0, pending=0, requests=0, rollbackVerified=False, expectedCameras=expected_cameras)
            try:
                expected = author(session, baseline, case, case_set)
                edits = collect(session.stage, session.intent, session.current())
                accepted, noops = preflight(session, edits, "revit", host.version(), diag)
                accepted, closure = plan(session, accepted, diag)
                assert all(host.supports(e) for e in accepted), case
                entry["accepted"] = len(accepted)
                if case in ("C-zoom-clamped", "C-derived-authored"):
                    code = "cctvOutOfEnvelope" if case == "C-zoom-clamped" else "sync:derivedAuthored"
                    assert not accepted and [d["code"] for d in diag.items] == [code]
                    entry.update(rollbackVerified=True, rollbackEvidence="No native request submitted")
                else:
                    assert accepted or case == "D-live-converge-all", case
                    entry["requests"] = 1
                    receipt = host.exchange(host.request(accepted, closure, rollback_only=True,
                        action="snapshot" if case == "D-live-converge-all" else "sync"))
                    entry["mutations"] = len(accepted)
                    entry["beforeVersion"] = baseline["version"]
                    entry["restoredVersion"] = receipt.get("restoredVersion")
                    if case_set == "datacentre":
                        assert receipt.get("restoredVersion") == baseline["version"]
                    entry.update(requests=1, rollbackVerified=receipt["rollbackVerified"], rollbackEvidence="Native fingerprint restored inside TransactionGroup")
                    assert not any(d["blocking"] for d in diag.items + host.diagnostics()), host.diagnostics()
                    clear(session.intent, accepted + noops)
                    result, _ = session.ensure_host("revit")
                    publish(session, receipt, result, "revit", receipt["version"], host.document)
                    current = session.current()
                    cp = current.GetPrimAtPath(expected["path"])
                    if case == "C-delete":
                        assert cp and not cp.IsActive()
                    else:
                        assert cp and cp.IsActive()
                        for path, val in expected["attributes"].items():
                            actual = current.GetAttributeAtPath(path).Get()
                            assert abs(actual - val) <= 1e-6, (path, actual, val)
                        if "matrix" in expected:
                            assert Gf.IsClose(UsdGeom.Xformable(cp).GetLocalTransformation(), expected["matrix"], 1e-6)
                        if "type" in expected:
                            actual = next(r for r in receipt["touched"] if r["path"] == expected["path"])
                            assert actual["type"]["ref"] == expected["type"]
                        if "sensors" in expected:
                            for s in expected["sensors"]:
                                for name,val in s["drivers"].items():
                                    assert abs(cp.GetChild(s["name"]).GetAttribute(name).Get() - val) <= 1e-6
                    if "level" in expected:
                        actual = next(r for r in receipt["touched"] if r.get("path") == expected["path"])
                        assert actual["levelBinding"]["ref"] == expected["level"]
                        entry.update(levelResolved=True, spaceDepth=expected["spaceDepth"])
                    if case == "D-live-repeat":
                        from aeco_sync import engine
                        calls = getattr(client, "calls", 0)
                        repeated = engine.apply(session, "revit", native=host)
                        assert repeated["mutations"] == 0 and repeated["pending"] == 0, repeated
                        assert getattr(client, "calls", 0) == calls
                        entry.update(repeatMutations=repeated["mutations"], repeatAccepted=repeated["accepted"], repeatRequests=0)
                    if case in ("X-converge-cctv", "D-live-converge-all"):
                        comparison = compare_receipts(receipt, receipt)
                        entry["convergence"] = comparison["cameras"]
                        assert comparison["cctvConverged"], comparison
                        if case == "D-live-converge-all":
                            assert len(comparison["cameras"]) == expected_cameras, (len(comparison["cameras"]), expected_cameras)
                    result.Save(); session.layer("derived.usda").Save()
                entry["pending"] = session.status()["pending"]
                assert entry["pending"] == (1 if case in ("C-zoom-clamped", "C-derived-authored") else 0)
                entry["status"] = "pass"
            except Exception as exc:
                entry.update(status="refused" if isinstance(exc, HostRefused) else "failed",
                             error=f"{type(exc).__name__}: {exc}")
            finally:
                # exchange() retains native findings even when it raises HostRefused.
                diag.items.extend(host.diagnostics())
                raw = getattr(host, "raw_receipt", None)
                if raw is not None:
                    (output / case / "receipt.revit.json").write_text(json.dumps(value(raw), indent=2) + "\n")
                    entry["rollbackVerified"] = bool(raw.get("rollbackVerified"))
                    if entry["rollbackVerified"]:
                        entry["rollbackEvidence"] = "Native fingerprint restored inside TransactionGroup"
                entry.update(pending=session.status()["pending"], diagnostics=diag.items,
                             codes=[d["code"] for d in diag.items])
                _, layer = session.ensure_host("revit")
                diag.write(session, layer, "revit", (raw or {}).get("version", host.version()), host.document)
                layer.Save()
                report["cases"][case] = entry
                print(entry["status"].upper() + " " + case + " [revit rollback]", flush=True)
                (output / "acceptance.json").write_text(json.dumps(value(report), indent=2) + "\n")
        passed = len(report["cases"]) == len(selected) and all(e["status"] == "pass" for e in report["cases"].values())
        report.update(status="pass" if passed else "failed", fullAcceptance=passed and set(selected) == set(allowed))
    except Exception as exc:
        report.update(status="incomplete", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        (output / "acceptance.json").write_text(json.dumps(value(report), indent=2) + "\n")
    return report


def main(argv=None):
    from aeco_sync import register_plugins
    register_plugins()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=os.environ.get("AECO_REVIT_STAGE"))
    parser.add_argument("--document", default=os.environ.get("AECO_REVIT_DOCUMENT"))
    parser.add_argument("--endpoint", default=os.environ.get("AECO_REVIT_ENDPOINT"))
    parser.add_argument("--case-set", choices=("c4", "datacentre"), default=os.environ.get("AECO_REVIT_CASE_SET", "c4"))
    parser.add_argument("--cases", nargs="+", help="Optional smoke subset; never full acceptance")
    parser.add_argument("--output", type=Path, default=Path(".work/dc-live.revit"))
    args = parser.parse_args(argv)
    if not args.stage or not args.document:
        parser.error("--stage and --document (or their AECO_REVIT variables) are required")
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=args.output))
    print("== stage: live camera evidence: " + str(output), flush=True)
    if not args.endpoint:
        (output / "acceptance.json").write_text(json.dumps(dict(status="not-run", fullAcceptance=False, reason="Endpoint unset")) + "\n")
        print("Live camera acceptance NOT RUN: endpoint unset")
        return 0
    result = run_suite(args.stage, output, client=ReplClient(args.endpoint), expected_path=args.document,
                       case_set=args.case_set, cases=args.cases)
    print(f"{sum(e['status'] == 'pass' for e in result['cases'].values())}/{len(result['cases'])} live cases passed")
    return int(result["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
