#!/usr/bin/env python3
"""Explicitly regenerate the offline fixture; this never contacts a native host."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bootstrap
from aeco_sync import register_plugins
register_plugins()
from usdaeco_revit.facility_replay import record_source

if __name__ == '__main__':
    root = Path(os.environ['AECO_DATACENTRE_ROOT'])
    if json.loads((root/'library.json').read_text())['version'] != '0.4.6':
        raise ValueError('Select the pinned data-centre v0.4.6 checkout')
    record = record_source(root/'dist/base/dc.usda')
    (Path(__file__).parent/'inputs/offline-receipts.json').write_text(json.dumps(record, indent=2)+'\n')
    print('== stage: offline fixture recorded; native execution NOT RUN')
