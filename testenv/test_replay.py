"""Fresh offline driver replay and explicit native absence."""
import os
from pathlib import Path
import pytest
from usdaeco_revit.replay import replay,audit

def test_offline_wallpipe_replay(tmp_path):
    session,summary=replay(tmp_path)
    assert summary == dict(mode='synthetic-receipt-replay',accepted=2,driverDifferences=0,
                           repeatMutations=0,nativeMutations=0,driversCompared=14,bodiesCompared=2)
    assert session.stage.GetPrimAtPath('/Model/Wall').GetAttribute('aeco:wall:height').Get()==2.6

def test_archived_live_summary_audit():
    summary=audit(Path(__file__).resolve().parents[1]/'examples/roundtrip/inputs/recording.json')
    assert summary['cases']==11 and summary['driverDifferences']==0
    assert summary['liveStatus']=='NOT RUN' and summary['rawReceiptReplay']=='NOT PROVEN'

@pytest.mark.live
@pytest.mark.skipif(not os.environ.get('AECO_REVIT_ENDPOINT'),reason='AECO_REVIT_ENDPOINT is unset; native gate NOT RUN')
def test_live_revit_gate():
    from usdaeco_revit.cli import main
    assert main(['live-gate','--case-set','datacentre']) in (0,None)
