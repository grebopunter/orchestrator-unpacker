"""Build the standalone Windows app:  python build_exe.py [dist_dir]

Produces  <dist_dir>/DOP Extract/DOP Extract.exe  (default dist_dir: dist).
Needs PySide6 and PyInstaller (pip install -r requirements-gui.txt).
"""
import os
import sys

import PyInstaller.__main__

here = os.path.dirname(os.path.abspath(__file__))
dist = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "dist")
PyInstaller.__main__.run([
    os.path.join(here, "dop_extract_gui.py"),
    "--name", "DOP Extract",
    "--windowed",
    "--noconfirm",
    "--distpath", dist,
    "--workpath", os.path.join(here, "build"),
    "--specpath", os.path.join(here, "build"),
])
