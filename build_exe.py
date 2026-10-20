"""Build the standalone Windows app:  python build_exe.py [dist_dir]

Produces  <dist_dir>/DOP Extract/DOP Extract.exe  and a release zip
<dist_dir>/DOP-Extract-<version>-windows.zip  (default dist_dir: dist).
Needs PySide6 and PyInstaller (pip install -r requirements-gui.txt).
"""
import os
import shutil
import sys

import PyInstaller.__main__

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from dop_extract import __version__  # noqa: E402

dist = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                       else os.path.join(here, "dist"))
assets = os.path.join(here, "assets")
PyInstaller.__main__.run([
    os.path.join(here, "dop_extract_gui.py"),
    "--name", "DOP Extract",
    "--windowed",
    "--noconfirm",
    "--icon", os.path.join(assets, "dop-extract.ico"),
    "--add-data", os.path.join(assets, "dop-extract.png") + os.pathsep + "assets",
    "--distpath", dist,
    "--workpath", os.path.join(here, "build"),
    "--specpath", os.path.join(here, "build"),
])
zip_base = os.path.join(dist, "DOP-Extract-%s-windows" % __version__)
shutil.make_archive(zip_base, "zip", dist, "DOP Extract")
print("Release zip:", zip_base + ".zip")
