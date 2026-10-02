from pathlib import Path
from datetime import datetime
import ctypes
import os
import re
import subprocess
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument



def documents_dir():
    # Asks Windows, since Documents may be redirected (e.g. into OneDrive).
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # 5 = CSIDL_PERSONAL
    return Path(buf.value)

FROZEN = getattr(sys, "frozen", False)  # True in the packaged app
# Bundled files: next to the code, or inside the packaged app.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
# Working folders: src/ when run from source, Documents\HwTex when packaged.
DATA_DIR = documents_dir() / "HwTex" if FROZEN else Path(__file__).parent

PROMPTS = RESOURCE_DIR / "prompts"
PROMPT_FILE = PROMPTS / "prompt.txt"
# The figure rule: a placeholder, or (experimental) redraw the figure with TikZ.
FIGURE_RULES = {False: PROMPTS / "figures.txt", True: PROMPTS / "figures_experimental.txt"}
TEMPLATE = RESOURCE_DIR / "latex_template" / "notes_template.tex"
# Optional personal setup: custom/template.tex replaces the default template, custom/prompt.txt
# adds rules to the prompt, and .sty files in custom/ are found when compiling.
CUSTOM = DATA_DIR / "custom"
INBOX = DATA_DIR / "inbox"
OUTPUT = DATA_DIR / "output"
PROCESSED = DATA_DIR / "processed"
FAILED = DATA_DIR / "failed"

SUPPORTED = [".pdf", ".png", ".jpg", ".jpeg"]
ERROR_MARKER = "HWTEX_ERROR"
MARKER = "%%BODY%%"  # where the notes go, in the template or in your own .tex
MAX_PREAMBLE = 8000  # the prompt goes on the command line, which Windows limits to ~32k chars
PAGE_PIXELS = 1600  # long side of each PDF page sent to Claude, enough for small handwriting
ROOT_COMMENT =re.compile(r"^\s*%\s*!TEX\s+root\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

# With no console (the packaged app), every claude/latexmk call would pop up its own
# console window, so hide them and don't let them wait on input that never comes.
NO_CONSOLE = sys.stdout is None
HIDDEN = {"creationflags": subprocess.CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL} if NO_CONSOLE else {}

def find_tool(name, fallbacks):
    # An app started from a shortcut may not have the same PATH as a terminal.
    found = shutil.which(name)
    if found:
        return found
    for path in fallbacks:
        if path.exists():
            return str(path)
    return None

LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
PROGRAM_FILES = Path(os.environ.get("ProgramFiles", "C:/Program Files"))

def find_claude():
    return find_tool("claude", [
        Path.home() / ".local" / "bin" / "claude.exe",  # native installer
        LOCAL_APPDATA / "Microsoft" / "WinGet" / "Links" / "claude.exe",
    ])

def find_latexmk():
    return find_tool("latexmk", [
        *sorted(Path("C:/texlive").glob("*/bin/windows/latexmk.exe"), reverse=True),
        LOCAL_APPDATA / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "latexmk.exe",
        PROGRAM_FILES / "MiKTeX" / "miktex" / "bin" / "x64" / "latexmk.exe",
    ])

CLAUDE = find_claude()
LATEXMK = find_latexmk()

def refresh_tools():
    # Picks up a claude or TeX install made while the app was open.
    global CLAUDE, LATEXMK
    CLAUDE, LATEXMK = find_claude(), find_latexmk()

def missing_tools():
    return [name for name, path in [("claude", CLAUDE), ("latexmk", LATEXMK)] if path is None]

def check_latexmk():
    """Returns (ok, detail). Found isn't enough: MiKTeX's latexmk also needs Perl."""
    if LATEXMK is None:
        return False, "not found"
    try:
        result = subprocess.run([LATEXMK, "-v"], capture_output=True, text=True, timeout=60, **HIDDEN)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or "latexmk could not run"
    return True, LATEXMK

def check_claude_login():
    """Returns (ok, detail). Sends Claude a tiny prompt, so it takes a few seconds."""
    if CLAUDE is None:
        return False, "not found"
    try:
        result = subprocess.run(
            [CLAUDE, "-p", "Reply with only the word OK."],
            capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=DATA_DIR, **HIDDEN,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if result.returncode == 0 and "OK" in result.stdout:
        return True, "logged in"
    return False, (result.stderr or result.stdout).strip() or f"claude exited with code {result.returncode}"

def ensure_folders():
    for folder in (INBOX, OUTPUT, PROCESSED, FAILED):
        folder.mkdir(parents=True, exist_ok=True)

class ConversionError(Exception):
    pass

class MissingMarker(ConversionError):
    pass

def natural_key(path):
    # page2 before page10, so pages are handled in the order they were written
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))]

def make_timestamp():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def clean_output(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    return text

def is_ready(file):
    size_before = file.stat().st_size
    time.sleep(2)
    size_after = file.stat().st_size
    return size_before > 0 and size_before == size_after

def template():
    custom = CUSTOM / "template.tex"
    return custom if custom.exists() else TEMPLATE

# newline="" keeps the file's own line endings when an existing .tex is written back.
def read_tex(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()

def write_tex(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)

def find_root(tex_path):
    """The file to compile: tex_path itself, or the main file that \\inputs it (None if not found)."""
    tex = read_tex(tex_path)
    match = ROOT_COMMENT.search(tex)
    if match:
        root = (tex_path.parent / match.group(1)).resolve()
        return root if root.exists() else None
    if "\\begin{document}" in tex:
        return tex_path
    # A chapter without its own \begin{document}: look for the main file next to it or one folder up.
    included = re.compile(r"\\(?:input|include|subfile)\{[^}]*\b" + re.escape(tex_path.stem) + r"(\.tex)?\}")
    for folder in (tex_path.parent, tex_path.parent.parent):
        for candidate in sorted(folder.glob("*.tex")):
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if "\\begin{document}" in text and included.search(text):
                return candidate
    return None

def preamble(tex):
    if "\\begin{document}" not in tex:
        return "(not in this file, assume only amsmath and amssymb are loaded)"
    return tex.split("\\begin{document}", 1)[0].strip()[:MAX_PREAMBLE]

def render_pages(pdf, folder):
    """Save each page of pdf as a PNG in folder and return their paths.

    Claude Code's own PDF reading fails on PDFs over 10 pages on Windows (it claims they
    are password-protected), so PDFs are sent as one image per page instead.
    """
    doc = QPdfDocument()
    doc.load(str(pdf))
    if doc.status() != QPdfDocument.Status.Ready or doc.pageCount() == 0:
        raise ConversionError(f"Could not open {pdf.name} as a PDF")
    pages = []
    for i in range(doc.pageCount()):
        size = doc.pagePointSize(i)
        scale = PAGE_PIXELS / max(size.width(), size.height())
        image = doc.render(i, QSize(round(size.width() * scale), round(size.height() * scale)))
        path = folder / f"page{i + 1:03d}.png"
        image.save(str(path))
        pages.append(path)
    doc.close()
    return pages

def describe_input(file, pages):
    if not pages:
        return str(file)
    return (f"the {len(pages)} page images of {file.name}, in this order:\n"
            + "\n".join(str(page) for page in pages) + "\n")

def make_prompt(source, tex, draw_figures=False):
    custom = CUSTOM / "prompt.txt"
    rules = custom.read_text(encoding="utf-8").strip() if custom.exists() else ""
    figure_rule = FIGURE_RULES[draw_figures].read_text(encoding="utf-8").strip()
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    prompt = prompt.replace("%%PATH%%", source).replace("%%FIGURE_RULE%%", figure_rule)
    prompt = prompt.replace("%%CUSTOM_RULES%%", rules)
    return prompt.replace("%%PREAMBLE%%", preamble(tex))

def insert_body(tex, body, keep_marker):
    if keep_marker:
        body += "\n\n" + MARKER
    newline = "\r\n" if "\r\n" in tex else "\n"
    return tex.replace(MARKER, body.replace("\n", newline), 1)

def archive(file, target, keep_original):
    if keep_original:
        shutil.copy2(file, target)
    else:
        file.rename(target)

def convert(file, timestamp=None, keep_original=False, target=None, more_coming=False, draw_figures=False):
    """Ask Claude to turn file into LaTeX and return the .tex path.

    draw_figures (experimental): Claude redraws diagrams and graphs with TikZ instead of
    leaving a placeholder.

    Without target, writes a new .tex made from the template to output/.
    With target (an existing .tex), puts the notes at the first %%BODY%% in it instead.
    more_coming: more files from the same batch follow, so if this is the last %%BODY%%,
    one is left after the notes for them.
    Moves file to processed/ on success.
    Moves file to failed/ and raises ConversionError on failure
    (MissingMarker if there's no %%BODY%% to replace).
    With keep_original=True the file is copied instead of moved.
    """
    timestamp = timestamp or make_timestamp()
    new_name = f"{file.stem}_{timestamp}{file.suffix}"
    source = target or template()
    tex = read_tex(source)
    if MARKER not in tex:
        archive(file, FAILED / new_name, keep_original)
        raise MissingMarker(f"No {MARKER} in {source.name}. Put {MARKER} where the notes should go "
                            "and save the file.")
    # A chapter's preamble is in its main file.
    root = find_root(source)
    with tempfile.TemporaryDirectory(prefix="hwtex-") as page_dir:
        try:
            pages = render_pages(file, Path(page_dir)) if file.suffix.lower() == ".pdf" else []
        except ConversionError:
            archive(file, FAILED / new_name, keep_original)
            raise
        prompt = make_prompt(describe_input(file, pages), read_tex(root) if root else tex, draw_figures)
        result = subprocess.run(
            # Claude only reads inside its working folder unless given access to other folders.
            [CLAUDE or "claude", "--add-dir", str(file.parent), "--add-dir", page_dir, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=DATA_DIR,
            **HIDDEN,
        )
    if result.returncode != 0 or not result.stdout.strip():
        archive(file, FAILED / new_name, keep_original)
        raise ConversionError(result.stderr)
    latex = clean_output(result.stdout)
    # prompt.txt tells Claude to reply with this instead of LaTeX when it can't do the job.
    if ERROR_MARKER in latex:
        archive(file, FAILED / new_name, keep_original)
        raise ConversionError(latex.split(ERROR_MARKER, 1)[1].strip(" :\n"))
    if target:
        tex_path = target
        tex = read_tex(target)  # again, it may have been edited while Claude worked
        if MARKER not in tex:
            archive(file, FAILED / new_name, keep_original)
            raise MissingMarker(f"{MARKER} was removed from {target.name} while converting.")
    else:
        tex_path = OUTPUT / f"{file.stem}_{timestamp}.tex"
    write_tex(tex_path, insert_body(tex, latex, more_coming and tex.count(MARKER) == 1))
    archive(file, PROCESSED / new_name, keep_original)
    return tex_path

def tex_env():
    # Lets a custom template's .sty files sit next to it in custom/.
    if not CUSTOM.exists():
        return None
    return {**os.environ, "TEXINPUTS": CUSTOM.as_posix() + os.pathsep + os.environ.get("TEXINPUTS", "")}

def compile_pdf(tex_path):
    """Compile tex_path with latexmk. Returns the PDF path, or None if no PDF was produced."""
    subprocess.run(
        [LATEXMK or "latexmk", "-pdf", "-interaction=nonstopmode", "-quiet", tex_path.name],
        cwd=tex_path.parent,
        capture_output=NO_CONSOLE,  # nowhere to print to; the .log has everything
        env=tex_env(),
        **HIDDEN,
    )
    pdf_path = tex_path.with_suffix(".pdf")
    return pdf_path if pdf_path.exists() else None
