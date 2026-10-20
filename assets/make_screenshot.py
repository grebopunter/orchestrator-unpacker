"""Renders docs/screenshot.png: converts the .orc files in a folder with the
app and captures the window without showing it.
Usage: python assets/make_screenshot.py ORC_FOLDER OUT_FOLDER"""
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(here))
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import dop_extract_gui as gui  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
app = QApplication([])
app.setWindowIcon(QIcon(gui.resource("dop-extract.png")))
ini = os.path.join(out, "screenshot-settings.ini")
w = gui.MainWindow(QSettings(ini, QSettings.IniFormat))
w.resize(900, 420)
w.rad_folder.setChecked(True)
w.out_edit.setText(out)
w.rad_full.setChecked(True)
w.add_paths([src])
w.start()
t = time.time()
while w.busy() and time.time() - t < 120:
    app.processEvents()
    time.sleep(0.01)
w.table.clearSelection()
app.processEvents()
dest = os.path.join(os.path.dirname(here), "docs", "screenshot.png")
os.makedirs(os.path.dirname(dest), exist_ok=True)
w.grab().save(dest)
print("saved", dest, w.summary.text())
