"""Core/axis registration bridge for the pinned sync v0.5.0 loader.

Only plugin discovery and the integration's declared core/axis bounds change.
The sync engine, schemas, optional camera bounds and registered metadata stay
untouched. Sync releases with their own updated loader use it directly.
"""
import json
import os
from pathlib import Path
import sys


def _resource(root, library, variable=None):
    if variable and os.environ.get(variable):
        candidates = [Path(os.environ[variable])]
    else:
        candidates = [root / "out" / "plugins" / library / "resources",
                      root / library, root / "plugins" / library / "resources"]
    for directory in candidates:
        if (directory / "plugInfo.json").is_file():
            return directory.resolve()
    raise RuntimeError("Build " + library + " or select its plugin directory")


def _metadata(directory):
    lines = (directory / "plugInfo.json").read_text().splitlines()
    return json.loads("\n".join(line for line in lines if not line.lstrip().startswith("#")))["Plugins"][0]["Info"]["aeco"]


def register_plugins(core=None, kind=None):
    """Load core first, axis second; verify selected and active versions."""
    import aeco_sync
    from aeco_sync.requirements import check_requirements
    from pxr import Plug

    repo = Path(aeco_sync.__file__).resolve().parent.parent
    core_root = Path(core or os.environ.get("AECO_CORE", os.environ.get("AECO_CORE_ROOT", repo.parent / "usdaeco-core")))
    axis_root = Path(os.environ.get("AECO_AXIS_ROOT", repo.parent / "usdaeco-axis"))
    # An explicit core argument takes precedence over environment selection.
    core_plugin = _resource(core_root, "usdAeco", None if core else "CORE_PLUGIN_DIR")
    axis_plugin = _resource(axis_root, "usdAecoAxis", "AXIS_PLUGIN_DIR")
    root = repo if (repo / "usdAecoSync/plugInfo.json").is_file() else Path(sys.prefix) / "share/usdaeco-sync"
    sync_plugin = _resource(root, "usdAecoSync")
    sync_metadata = _metadata(sync_plugin)
    # This integration explicitly supports the unchanged sync protocol on the
    # split core/axis pair. Never rewrite the dependency plugin's descriptor.
    requirements = dict(sync_metadata["requires"])
    requirements.update(usdAeco=">=0.9,<1.0", usdAecoAxis=">=0.1,<0.2", usdAecoSync=">=0.5,<0.6")
    contract = dict(sync_metadata, requires=requirements)
    available = {"usdAeco": _metadata(core_plugin), "usdAecoAxis": _metadata(axis_plugin),
                 "usdAecoSync": sync_metadata}
    check_requirements(contract, available)
    registry = Plug.Registry()
    registry.RegisterPlugins(str(core_plugin))
    registry.RegisterPlugins(str(axis_plugin))
    kind = kind or os.environ.get("AECO_KIND_PLUGIN")
    if kind:
        registry.RegisterPlugins(str(kind))
    cctv = os.environ.get("AECO_CCTV_ROOT")
    if not cctv and (repo.parent / "usdaeco-cctv/plugins/usdAecoCctv/resources/plugInfo.json").is_file():
        cctv = str(repo.parent / "usdaeco-cctv")
    if cctv:
        cctv = Path(cctv).resolve()
        plugin = _resource(cctv, "usdAecoCctv")
        available["usdAecoCctv"] = _metadata(plugin)
        check_requirements(contract, available)
        if str(cctv / "tools") not in sys.path:
            sys.path.insert(0, str(cctv / "tools"))
        registry.RegisterPlugins(str(plugin))
    registry.RegisterPlugins(str(sync_plugin))
    loaded = {name: registry.GetPluginWithName(name).metadata["aeco"]
              for name in available if registry.GetPluginWithName(name)}
    check_requirements(contract, loaded)
    return root


def install_sync_loader():
    """Bridge only sync v0.5.0, before importing its CLI or host modules."""
    import aeco_sync
    if aeco_sync.__version__ == "0.5.0":
        aeco_sync.register_plugins = register_plugins
