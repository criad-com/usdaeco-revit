"""Launch isolated Python probes with explicit source paths, never PYTHONPATH."""
import os
from pathlib import Path
import subprocess
import sys

def python(args, **kwargs):
    args = [str(a) for a in args]
    paths = [p for p in sys.path if p and Path(p).is_dir()]
    code = "import sys,runpy;sys.dont_write_bytecode=True;sys.path[:0]=" + repr(paths) + ";from usdaeco_revit.plugins import install_sync_loader;install_sync_loader();"
    if args[0] == "-m":
        code += "sys.argv=" + repr(args[1:]) + ";runpy.run_module(sys.argv[0],run_name='__main__')"
    elif args[0] == "-c":
        code += "sys.argv=" + repr(["-c", *args[2:]]) + ";exec(" + repr(args[1]) + ")"
    else:
        code += "sys.argv=" + repr(args) + ";runpy.run_path(sys.argv[0],run_name='__main__')"
    kwargs.setdefault("env", {k:v for k,v in os.environ.items() if k != "PYTHONPATH"})
    checked = kwargs.pop("check", False)
    proc = subprocess.run([sys.executable, "-c", code], **kwargs)
    if checked and proc.returncode:
        raise AssertionError(proc.stderr or proc.stdout or f"Python probe exited {proc.returncode}")
    return proc
