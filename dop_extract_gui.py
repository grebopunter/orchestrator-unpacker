#!/usr/bin/env python3
"""DOP Extract: desktop app for converting Digital Orchestrator .orc files.

A window around dop_extract.convert(): drop in .orc files or folders, pick
where the output goes and what to make, press Convert.  Requires PySide6.
"""

import os
import sys

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
    QGroupBox)

import dop_extract as dx

APP_NAME = "DOP Extract"

WAITING, CONVERTING, OK, WARN, FAILED, STOPPED = (
    "Waiting", "Converting", "OK", "Warnings", "Failed", "Stopped")


def open_path(path):
    """Open a file or folder with its Windows default program."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - local file the user chose
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
                          path])


class Job:
    def __init__(self, path):
        self.path = path
        self.status = WAITING
        self.out_dir = None
        self.message = ""


class Worker(QObject):
    """Converts the queued files one at a time in a background thread."""
    started_file = Signal(int)
    progress = Signal(int, float)
    finished_file = Signal(int, str, str, str)   # row, status, out_dir, msg
    all_done = Signal()

    def __init__(self, jobs, out_root, make_rpp):
        super().__init__()
        self.jobs = jobs            # list of (row, path)
        self.out_root = out_root
        self.make_rpp = make_rpp
        self._stop = False

    def stop(self):
        self._stop = True

    @Slot()
    def run(self):
        for row, path in self.jobs:
            if self._stop:
                break
            self.started_file.emit(row)

            def cb(x, row=row):
                if self._stop:
                    raise dx.Cancelled()
                self.progress.emit(row, x)

            try:
                out_dir, warnings, _ = dx.convert(path, self.out_root,
                                                  self.make_rpp, cb)
                if warnings:
                    self.finished_file.emit(row, WARN, out_dir,
                                            "; ".join(warnings))
                else:
                    self.finished_file.emit(row, OK, out_dir, "")
            except dx.Cancelled:
                self.finished_file.emit(row, STOPPED, "", "Stopped part-way")
            except Exception as e:  # report every failure in the list
                self.finished_file.emit(row, FAILED, "", str(e) or
                                        type(e).__name__)
        self.all_done.emit()


class ReportDialog(QDialog):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 520)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.NoWrap)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        box.setFont(f)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


class MainWindow(QMainWindow):
    COL_FILE, COL_STATUS, COL_PROGRESS, COL_ACTIONS = range(4)

    def __init__(self, settings=None):
        super().__init__()
        self.setWindowTitle("%s %s" % (APP_NAME, dx.__version__))
        self.setAcceptDrops(True)
        self.settings = settings or QSettings("grebopunter", "DOP Extract")
        self.jobs = []
        self.thread = None
        self.worker = None

        # --- add files -------------------------------------------------
        self.btn_add_files = QPushButton("Add files…")
        self.btn_add_folder = QPushButton("Add folder…")
        self.chk_sub = QCheckBox("Include subfolders")
        self.btn_remove = QPushButton("Remove selected")
        self.btn_clear = QPushButton("Clear list")
        top = QHBoxLayout()
        for w in (self.btn_add_files, self.btn_add_folder, self.chk_sub):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_clear)

        # --- file list -------------------------------------------------
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Progress", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)
        hh.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_PROGRESS, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_ACTIONS, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(self.COL_PROGRESS, 160)
        self.hint = QLabel("Drag .orc files or folders here, or use "
                           "Add files / Add folder.")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: gray; padding: 6px;")

        # --- output ----------------------------------------------------
        self.rad_next = QRadioButton("Next to each .orc file")
        self.rad_folder = QRadioButton("In this folder:")
        self.out_group = QButtonGroup(self)
        self.out_group.addButton(self.rad_next)
        self.out_group.addButton(self.rad_folder)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Choose a folder")
        self.btn_browse = QPushButton("Browse…")
        out_row = QHBoxLayout()
        out_row.addWidget(self.rad_next)
        out_row.addSpacing(16)
        out_row.addWidget(self.rad_folder)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.btn_browse)
        out_box = QGroupBox("Output")
        out_box.setLayout(out_row)

        # --- mode ------------------------------------------------------
        self.rad_full = QRadioButton(
            "Full conversion (.mid, WAVs and REAPER project)")
        self.rad_extract = QRadioButton("Extract only (.mid and WAVs)")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rad_full)
        self.mode_group.addButton(self.rad_extract)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.rad_full)
        mode_row.addSpacing(16)
        mode_row.addWidget(self.rad_extract)
        mode_row.addStretch(1)
        mode_box = QGroupBox("Make")
        mode_box.setLayout(mode_row)

        # --- run -------------------------------------------------------
        self.summary = QLabel("")
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setDefault(True)
        self.btn_convert.setMinimumWidth(110)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setMinimumWidth(80)
        self.btn_stop.setEnabled(False)
        run_row = QHBoxLayout()
        run_row.addWidget(self.summary, 1)
        run_row.addWidget(self.btn_stop)
        run_row.addWidget(self.btn_convert)

        lay = QVBoxLayout()
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.hint)
        lay.addWidget(out_box)
        lay.addWidget(mode_box)
        lay.addLayout(run_row)
        central = QWidget()
        central.setLayout(lay)
        self.setCentralWidget(central)

        delete = QAction(self)
        delete.setShortcut(QKeySequence.Delete)
        delete.triggered.connect(self.remove_selected)
        self.addAction(delete)

        self.btn_add_files.clicked.connect(self.pick_files)
        self.btn_add_folder.clicked.connect(self.pick_folder)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_list)
        self.btn_browse.clicked.connect(self.pick_out_folder)
        self.btn_convert.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.rad_next.toggled.connect(self.update_controls)
        self.out_edit.textChanged.connect(self.update_controls)

        self.load_settings()
        self.update_controls()

    # ------------------------------------------------------------- settings
    def load_settings(self):
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(980, 560)
        self.chk_sub.setChecked(s.value("subfolders", False, type=bool))
        if s.value("out_mode", "next") == "folder":
            self.rad_folder.setChecked(True)
        else:
            self.rad_next.setChecked(True)
        self.out_edit.setText(s.value("out_folder", "", type=str))
        if s.value("make", "full") == "extract":
            self.rad_extract.setChecked(True)
        else:
            self.rad_full.setChecked(True)

    def save_settings(self):
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("subfolders", self.chk_sub.isChecked())
        s.setValue("out_mode", "folder" if self.rad_folder.isChecked()
                   else "next")
        s.setValue("out_folder", self.out_edit.text())
        s.setValue("make", "extract" if self.rad_extract.isChecked()
                   else "full")

    def closeEvent(self, event):
        if self.busy():
            if QMessageBox.question(
                    self, APP_NAME,
                    "A conversion is running. Stop it and quit?") \
                    != QMessageBox.Yes:
                event.ignore()
                return
            self.stop()
            self.thread.quit()
            self.thread.wait()
        self.save_settings()
        event.accept()

    # ---------------------------------------------------------- adding files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.busy():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.isLocalFile()]
        self.add_paths(paths)

    def pick_files(self):
        start = self.settings.value("last_in", "", type=str)
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add .orc files", start,
            "Digital Orchestrator files (*.orc *.ORC);;All files (*)")
        if files:
            self.settings.setValue("last_in", os.path.dirname(files[0]))
            self.add_paths(files)

    def pick_folder(self):
        start = self.settings.value("last_in", "", type=str)
        folder = QFileDialog.getExistingDirectory(self, "Add folder", start)
        if folder:
            self.settings.setValue("last_in", folder)
            self.add_paths([folder])

    def add_paths(self, paths):
        if self.busy():
            return
        files = dx.find_inputs(paths, self.chk_sub.isChecked())
        known = {os.path.normcase(os.path.abspath(j.path)) for j in self.jobs}
        added = 0
        for f in files:
            key = os.path.normcase(os.path.abspath(f))
            if key in known or not os.path.isfile(f):
                continue
            known.add(key)
            self.jobs.append(Job(os.path.abspath(f)))
            self.add_row(self.jobs[-1])
            added += 1
        if paths and not added and not files:
            self.summary.setText("No .orc files found there.")
        self.update_controls()

    def add_row(self, job):
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(os.path.basename(job.path))
        item.setToolTip(job.path)
        self.table.setItem(row, self.COL_FILE, item)
        self.table.setItem(row, self.COL_STATUS, QTableWidgetItem(job.status))
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(0)
        bar.setTextVisible(False)
        self.table.setCellWidget(row, self.COL_PROGRESS, bar)
        self.table.setCellWidget(row, self.COL_ACTIONS, self.make_actions(job))

    def make_actions(self, job):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(4)
        for label, fn in (("Report", self.show_report),
                          ("Folder", self.open_folder),
                          ("REAPER", self.open_reaper)):
            b = QToolButton()
            b.setText(label)
            b.clicked.connect(lambda _=False, j=job, f=fn: f(j))
            h.addWidget(b)
        w.buttons = w.findChildren(QToolButton)
        self.set_actions_enabled(w, job)
        return w

    def set_actions_enabled(self, w, job):
        done = job.out_dir is not None and os.path.isdir(job.out_dir)
        report, folder, reaper = w.buttons
        report.setEnabled(done or bool(job.message))
        folder.setEnabled(done)
        reaper.setEnabled(done and os.path.isfile(self.rpp_path(job)))

    def remove_selected(self):
        if self.busy():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.jobs[r]
        self.update_controls()

    def clear_list(self):
        if self.busy():
            return
        self.table.setRowCount(0)
        self.jobs = []
        self.summary.setText("")
        self.update_controls()

    def pick_out_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Output folder", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)
            self.rad_folder.setChecked(True)

    # ------------------------------------------------------------- actions
    @staticmethod
    def rpp_path(job):
        song = os.path.splitext(os.path.basename(job.path))[0]
        return os.path.join(job.out_dir or "", song + ".RPP")

    def show_report(self, job):
        path = os.path.join(job.out_dir or "", "conversion-report.txt")
        if job.out_dir and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        else:
            text = "%s\n\n%s: %s\n" % (job.path, job.status, job.message)
        ReportDialog("Report: " + os.path.basename(job.path), text,
                     self).exec()

    def open_folder(self, job):
        if job.out_dir and os.path.isdir(job.out_dir):
            open_path(job.out_dir)

    def open_reaper(self, job):
        rpp = self.rpp_path(job)
        if os.path.isfile(rpp):
            open_path(rpp)

    # ----------------------------------------------------------- converting
    def busy(self):
        return self.thread is not None

    def update_controls(self):
        busy = self.busy()
        for w in (self.btn_add_files, self.btn_add_folder, self.chk_sub,
                  self.btn_remove, self.btn_clear, self.rad_next,
                  self.rad_folder, self.rad_full, self.rad_extract):
            w.setEnabled(not busy)
        self.out_edit.setEnabled(not busy and self.rad_folder.isChecked())
        self.btn_browse.setEnabled(not busy)
        folder_ok = (self.rad_next.isChecked()
                     or bool(self.out_edit.text().strip()))
        self.btn_convert.setEnabled(not busy and bool(self.jobs) and folder_ok)
        self.btn_stop.setEnabled(busy)
        self.hint.setVisible(not self.jobs)

    def start(self):
        if self.busy() or not self.jobs:
            return
        out_root = None
        if self.rad_folder.isChecked():
            out_root = self.out_edit.text().strip()
            try:
                os.makedirs(out_root, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, APP_NAME,
                                    "Can't use that output folder:\n%s" % e)
                return
        todo = []
        for row, job in enumerate(self.jobs):
            job.status, job.out_dir, job.message = WAITING, None, ""
            self.set_row(row)
            todo.append((row, job.path))
        self.save_settings()

        self.thread = QThread(self)
        self.worker = Worker(todo, out_root, self.rad_full.isChecked())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.started_file.connect(self.on_started)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_file.connect(self.on_finished)
        self.worker.all_done.connect(self.on_all_done)
        self.summary.setText("Converting…")
        self.thread.start()
        self.update_controls()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.summary.setText("Stopping…")

    def set_row(self, row):
        job = self.jobs[row]
        st = self.table.item(row, self.COL_STATUS)
        st.setText(job.status)
        st.setToolTip(job.message)
        colour = {OK: Qt.darkGreen, WARN: Qt.darkYellow, FAILED: Qt.red,
                  STOPPED: Qt.darkGray}.get(job.status)
        st.setForeground(colour if colour is not None else
                         self.table.palette().text())
        bar = self.table.cellWidget(row, self.COL_PROGRESS)
        if job.status in (OK, WARN):
            bar.setValue(1000)
        elif job.status == WAITING:
            bar.setValue(0)
        self.set_actions_enabled(
            self.table.cellWidget(row, self.COL_ACTIONS), job)

    @Slot(int)
    def on_started(self, row):
        self.jobs[row].status = CONVERTING
        self.set_row(row)
        self.table.scrollToItem(self.table.item(row, self.COL_FILE))

    @Slot(int, float)
    def on_progress(self, row, x):
        self.table.cellWidget(row, self.COL_PROGRESS).setValue(int(x * 1000))

    @Slot(int, str, str, str)
    def on_finished(self, row, status, out_dir, message):
        job = self.jobs[row]
        job.status, job.out_dir, job.message = status, out_dir or None, message
        self.set_row(row)

    @Slot()
    def on_all_done(self):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.worker = None
        counts = {}
        for j in self.jobs:
            if j.status == CONVERTING:
                j.status = STOPPED
            counts[j.status] = counts.get(j.status, 0) + 1
        for row in range(len(self.jobs)):
            self.set_row(row)
        parts = ["%d %s" % (counts[k], k.lower())
                 for k in (OK, WARN, FAILED, STOPPED, WAITING) if counts.get(k)]
        self.summary.setText("Done: " + ", ".join(parts))
        self.update_controls()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.add_paths([a for a in sys.argv[1:] if os.path.exists(a)])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
