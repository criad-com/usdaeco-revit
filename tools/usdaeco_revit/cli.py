"""Revit transport and explicitly requested native acceptance gates."""
import argparse
import os

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    live = sub.add_parser('live-gate')
    live.add_argument('--suite', choices=('camera', 'wallpipe'), default='camera')
    args, remaining = parser.parse_known_args(argv)
    if not os.environ.get('AECO_REVIT_ENDPOINT'):
        print('NOT RUN Revit live gate: AECO_REVIT_ENDPOINT is unset')
        return 0
    if args.suite == 'camera':
        from .gates.cctv_revit import main as run
        return run(remaining)
    from .gates.revit import main as run
    import sys
    sys.argv = ['aeco-revit live-gate', *remaining]
    return run()
