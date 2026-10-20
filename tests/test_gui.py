"""Offscreen tests for the DOP Extract window (skipped without PySide6)."""

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    import dop_extract_gui as gui
except ImportError:  # PySide6 not installed
    gui = None

from test_dop_extract import build_orc


@unittest.skipIf(gui is None, "PySide6 not installed")
class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data, _ = build_orc()
        self.src_dir = os.path.join(self.tmp.name, "in")
        os.makedirs(os.path.join(self.src_dir, "sub"))
        for rel in ("a.orc", "b.ORC", os.path.join("sub", "c.orc")):
            with open(os.path.join(self.src_dir, rel), "wb") as f:
                f.write(data)
        with open(os.path.join(self.src_dir, "bad.orc"), "wb") as f:
            f.write(b"not an orc file")
        ini = os.path.join(self.tmp.name, "settings.ini")
        self.settings = QSettings(ini, QSettings.IniFormat)
        self.win = gui.MainWindow(self.settings)

    def tearDown(self):
        self.win.close()
        self.tmp.cleanup()

    def run_until_done(self, timeout=30):
        end = time.time() + timeout
        while self.win.busy() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.win.busy(), "conversion did not finish")

    def test_add_folder_with_and_without_subfolders(self):
        self.win.chk_sub.setChecked(False)
        self.win.add_paths([self.src_dir])
        self.assertEqual(len(self.win.jobs), 3)          # a, b, bad
        self.win.chk_sub.setChecked(True)
        self.win.add_paths([self.src_dir])                # no duplicates
        self.assertEqual(len(self.win.jobs), 4)
        self.assertEqual(self.win.table.rowCount(), 4)

    def test_convert_next_to_files(self):
        self.win.rad_next.setChecked(True)
        self.win.rad_full.setChecked(True)
        self.win.add_paths([self.src_dir])
        self.assertTrue(self.win.btn_convert.isEnabled())
        self.win.start()
        self.run_until_done()
        status = {os.path.basename(j.path): j.status for j in self.win.jobs}
        self.assertEqual(status, {"a.orc": gui.OK, "b.ORC": gui.OK,
                                  "bad.orc": gui.FAILED})
        self.assertTrue(os.path.isfile(
            os.path.join(self.src_dir, "a", "a.RPP")))
        self.assertIn("1 failed", self.win.summary.text())
        row = [os.path.basename(j.path) for j in self.win.jobs].index("a.orc")
        report, folder, reaper = self.win.table.cellWidget(
            row, self.win.COL_ACTIONS).buttons
        self.assertTrue(report.isEnabled() and folder.isEnabled()
                        and reaper.isEnabled())

    def test_extract_only_to_chosen_folder(self):
        out = os.path.join(self.tmp.name, "out")
        self.win.rad_folder.setChecked(True)
        self.win.out_edit.setText(out)
        self.win.rad_extract.setChecked(True)
        self.win.add_paths([os.path.join(self.src_dir, "a.orc")])
        self.win.start()
        self.run_until_done()
        self.assertEqual(self.win.jobs[0].status, gui.OK)
        self.assertTrue(os.path.isfile(os.path.join(out, "a", "a.mid")))
        self.assertFalse(os.path.exists(os.path.join(out, "a", "a.RPP")))
        reaper = self.win.table.cellWidget(0, self.win.COL_ACTIONS).buttons[2]
        self.assertFalse(reaper.isEnabled())

    def test_folder_mode_needs_a_folder(self):
        self.win.add_paths([os.path.join(self.src_dir, "a.orc")])
        self.win.rad_folder.setChecked(True)
        self.win.out_edit.setText("")
        self.assertFalse(self.win.btn_convert.isEnabled())

    def test_stop(self):
        self.win.chk_sub.setChecked(True)
        self.win.add_paths([self.src_dir])
        self.win.start()
        self.win.stop()
        self.run_until_done()
        self.assertTrue(all(j.status != gui.CONVERTING for j in self.win.jobs))
        self.assertTrue(any(j.status in (gui.STOPPED, gui.WAITING)
                            for j in self.win.jobs))

    def test_settings_remembered(self):
        out = os.path.join(self.tmp.name, "o")
        self.win.rad_folder.setChecked(True)
        self.win.out_edit.setText(out)
        self.win.rad_extract.setChecked(True)
        self.win.chk_sub.setChecked(True)
        self.win.save_settings()
        w2 = gui.MainWindow(self.settings)
        self.assertTrue(w2.rad_folder.isChecked())
        self.assertEqual(w2.out_edit.text(), out)
        self.assertTrue(w2.rad_extract.isChecked())
        self.assertTrue(w2.chk_sub.isChecked())
        w2.close()


if __name__ == "__main__":
    unittest.main()
