import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import os
from pathlib import Path
import pytest
from aeco_sync import register_plugins

os.environ.setdefault(
    "AECO_KIND_PLUGIN",
    str(Path(__file__).resolve().parent / "fixtures/usdAecoKindProto"),
)
if (Path(__file__).resolve().parents[2] / "usdaeco-cctv").is_dir():
    os.environ.setdefault("AECO_CCTV_ROOT", str(Path(__file__).resolve().parents[2] / "usdaeco-cctv"))
register_plugins()
from pxr import Usd, UsdGeom, Gf
from aeco_sync.stack import create


@pytest.fixture
def session(tmp_path):
    model = tmp_path / "model.usda"
    stage = Usd.Stage.CreateNew(str(model))
    from pxr import Vt
    stage.SetMetadata("fallbackPrimTypes", {"AecoPort": Vt.TokenArray(["Xform"])})
    prim = UsdGeom.Xform.Define(stage, "/Pipe").GetPrim()
    for api in ("AecoElementAPI", "AecoAxisAPI", "AecoPipeAPI", "AecoPipeTypeAPI"):
        prim.ApplyAPI(api)
    prim.GetAttribute("aeco:id").Set("946ea480-cd43-4abc-9018-85e327fdab24")
    prim.GetAttribute("aeco:axis:end").Set(Gf.Vec3d(0, 0, 2))
    prim.GetAttribute("aeco:axis:length").Set(2)
    prim.GetAttribute("aeco:pipe:outerDiameter").Set(0.05)
    prim.GetAttribute("aeco:pipeType:nominalDiameters").Set([0.05, 0.065])
    prim.GetAttribute("aeco:pipeType:outerDiameters").Set([0.05, 0.0761])
    prim.GetAttribute("aeco:pipeType:innerDiameters").Set([0.044, 0.0697])
    UsdGeom.Xformable(prim).MakeMatrixXform().Set(Gf.Matrix4d(1))
    stage.GetRootLayer().Save()
    document = tmp_path / "baseline.ifc"
    document.write_text("opaque test document")
    return create(model, document)
