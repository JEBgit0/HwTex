# HwTex

Turn photos and scans of handwritten notes into LaTeX and a PDF.

Drop a PDF or image of your notes on the window. [Claude Code](https://code.claude.com/docs/en/overview) reads the handwriting and writes the LaTeX, and your own LaTeX installation turns it into a PDF. The result is a new document, or it goes straight into a `.tex` file you are already writing.

Windows only.

## Motivation

I wanted to try out LaTeX, but typing up my handwritten lecture notes by hand turned out to be far too time consuming. My professor suggested automating it as a small project. It grew a bit beyond that.

If there is real interest in HwTex, I will keep updating it. Feel free to open an [issue](../../issues) with bugs, ideas or questions, or star the repository if you find it useful.

## Features

- **Drop files or folders**, or pick them with a file dialog. Supports `.pdf`, `.png`, `.jpg` and `.jpeg`, including multi-page PDFs from a tablet.
- **Watch a folder**, for example a OneDrive folder your iPad or phone uploads to. New files are converted as they arrive.
- **Insert into your own `.tex`**: type your notes on the PC, switch to pen for a while, and put the handwritten part exactly where you left a `%%BODY%%` marker.
- **Built-in PDF viewer** that shows each result as soon as it is ready.
- **Exact transcription**: mistakes and missing steps are kept as written. Crossed-out terms keep their `\cancel`, scribbled-out mistakes are left out.
- **Figures** become a described placeholder, or with the **experimental** option, Claude redraws them with TikZ.
- **Your own template and macros** through an optional `custom/` folder.

## Requirements

- **Claude Code**, logged in with a paid Claude plan (Pro or Max) or an Anthropic API key. Install it from PowerShell:
  ```powershell
  irm https://claude.ai/install.ps1 | iex
  ```
  then run `claude` once and log in.
- **A TeX distribution** with `latexmk`. [TeX Live](https://tug.org/texlive/windows.html) (full install) is recommended. MiKTeX works too, but also needs [Perl](https://strawberryperl.com).

The app has a **Setup** window that checks both and links to anything missing.

## Install

Download `HwTex.zip` from the [Releases](../../releases) page, unzip it anywhere and run `HwTex.exe`. The app is not code-signed, so Windows may show "Windows protected your PC": click **More info**, then **Run anyway**.

Your files are saved in `Documents\HwTex`.

## Using it

### New PDFs

Drop files on the window. Each file becomes a new `.tex` and PDF in `Documents\HwTex\output`. Double-click a file in the list to open its PDF in your normal viewer.

### Inserting into your own `.tex`

1. In your `.tex`, write a line with only `%%BODY%%` where the handwritten notes should go, and **save the file**.
2. Click **Insert into .tex...** and pick the file.
3. Drop your notes. HwTex replaces `%%BODY%%` with the notes and compiles the file.
4. Let your editor reload the file before you keep typing.

```latex
\section{Lecture 5}
Typed during the lecture...

%%BODY%%

Typed again after switching back to the PC.
```

- Only `%%BODY%%` is changed. It is used up, so add a new one each time you switch to writing by hand.
- For several spots, put a `%%BODY%%` at each. Each file fills the next one from the top, in name order (`page2` before `page10`). Extra files go in after each other at the last one.
- A chapter that your main file includes with `\input` is compiled through the main file. HwTex finds it next to the chapter or one folder up, or you can add `% !TEX root = main.tex` to the chapter.

### Experimental: redrawing figures

By default, diagrams and graphs become a placeholder with a description of what to draw. Tick **Experimental: redraw graphs and diagrams (TikZ)** to have Claude redraw them with TikZ instead, copying axes, labels, curves and marked points as closely as it can. Check these figures before relying on them: drawings are less reliable than text. Your document needs to load `tikz` (the default template does).

### Your own template

Create `Documents\HwTex\custom` with any of:

| File | Purpose |
|---|---|
| `template.tex` | Replaces the default template. Must contain `%%BODY%%`. |
| `prompt.txt` | Extra rules for Claude, one `- ...` line per rule, for example which of your macros to use. |
| `*.sty` | Style files your template needs. They are found when compiling. |

## Keeping the data folder small

HwTex keeps everything it makes, so `Documents\HwTex` grows over time:

- `processed/` has a copy of every file you converted. Tablet PDFs can be 10 MB or more each.
- `output/` has every `.tex` and PDF, plus LaTeX's helper files (`.aux`, `.log`, `.fls`, `.fdb_latexmk`).
- `failed/` has the files that could not be converted.

HwTex never needs old files, so delete what you no longer need from time to time. Copy any PDFs you want to keep somewhere else first. If Documents is synced to OneDrive, these folders also use your cloud storage.

## Running from source

```powershell
git clone <this repository>
cd HwTex
pip install -r requirements.txt
python src/app.py
```

When run from source, the working folders (`output/`, `processed/`, `failed/`, `inbox/`, `custom/`) are created inside `src/` instead of Documents.

`src/main.py` is the original console version: it converts whatever lands in `src/inbox/`.

## Project layout

```
src/
  app.py            the desktop app (PySide6)
  core.py           finding the tools, calling Claude, writing the .tex, compiling
  main.py           the original console version
  build.py          packages the app with PyInstaller
  prompts/          the instructions sent to Claude
  latex_template/   the default notes template
  assets/           the app icon
  share_readme.txt  the readme included in the release zip
```

## How it works

1. A PDF is turned into one image per page (Claude Code's own PDF reading fails on PDFs over 10 pages).
2. HwTex runs `claude -p` with the instructions in `src/prompts/`, the page images and the preamble of the document the notes go into, so Claude only uses packages and macros that document has.
3. The LaTeX is put into the template or your own `.tex` at `%%BODY%%`.
4. `latexmk` compiles it, and the input file is moved or copied to `processed/`.

## License

[MIT](LICENSE). The release zip includes Qt for Python (PySide6), which is licensed under the LGPL.
