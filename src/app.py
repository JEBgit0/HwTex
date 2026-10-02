from pathlib import Path
import itertools
import sys
import threading

from PySide6.QtCore import QBuffer, QByteArray, QObject, QSettings, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

import core
from core import (
    DATA_DIR, FAILED, INBOX, MARKER, OUTPUT, PROCESSED, RESOURCE_DIR, SUPPORTED,
    ConversionError, MissingMarker, convert, compile_pdf, ensure_folders, find_root, is_ready, natural_key,
)



WATCH_INTERVAL_MS = 10_000  # same as the 10 s sleep in main.py
CLAUDE_INSTALL_URL = "https://code.claude.com/docs/en/setup"
TEX_INSTALL_URL = "https://tug.org/texlive/windows.html"
PERL_URL = "https://strawberryperl.com"
ICON = RESOURCE_DIR / "assets" / "icon.ico"
NEW_DOCUMENT_TEXT = "Each file becomes a new PDF"

class Worker(QObject):
    """Runs jobs one at a time on a background thread, like the inbox loop does."""
    status = Signal(int, str)
    # job id, result path (pdf, log or ""), error ("" on success), notes were added to your own .tex
    done = Signal(int, str, str, bool)

    @Slot(int, str, bool, str, bool, bool)
    def run(self, job_id, path, keep_original, target, more_coming, draw_figures):
        # target: .tex to put the notes in at %%BODY%%, or "" for a new document
        file = Path(path)
        if not file.exists():
            self.done.emit(job_id, "", "File no longer exists", False)
            return
        self.status.emit(job_id, "converting...")
        try:
            tex_path = convert(file, keep_original=keep_original,
                               target=Path(target) if target else None, more_coming=more_coming,
                               draw_figures=draw_figures)
        except MissingMarker as e:
            self.done.emit(job_id, "", str(e), False)
            return
        except ConversionError as e:
            self.done.emit(job_id, "", f"Claude failed: {e}".strip(), False)
            return
        except Exception as e:
            self.done.emit(job_id, "", str(e), False)
            return
        # From here on, notes going into your own .tex are already in it; only compiling can fail.
        added = bool(target)
        prefix = f"Notes added to {tex_path.name}, but " if added else ""
        try:
            root = find_root(tex_path)  # a chapter is compiled through its main file
        except (OSError, ValueError) as e:
            self.done.emit(job_id, str(tex_path), f"{prefix}could not read it to compile: {e}", added)
            return
        if root is None:
            self.done.emit(job_id, str(tex_path), f"{prefix}no main file found to compile. "
                           "Add a line % !TEX root = main.tex to it.", added)
            return
        self.status.emit(job_id, "compiling...")
        try:
            pdf_path = compile_pdf(root)
        except Exception as e:
            self.done.emit(job_id, str(tex_path), f"{prefix}could not run latexmk: {e}", added)
            return
        if pdf_path is None:
            log = root.with_suffix(".log")
            self.done.emit(job_id, str(log), f"{prefix}PDF not created, see {log.name}", added)
            return
        self.done.emit(job_id, str(pdf_path), "", added)


class Watcher(QObject):
    """Scans the watched folder on its own thread, since is_ready sleeps while checking."""
    found = Signal(str, list, str)  # folder, ready files, error ("" if the scan worked)

    @Slot(str, list)
    def scan(self, folder, skip):
        ready = []
        try:
            for file in sorted(Path(folder).iterdir(), key=natural_key):
                if file.suffix.lower() in SUPPORTED and str(file) not in skip and is_ready(file):
                    ready.append(str(file))
        except OSError as e:
            self.found.emit(folder, ready, str(e))
            return
        self.found.emit(folder, ready, "")


class DropZone(QLabel):
    dropped = Signal(list)

    def __init__(self):
        super().__init__("Drop files or folders here\n(" + ", ".join(SUPPORTED) + ")")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(140)
        self.setAcceptDrops(True)
        self.set_highlight(False)

    def set_highlight(self, on):
        color = "#3b82f6" if on else "#888"
        self.setStyleSheet(
            f"border: 2px dashed {color}; border-radius: 8px; color: {color}; font-size: 14px;"
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.set_highlight(True)

    def dragLeaveEvent(self, event):
        self.set_highlight(False)

    def dropEvent(self, event):
        self.set_highlight(False)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.dropped.emit(paths)


class SetupDialog(QDialog):
    """Shows what HwTex needs on this PC, with install links for anything missing."""
    login_checked = Signal(bool, str)

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("HwTex setup")
        self.setMinimumWidth(520)
        self.settings = settings
        self.login = None  # (ok, detail) once tested in this session
        self.testing = False
        self.latex = (False, "")

        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        self.login_button = QPushButton("Test Claude login")
        self.login_button.clicked.connect(self.test_login)
        recheck_button = QPushButton("Check again")
        recheck_button.clicked.connect(self.refresh)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        self.login_checked.connect(self.login_done)

        buttons = QHBoxLayout()
        buttons.addWidget(self.login_button)
        buttons.addWidget(recheck_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addLayout(buttons)

    def needs_attention(self):
        return bool(core.missing_tools()) or not self.latex[0] or not self.settings.value("claude_login_ok", False, bool)

    def refresh(self):
        core.refresh_tools()
        self.latex = core.check_latexmk()
        self.render()

    def render(self):
        ok, bad = "<span style='color:#16a34a'>&#10003;</span>", "<span style='color:#dc2626'>&#10007;</span>"
        rows = []

        if core.CLAUDE:
            rows.append(f"{ok} <b>Claude Code</b> found<br><small>{core.CLAUDE}</small>")
        else:
            rows.append(
                f"{bad} <b>Claude Code</b> not found. <a href='{CLAUDE_INSTALL_URL}'>Install it</a>, "
                "for example by running this in PowerShell:<br><code>irm https://claude.ai/install.ps1 | iex</code>"
            )

        if not core.CLAUDE:
            rows.append(f"{bad} <b>Claude login</b>: install Claude Code first.")
        elif self.testing:
            rows.append("&#8230; <b>Claude login</b>: testing, this takes a few seconds.")
        elif self.login is None:
            tested = self.settings.value("claude_login_ok", False, bool)
            rows.append(
                f"{ok if tested else bad} <b>Claude login</b>: "
                + ("worked last time it was tested." if tested else
                   "not tested yet. Click <i>Test Claude login</i> (sends Claude one tiny message).")
            )
        elif self.login[0]:
            rows.append(f"{ok} <b>Claude login</b> works.")
        else:
            rows.append(
                f"{bad} <b>Claude login</b> failed: {self.login[1]}<br>Open a terminal, run <code>claude</code> "
                "and log in. Claude Code needs a Pro or Max plan, or an Anthropic API key."
            )

        if self.latex[0]:
            rows.append(f"{ok} <b>LaTeX</b> (latexmk) works<br><small>{self.latex[1]}</small>")
        elif core.LATEXMK is None:
            rows.append(f"{bad} <b>LaTeX</b> not found. <a href='{TEX_INSTALL_URL}'>Install TeX Live</a> "
                        "(the full install includes everything HwTex needs).")
        else:
            rows.append(f"{bad} <b>LaTeX</b>: latexmk was found but won't run: {self.latex[1]}<br>"
                        f"With MiKTeX, latexmk also needs <a href='{PERL_URL}'>Perl</a>.")

        rows.append(f"<br>Your PDFs are saved in:<br><small>{DATA_DIR}</small>")
        rows.append("<small>Just installed something? Close and reopen HwTex if <i>Check again</i> "
                    "doesn't pick it up.</small>")
        self.label.setText("<br><br>".join(rows))
        self.login_button.setEnabled(bool(core.CLAUDE) and not self.testing)

    def test_login(self):
        self.testing = True
        self.render()
        threading.Thread(target=lambda: self.login_checked.emit(*core.check_claude_login()), daemon=True).start()

    def login_done(self, ok, detail):
        self.testing = False
        self.login = (ok, detail)
        self.settings.setValue("claude_login_ok", ok)
        self.render()


class MainWindow(QMainWindow):
    submit = Signal(int, str, bool, str, bool, bool)
    request_scan = Signal(str, list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HwTex")
        self.resize(1200, 750)
        self.settings = QSettings("HwTex", "HwTex")
        self.job_ids = itertools.count()
        self.jobs = {}  # job id -> QListWidgetItem
        self.watched = None  # folder being watched, or None
        self.target = None  # .tex new notes are put into at %%BODY%%, or None for new documents
        self.watch_jobs = {}  # job id -> path, for watched files still being processed
        self.gave_up = set()  # watched files that failed without being moved to failed/
        self.scanning = False

        drop_zone = DropZone()
        drop_zone.dropped.connect(self.add_paths)
        files_button = QPushButton("Select files...")
        files_button.clicked.connect(self.select_files)
        folder_button = QPushButton("Select folder...")
        folder_button.clicked.connect(self.select_folder)
        self.watch_button = QPushButton("Watch folder...")
        self.watch_button.clicked.connect(self.toggle_watch)
        self.setup_dialog = SetupDialog(self, self.settings)
        setup_button = QPushButton("Setup")
        setup_button.clicked.connect(self.show_setup)
        self.watch_label = QLabel("Not watching a folder")
        self.watch_label.setWordWrap(True)
        self.watch_label.setStyleSheet("color: #888;")
        self.target_button = QPushButton("Insert into .tex...")
        self.target_button.clicked.connect(self.toggle_target)
        self.target_label = QLabel(NEW_DOCUMENT_TEXT)
        self.target_label.setWordWrap(True)
        self.target_label.setStyleSheet("color: #888;")
        self.figures_box = QCheckBox("Experimental: redraw graphs and diagrams (TikZ)")
        self.figures_box.setToolTip(
            "Claude tries to copy each figure exactly with TikZ instead of leaving a\n"
            "FIGURE HERE placeholder. Check the result: drawings are less reliable than text."
        )
        self.figures_box.setChecked(self.settings.value("draw_figures", False, bool))
        self.figures_box.toggled.connect(lambda on: self.settings.setValue("draw_figures", on))

        self.job_list = QListWidget()
        self.job_list.currentItemChanged.connect(self.show_job)
        self.job_list.itemDoubleClicked.connect(self.open_job)
        self.job_list.setToolTip("Double-click to open the PDF (or the log if compiling failed)")

        buttons = QHBoxLayout()
        buttons.addWidget(files_button)
        buttons.addWidget(folder_button)
        buttons.addWidget(self.watch_button)
        buttons.addWidget(setup_button)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_label, 1)
        target_row.addWidget(self.target_button)
        left_layout = QVBoxLayout()
        left_layout.addWidget(drop_zone)
        left_layout.addLayout(buttons)
        left_layout.addWidget(self.watch_label)
        left_layout.addLayout(target_row)
        left_layout.addWidget(self.figures_box)
        left_layout.addWidget(self.job_list)
        left = QWidget()
        left.setLayout(left_layout)

        self.pdf_doc = QPdfDocument(self)
        self.pdf_buffer = None  # holds the shown PDF's bytes
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_doc)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.pdf_view)
        splitter.setSizes([420, 780])
        self.setCentralWidget(splitter)

        self.worker_thread = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.worker_thread)
        self.submit.connect(self.worker.run)
        self.worker.status.connect(self.set_status)
        self.worker.done.connect(self.job_done)
        self.worker_thread.start()

        self.watcher_thread = QThread(self)
        self.watcher = Watcher()
        self.watcher.moveToThread(self.watcher_thread)
        self.request_scan.connect(self.watcher.scan)
        self.watcher.found.connect(self.watched_files_found)
        self.watcher_thread.start()

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(WATCH_INTERVAL_MS)
        self.watch_timer.timeout.connect(self.scan_watched)

    def show_setup(self, only_if_needed=False):
        self.setup_dialog.refresh()
        if not only_if_needed or self.setup_dialog.needs_attention():
            self.setup_dialog.show()

    def select_files(self):
        pattern = "Notes (" + " ".join(f"*{ext}" for ext in SUPPORTED) + ")"
        names, _ = QFileDialog.getOpenFileNames(self, "Select files", "", pattern)
        self.add_paths([Path(name) for name in names])

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths):
        files = []
        for path in paths:
            if path.is_dir():
                files.extend(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED)
            elif path.suffix.lower() in SUPPORTED:
                files.append(path)
        if not files:
            self.statusBar().showMessage("No supported files found", 4000)
            return
        # Explorer hands over dropped files starting with the one you dragged, not in name order.
        files.sort(key=natural_key)
        for i, file in enumerate(files):
            self.add_job(file, keep_original=True, more_coming=i < len(files) - 1)

    def add_job(self, file, keep_original, more_coming=False):
        # more_coming: more files from the same batch follow. Each file fills the next %%BODY%%,
        # and files beyond the last %%BODY%% go in after each other at that one.
        job_id = next(self.job_ids)
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, "")
        item.setData(Qt.ItemDataRole.UserRole + 1, file.name)
        self.job_list.addItem(item)
        self.jobs[job_id] = item
        self.set_status(job_id, "queued")
        self.submit.emit(job_id, str(file), keep_original, str(self.target or ""), more_coming,
                         self.figures_box.isChecked())
        return job_id

    def toggle_target(self):
        if self.target:
            self.target = None
            self.target_label.setText(NEW_DOCUMENT_TEXT)
            self.target_label.setToolTip("")
            self.target_button.setText("Insert into .tex...")
            return
        start = self.settings.value("target_folder", "")
        name, _ = QFileDialog.getOpenFileName(self, "Insert notes into", start, "LaTeX (*.tex)")
        if not name:
            return
        target = Path(name)
        self.settings.setValue("target_folder", str(target.parent))
        self.target = target
        self.target_label.setText(f"Notes go into {target.name} where it says {MARKER}")
        self.target_label.setToolTip(str(target))
        self.target_button.setText("New PDFs instead")
        self.explain_target(target)

    def explain_target(self, target):
        try:
            markers = core.read_tex(target).count(MARKER)
        except (OSError, ValueError):
            markers = 0
        show_reminder = not self.settings.value("hide_save_reminder", False, bool)
        if markers and not show_reminder:
            return
        parts = []
        if not markers:
            parts.append(f"{target.name} has no {MARKER} yet. Add a line with {MARKER} where the "
                         "handwritten notes should go.")
        if show_reminder:
            parts.append(
                f"Save {target.name} in your editor before converting. HwTex changes the saved file, "
                "so anything you haven't saved isn't seen, and saving an older version from your "
                "editor afterwards overwrites the notes. Let your editor reload the file before "
                "you keep typing."
            )
        parts.append(f"Each file fills the next {MARKER} from the top, and is replaced by the notes, "
                     f"so add a new {MARKER} each time you switch to writing by hand.")
        box = QMessageBox(QMessageBox.Icon.Information, "HwTex", "\n\n".join(parts), parent=self)
        # Keep our own reference: box.checkBox() hands back a bare QObject in PySide.
        dont_remind = QCheckBox("Don't remind me to save again")
        if show_reminder:
            box.setCheckBox(dont_remind)
        box.exec()
        if show_reminder and dont_remind.isChecked():
            self.settings.setValue("hide_save_reminder", True)

    def toggle_watch(self):
        if self.watched:
            self.stop_watching()
            return
        start = self.settings.value("watch_folder", str(INBOX))
        folder = QFileDialog.getExistingDirectory(self, "Watch folder", start)
        if not folder:
            return
        if Path(folder).resolve() in {OUTPUT.resolve(), PROCESSED.resolve(), FAILED.resolve()}:
            QMessageBox.warning(self, "HwTex", "Can't watch output/, processed/ or failed/, "
                                "the app writes its own files there.")
            return
        self.settings.setValue("watch_folder", folder)
        self.watched = folder
        self.watch_label.setText(f"Watching {folder}\nNew files are moved to processed/ after converting.")
        self.watch_button.setText("Stop watching")
        self.watch_timer.start()
        self.scan_watched()

    def stop_watching(self):
        # Files already queued from the folder still finish.
        self.watch_timer.stop()
        self.watched = None
        self.watch_label.setText("Not watching a folder")
        self.watch_button.setText("Watch folder...")

    def scan_watched(self):
        if self.scanning or not self.watched:
            return
        self.scanning = True
        skip = list(self.watch_jobs.values()) + list(self.gave_up)
        self.request_scan.emit(self.watched, skip)

    def watched_files_found(self, folder, files, error):
        self.scanning = False
        if error:
            self.statusBar().showMessage(f"Can't read watched folder: {error}", 8000)
        if folder != self.watched:
            return  # stopped or switched folders while scanning
        new = [path for path in files if path not in self.watch_jobs.values()]
        for i, path in enumerate(new):
            job_id = self.add_job(Path(path), keep_original=False, more_coming=i < len(new) - 1)
            self.watch_jobs[job_id] = path

    def set_status(self, job_id, status):
        item = self.jobs[job_id]
        item.setText(f"{item.data(Qt.ItemDataRole.UserRole + 1)}  ·  {status}")

    def job_done(self, job_id, result_path, error, added):
        watched_path = self.watch_jobs.pop(job_id, None)
        # A watched file still in place after failing would be picked up again every scan.
        if error and watched_path and Path(watched_path).exists():
            self.gave_up.add(watched_path)
        item = self.jobs[job_id]
        item.setData(Qt.ItemDataRole.UserRole, result_path)
        if error:
            self.set_status(job_id, "⚠ added, not compiled" if added else "✗ failed")
            item.setToolTip(error)
            self.statusBar().showMessage(f"{item.data(Qt.ItemDataRole.UserRole + 1)}: {error}", 8000)
            return
        self.set_status(job_id, "✓ done")
        current = self.job_list.currentItem()
        # Also when it rebuilt the PDF on screen, as happens when adding to your own .tex.
        if current is None or current is item or current.data(Qt.ItemDataRole.UserRole) == result_path:
            self.job_list.setCurrentItem(item)
            self.show_job(item)

    def show_job(self, item, _previous=None):
        path = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.pdf_doc.close()
        if self.pdf_buffer:
            self.pdf_buffer.deleteLater()
            self.pdf_buffer = None
        if not path.endswith(".pdf"):
            return
        # Loaded from memory: a PDF opened by path stays locked, and your own .tex's PDF gets rebuilt.
        try:
            data = Path(path).read_bytes()
        except OSError:
            return
        self.pdf_buffer = QBuffer(self)
        self.pdf_buffer.setData(QByteArray(data))
        self.pdf_buffer.open(QBuffer.OpenModeFlag.ReadOnly)
        self.pdf_doc.load(self.pdf_buffer)

    def open_job(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def closeEvent(self, event):
        # Queued jobs are dropped; a job already running finishes first.
        self.watch_timer.stop()
        for thread in (self.watcher_thread, self.worker_thread):
            thread.quit()
            thread.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    ensure_folders()
    window = MainWindow()
    window.show()
    window.statusBar().showMessage(f"Files are saved in {OUTPUT.parent}", 8000)
    # First run, or something missing: open the setup check once the window is up.
    QTimer.singleShot(0, lambda: window.show_setup(only_if_needed=True))
    sys.exit(app.exec())
