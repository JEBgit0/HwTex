"""Package app.py into HwTex.exe, add it to the Start Menu and make a zip to share.

Run with: python build.py
Close the app first, the old HwTex.exe can't be replaced while it runs.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import PyInstaller.__main__
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer



SRC = Path(__file__).parent
# Outside OneDrive, so the few hundred MB of Qt files don't get synced.
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs"
WORK_DIR = Path(tempfile.gettempdir()) / "hwtex-build"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "HwTex.lnk"
ZIP_FILE = Path.home() / "Downloads" / "HwTex.zip"
ICON_SVG = SRC / "assets" / "icon.svg"
ICON = SRC / "assets" / "icon.ico"

def make_icon():
    # Windows wants .ico, so render the SVG and let Pillow pack the sizes.
    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(ICON_SVG)).render(painter)
    painter.end()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    png = WORK_DIR / "icon.png"
    image.save(str(png))
    Image.open(png).save(ICON, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

def bundle(file, folder):
    return f"{SRC / file}{os.pathsep}{folder}"

make_icon()
PyInstaller.__main__.run([
    str(SRC / "app.py"),
    "--name", "HwTex",
    "--windowed",
    "--noconfirm",
    "--icon", str(ICON),
    "--distpath", str(INSTALL_DIR),
    "--workpath", str(WORK_DIR),
    "--specpath", str(WORK_DIR),
    "--add-data", bundle("prompts", "prompts"),
    "--add-data", bundle("latex_template/notes_template.tex", "latex_template"),
    "--add-data", bundle("assets/icon.ico", "assets"),
])

app_dir = INSTALL_DIR / "HwTex"
exe = app_dir / "HwTex.exe"
shutil.copy2(SRC / "share_readme.txt", app_dir / "READ ME FIRST.txt")

subprocess.run([
    "powershell", "-NoProfile", "-Command",
    f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{SHORTCUT}'); "
    f"$s.TargetPath = '{exe}'; $s.WorkingDirectory = '{exe.parent}'; $s.Save()",
], check=True)

print("\nZipping...")
shutil.make_archive(str(ZIP_FILE.with_suffix("")), "zip", root_dir=INSTALL_DIR, base_dir="HwTex")

print(f"\nInstalled: {exe}\nStart Menu shortcut: {SHORTCUT}\nZip to share: {ZIP_FILE}")
