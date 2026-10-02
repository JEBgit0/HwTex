HwTex: turns photos/scans of handwritten notes into LaTeX PDFs
===============================================================

HwTex runs on Windows. It uses Claude (through Claude Code) to read your notes
and LaTeX to make the PDF. Both have to be installed on your PC first, and
Claude runs on your own Claude account.


1. Install Claude Code
----------------------
You need a paid Claude plan (Pro or Max) or an Anthropic API key.

  a. Open PowerShell (Start menu, type "PowerShell").
  b. Paste this and press Enter:

       irm https://claude.ai/install.ps1 | iex

  c. When it finishes, type "claude" and press Enter, then log in when your
     browser opens. After that you can close PowerShell.

  More help: https://code.claude.com/docs/en/setup


2. Install TeX Live (LaTeX)
---------------------------
  Download "install-tl-windows.exe" from https://tug.org/texlive/windows.html
  and run it with the default options. The full install is large (several GB)
  and can take an hour, but it includes everything HwTex needs.

  Already have MiKTeX? That works too, but you also need Perl from
  https://strawberryperl.com


3. Start HwTex
--------------
  Unzip this folder anywhere (for example Documents) and double-click
  HwTex.exe.

  Windows may say "Windows protected your PC", because the app isn't signed.
  Click "More info", then "Run anyway".

  The first time, a Setup window opens. Click "Test Claude login". When every
  line has a green check mark you're ready.


Using it
--------
  - Drop images or PDFs onto the window, or use "Select files..." or
    "Select folder...". Your originals are left where they are.
  - "Watch folder..." picks up new files in a folder automatically, for
    example a OneDrive folder you upload photos to from your phone. Those files
    are moved out of the folder once converted.
  - Finished PDFs appear on the right. Double-click a file in the list to open
    it in your normal PDF viewer.
  - Everything is saved in Documents\HwTex (output, processed, failed).
  - Something not working? Click "Setup" to see what's missing.


Adding handwritten notes to your own .tex file
----------------------------------------------
  Typing notes on your PC and switching to pen for a while? You can put the
  handwritten part straight into the .tex file you're writing:

  1. In your .tex file, write a line with only %%BODY%% where the handwritten
     notes should go, and SAVE the file. HwTex only sees what's saved.
  2. In HwTex click "Insert into .tex..." and pick that file.
  3. Drop your notes (or let "Watch folder..." pick them up). HwTex replaces
     %%BODY%% with the notes and compiles the file.
  4. Let your editor reload the file before you keep typing. If you save an
     older version over it, the notes are gone.

  Only %%BODY%% is changed, nothing else in the file. It's used up, so write
  a new %%BODY%% next time you switch to writing by hand.

  Several spots at once: put a %%BODY%% at each spot. Each file fills the next
  %%BODY%% from the top, and files are taken in name order (page2 before
  page10). Drop more files than there are %%BODY%%s, and the extra ones go in
  after each other at the last one. A PDF with several pages counts as one file.

  Is the file a chapter that your main file \input's? HwTex finds the main
  file next to it or one folder up and compiles that. If it can't, add a line
  % !TEX root = main.tex to the chapter.

  "added, not compiled" in the list means the notes are in your file, but the
  PDF couldn't be made. Hover over the file in the list to see why.

  Click "New PDFs instead" to go back to making a new PDF per file.


Experimental: redrawing graphs
------------------------------
  Normally a drawing becomes a box saying FIGURE HERE, with a description of
  what to draw. Tick "Experimental: redraw graphs and diagrams (TikZ)" and
  Claude tries to copy each drawing with TikZ instead. Check the result:
  drawings are less reliable than text. Your document needs to load tikz (the
  default template does).


Keeping Documents\HwTex small
-----------------------------
  HwTex keeps everything it makes, so the folder grows over time:
    processed  a copy of every file you converted (tablet PDFs can be 10 MB+)
    output     every .tex and PDF, plus LaTeX's helper files (.aux, .log, ...)
    failed     files that could not be converted
  HwTex never needs old files, so delete what you don't need from time to
  time. Copy PDFs you want to keep somewhere else first. If Documents is synced
  to OneDrive, these folders also use your cloud storage.


Your own template (optional)
----------------------------
  Make a folder Documents\HwTex\custom with:
    template.tex  your template, with %%BODY%% where the notes go
    prompt.txt    extra rules for Claude, one "- ..." line per rule, e.g. which
                  of your macros to use
    *.sty         any style files your template needs
