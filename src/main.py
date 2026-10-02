import time
import os

from core import INBOX, SUPPORTED, ConversionError, make_timestamp, ensure_folders, is_ready, convert, compile_pdf



# DEMO: compile and open the first .tex of this run (remove later)
demo_shown = False

def compile_and_open(tex_path):
    pdf_path = compile_pdf(tex_path)
    if pdf_path:
        os.startfile(pdf_path)
    else:
        print(f"    PDF not created, check output/{tex_path.stem}.log")

def process_inbox():
    global demo_shown
    timestamp = make_timestamp()
    for file in INBOX.iterdir():
        if file.suffix.lower() in SUPPORTED:
            if not is_ready(file):
                print(f" {file.name} still syncing, will retry")
                continue
            print(f"Converting {file.name}...")
            try:
                tex_path = convert(file, timestamp)
            except ConversionError as e:
                print(f"  Failed, moved {file.name} to failed/. Error: {e}")
                continue
            print(f"    Done -> output/{tex_path.name}")
            if not demo_shown:
                demo_shown = True
                compile_and_open(tex_path)
            print("Watching inbox... (Ctrl+C to stop)")

ensure_folders()
print("Watching inbox... (Ctrl+C to stop)")
try:
    while True:
        process_inbox()
        time.sleep(10)
except KeyboardInterrupt:
    print("Stopped.")
