#!/usr/bin/env python3
"""Replay an offline source recording inside the complete pinned base facility."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import bootstrap
from aeco_sync import register_plugins
register_plugins()
from pxr import Sdf, Usd, UsdGeom
from usdaeco_check.example import run_example
from usdaeco_revit.replay import audit
from usdaeco_revit.facility_replay import run_replay, resolve_targets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    example = Path(__file__).resolve().parent
    if os.environ.get('AECO_DATACENTRE_STAGE'):
        raise ValueError('This example requires the pinned base release; unset AECO_DATACENTRE_STAGE')
    if os.environ.get('USDRECORD'):
        os.environ['PATH'] = str(Path(os.environ['USDRECORD']).resolve().parent)+os.pathsep+os.environ.get('PATH', '')
    record = json.loads((example/'inputs/offline-receipts.json').read_text())

    def hook(stage, out):
        source = example/'inputs/source/dist/base/dc.usda'
        session, summary = run_replay(source, out, record)
        stage.GetRootLayer().subLayerPaths.insert(0, str(session.path))
        stage.MuteLayer(session.intent.identifier)
        presentation = Sdf.Layer.CreateNew(str(out/'presentation.usda'))
        stage.GetRootLayer().subLayerPaths.insert(0, presentation.identifier)
        with Usd.EditContext(stage, presentation):
            # A roof cutaway exposes the replayed boundary in facility context.
            for prim in stage.Traverse():
                if (prim.GetAttribute('aeco:class:ifc:code').Get() or '') == 'IfcSlab.ROOF':
                    UsdGeom.Imageable(prim).GetVisibilityAttr().Set('invisible')
            for name, prim in resolve_targets(stage).items():
                mesh = UsdGeom.Mesh(prim.GetChild('Geom'))
                mesh.GetDisplayColorAttr().Set([(.02, .75, .9) if name == 'wall' else (1., .22, .015)])
                # Display the recorded tessellation once, with guides retained.
                UsdGeom.Imageable(prim.GetChild('Proxy')).GetVisibilityAttr().Set('invisible')
        presentation.Save()
        return [audit(example/'inputs/recording.json'), summary]

    return run_example(example, hook, variant='base', publish=args.publish, size=(960, 600))


if __name__ == '__main__':
    main()
