#!/usr/bin/env python3
"""Verify source integration and print the family N checks, M failed line."""
import json
import os
from pathlib import Path
import re
import sys
import bootstrap
from usdaeco_check import Report
from usdaeco_check.structure import check_structure
from usdaeco_check.example import check_example
from usdaeco_revit.runtime import python

ROOT=Path(__file__).resolve().parent

def main():
    report=Report();evidence={}
    print('== stage: core validators',flush=True)
    import aeco_sync
    aeco_sync.register_plugins()
    from pxr import Plug, Usd, UsdValidation
    core=Path(os.environ.get('AECO_CORE_ROOT',os.environ.get('AECO_CORE',ROOT.parent/'usdaeco-core')))
    Plug.Registry().RegisterPlugins(str(core.resolve()/'usdAecoValidators'))
    try:
        import usdAecoValidators
    except ImportError as exc:
        raise RuntimeError('usdAecoValidators must be importable; core validation cannot be skipped') from exc
    registry=UsdValidation.ValidationRegistry()
    descriptor=json.loads((core/'usdAecoValidators/plugInfo.json').read_text())
    declared={'usdAecoValidators:'+name for name,definition in descriptor['Plugins'][0]['Info']['Validators'].items()
              if isinstance(definition,dict)}
    names={metadata.name for metadata in registry.GetValidatorMetadataForKeyword('UsdAecoValidators')}
    validators=registry.GetOrLoadValidatorsByName(sorted(names))
    loaded=bool(declared) and names==declared and len(validators)==len(names) and all(validators)
    report.check('all core validators loaded',loaded,f'{len(validators)} registry validators')
    if not loaded:
        raise RuntimeError('Every declared core validator must load through UsdValidation')
    print('== stage: integration structure',flush=True)
    for result in check_structure(ROOT):report.add(result)
    from aeco_sync.hosts.base import Host,discover,host_class
    from usdaeco_revit.host import RevitHost
    report.check('sync v0.5 contract',aeco_sync.__version__.startswith('0.5.'))
    entry=discover().get('revit')
    report.check('declared entry point',entry is not None and entry.value=='usdaeco_revit.host:RevitHost')
    report.check('discovered host implements lifecycle',host_class('revit') is RevitHost and not RevitHost.__abstractmethods__ and issubclass(RevitHost,Host))
    report.check('immutable capabilities',isinstance(RevitHost().capabilities(),frozenset))
    print('== stage: regression tests',flush=True)
    tests=python(['-m','pytest','-q','--tb=short','-rs'],cwd=ROOT,capture_output=True,text=True,timeout=900)
    print(tests.stdout)
    if tests.returncode:print(tests.stderr)
    report.check('pytest',tests.returncode==0)
    passed=re.search(r'(\d+) passed',tests.stdout);skipped=re.search(r'(\d+) skipped',tests.stdout)
    evidence['pytest']={'passed':int(passed[1]) if passed else 0,'skipped':int(skipped[1]) if skipped else 0}
    from usdaeco_revit.transport import script_pack
    source=script_pack({},'check-pack')
    report.check('hash-verified script pack',len(source)>10000)
    report.check('background document guard',all(token in source for token in ('backgroundDocument','expectedPath')))
    archive=json.loads((ROOT/'testenv/fixtures/recorded-camera-acceptance.json').read_text())
    report.check('archived camera evidence complete',archive['live']['cases']==12 and archive['live']['passed']==12)
    live_status='PASS' if os.environ.get('AECO_REVIT_ENDPOINT') and tests.returncode==0 and evidence['pytest']['skipped']==0 else 'NOT RUN'
    print(live_status+' Revit live gate; see docs/acceptance.md',flush=True)
    evidence['live']={'status':live_status,'priorCases':12,'priorPassed':12}
    if live_status == 'NOT RUN':
        report.not_run('live Revit execution', 'AECO_REVIT_ENDPOINT is unset; offline receipts are not native evidence')
    else:
        report.check('live Revit execution', True)

    print('== stage: roundtrip and render',flush=True)
    run=python([str(ROOT/'examples/roundtrip/run.py')],cwd=ROOT,capture_output=True,text=True,timeout=300)
    print(run.stdout)
    if run.returncode:print(run.stderr[-4000:])
    report.check('roundtrip runner',run.returncode==0)
    report.add(check_example(ROOT/'examples/roundtrip',execute=False))
    if run.returncode==0:
        findings=json.loads((ROOT/'examples/roundtrip/out/findings.json').read_text())
        evidence['roundtrip']=findings
        row=findings[-1]
        report.check('wall and pipe drivers compared',row['driversCompared']==14)
        report.check('zero driver differences',row['driverDifferences']==0)
        report.check('two offline source bodies compared',row['bodiesCompared']==2)
        report.check('repeat apply zero mutations',row['repeatMutations']==0)
        final=Usd.Stage.Open(str(ROOT/'examples/roundtrip/result/example.usdc'))
        from usdaeco_revit.facility_replay import resolve_targets, identities
        targets=resolve_targets(final)
        wall,pipe=targets['wall'],targets['pipe']
        report.check('published final drivers converged',
                     wall.GetAttribute('aeco:wall:height').Get()==3.4 and
                     pipe.GetAttribute('aeco:pipe:nominalDiameter').Get()==.25 and
                     pipe.GetAttribute('aeco:pipe:outerDiameter').Get()==.25)
        report.check('published final bodies converged',
                     abs(max(p[2] for p in wall.GetChild('Geom').GetAttribute('points').Get())-3.4)<1e-6 and
                     abs(max(p[0] for p in pipe.GetChild('Geom').GetAttribute('points').Get())-.125)<1e-6)
        source_path=ROOT/'examples/roundtrip/inputs/source/dist/base/dc.usda'
        source=Usd.Stage.Open(str(source_path))
        manifest=json.loads(source_path.with_name('dc.manifest.json').read_text())
        report.check('counts read from pinned source manifest',row['sourceCounts']==manifest['counts'])
        report.check('full facility and identities retained',
                     {p.GetPath() for p in source.TraverseAll()} <= {p.GetPath() for p in final.TraverseAll()}
                     and identities(source).keys()==identities(final).keys()
                     and not final.GetPrimAtPath('/Model'))
        publication=json.loads((ROOT/'examples/roundtrip/manifest.json').read_text())
        report.check('published pinned base source', publication['source']['mode']=='pinned'
                     and publication['datacentre']=={'ref':'v0.4.6','variant':'base'})
        evidence['result']={'sourcePrims':row['sourcePrimCount'],
                            'publishedPrims':sum(1 for _ in final.TraverseAll()),
                            'bytes':publication['result']['bytes']}
        errors=UsdValidation.ValidationContext(validators).Validate(final)
        failures=[error for error in errors if error.GetType()==UsdValidation.ValidationErrorType.Error]
        warnings=[error for error in errors if error.GetType()==UsdValidation.ValidationErrorType.Warn]
        evidence['coreValidation']={'validators':sorted(names),'errors':len(failures),'warnings':len(warnings),
                                    'findings':[{'name':e.GetName(),'message':e.GetMessage()} for e in errors]}
        report.check('published result core validation',not failures,
                     f'{len(validators)} validators, {len(failures)} errors, {len(warnings)} warnings')
    probe="""import sys
from pxr import Usd, UsdGeom
assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoPort').isUnknown
stage=Usd.Stage.Open(sys.argv[1]);assert stage and not stage.GetCompositionErrors() and stage.Flatten()
mappings=stage.GetMetadata('fallbackPrimTypes') or {}
for prim in stage.TraverseAll():
    if prim.GetTypeName().startswith('Aeco'):
        assert prim.GetTypeName() in mappings, prim.GetTypeName()
        assert prim.IsA(UsdGeom.Xform) or prim.IsA(UsdGeom.Scope), str(prim.GetPath())
"""
    clean={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PXR_PLUGINPATH_NAME','PXR_AR_DEFAULT_SEARCH_PATH')}
    vanilla=python(['-c',probe,str(ROOT/'examples/roundtrip/out/example.usda')],env=clean,capture_output=True,text=True)
    report.check('roundtrip composes with no family plugins',vanilla.returncode==0,vanilla.stderr[-1000:] if vanilla.returncode else '')
    evidence.update(checks=len(report.results),failed=report.failed)
    output=ROOT/'out';output.mkdir(exist_ok=True)
    (output/'check.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return report.finish()

if __name__=='__main__':raise SystemExit(main())
