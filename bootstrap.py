"""Run from source without installing into a shared Python environment."""
import os
from pathlib import Path
import sys
import tomllib
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent

def setup():
    paths = [ROOT, ROOT / "tools"]
    for variable, sibling in (("AECO_SYNC_ROOT", "usdaeco-sync"),
                              ("AECO_CORE_ROOT", "usdaeco-core"),
                              ("AECO_AXIS_ROOT", "usdaeco-axis"),
                              ("AECO_IFC_ROOT", "usdaeco-ifc"),
                              ("TOOLCHAIN_DIR", "usdaeco-toolchain")):
        root = Path(os.environ.get(variable, ROOT.parent / sibling))
        paths.extend([root, root / "tools"])
    for path in reversed(paths):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    metadata_root = ROOT / "out" / "source-metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    for root in (ROOT, Path(os.environ.get("AECO_IFC_ROOT", ROOT.parent / "usdaeco-ifc"))):
        manifest = root / "pyproject.toml"
        if not manifest.is_file():
            continue
        project = tomllib.loads(manifest.read_text())["project"]
        metadata = metadata_root / (project["name"].replace("-", "_") + "-" + project["version"] + ".dist-info")
        metadata.mkdir(exist_ok=True)
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: " + project["name"] + "\nVersion: " + project["version"] + "\n")
        entries = project["entry-points"]["aeco_sync.hosts"]
        (metadata / "entry_points.txt").write_text("[aeco_sync.hosts]\n" + "".join(f"{name} = {value}\n" for name, value in entries.items()))
    sys.path.insert(0, str(metadata_root))
    os.environ.setdefault("AECO_KIND_PLUGIN", str(ROOT / "testenv/fixtures/usdAecoKindProto"))
setup()
from usdaeco_revit.plugins import install_sync_loader
install_sync_loader()
