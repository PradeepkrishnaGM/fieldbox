# PyInstaller spec: `pyinstaller packaging/fieldbox.spec` (run from the repository root).
# One-folder build (faster start than one-file, and what the AppImage wraps).
import os

a = Analysis([os.path.join(SPECPATH, "launch.py")], pathex=[os.path.dirname(SPECPATH)],
             hiddenimports=[], excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="fieldbox", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="fieldbox")
