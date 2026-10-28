"""HwTex's local engine: the fine-tuned handwriting model, run on this PC's NVIDIA GPU.

HwTex starts this once (through uv, which installs torch and friends on first use) and keeps it
running, so the model is loaded only once. It talks JSON lines over stdin/stdout:

    -> {"status": "..."}                  while starting (downloads, loading)
    -> {"ready": true}                    once the model is loaded
    <- {"pages": ["page1.png", ...]}      one request per file, page images in order
    -> {"status": "page 1/3"}             per page
    -> {"body": "..."} or {"error": "..."}

The model: a LoRA adapter on Qwen3-VL-2B, trained in github.com/JEBgit0/slop-page (ml/hwtex-model).
The page handling below (prompt, loop stop, closing environments) matches its engines.py, which
the model was scored with.
"""

import json
import os
import re
import sys
from pathlib import Path

# Everything libraries print goes to stderr; stdout carries only the protocol.
PROTOCOL = sys.stdout
sys.stdout = sys.stderr

ADAPTER_REPO = "despjeb/hwtex-qwen3-vl-2b"
ADAPTER_VERSION = "v3"
# For testing a checkpoint before it's published: a folder with adapter_config.json.
ADAPTER_DIR = os.environ.get("HWTEX_ADAPTER")
PAGE_PIXELS = 1600  # long side, as HwTex renders PDF pages and as the model was trained
MAX_NEW_TOKENS = 3072
# A block of up to LOOP_PERIOD lines repeated LOOP_REPEATS times in a row is a decoding loop, not
# notes. Greedy decoding never leaves one, so generation stops there and only the first copy is kept.
LOOP_PERIOD = 4
LOOP_REPEATS = 6
LIST_ENVS = {"itemize", "enumerate", "description"}
STRUCTURE = re.compile(r"\\begin\{([^}]+)\}|\\end\{([^}]+)\}|\\item(?![a-zA-Z])")


def send(**message):
    PROTOCOL.write(json.dumps(message) + "\n")
    PROTOCOL.flush()


def loop_start(lines):
    """Index of the second copy of the block the lines end in a loop of, or None."""
    for period in range(1, LOOP_PERIOD + 1):
        block = lines[-period:]
        if len(lines) < period * LOOP_REPEATS or not any(line.strip() for line in block):
            continue
        start = len(lines) - period
        while start >= period and lines[start - period : start] == block:
            start -= period
        if (len(lines) - start) // period >= LOOP_REPEATS:
            return start + period
    return None


def cut_loop(text):
    lines = text.splitlines()
    start = loop_start(lines)
    return text if start is None else "\n".join(lines[:start])


def balance(text):
    """Close every environment within the page: pages are joined into one document, so one left
    open (or a stray \\end, or an \\item outside a list) breaks the whole document's compile."""
    out, stack, last = [], [], 0
    for m in STRUCTURE.finditer(text):
        out.append(text[last : m.start()])
        last = m.end()
        if m.group(1):
            stack.append(m.group(1))
            out.append(m.group())
        elif m.group(2):
            if m.group(2) in stack:
                while stack[-1] != m.group(2):
                    out.append(f"\\end{{{stack.pop()}}}\n")
                stack.pop()
                out.append(m.group())
        else:
            if not LIST_ENVS & set(stack):
                stack.append("itemize")
                out.append("\\begin{itemize}\n")
            out.append(m.group())
    out.append(text[last:])
    return "".join(out).rstrip() + "".join(f"\n\\end{{{env}}}" for env in reversed(stack))


def clean_output(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return balance(cut_loop(text))


def load_image(path):
    from PIL import Image

    image = Image.open(path).convert("RGB")
    scale = PAGE_PIXELS / max(image.size)
    if scale < 1:  # phone photos: same size as a rendered PDF page
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    return image


class Model:
    def __init__(self):
        import torch
        from huggingface_hub import snapshot_download
        from peft import PeftModel
        from transformers import AutoModelForImageTextToText, AutoProcessor, StoppingCriteria

        if not torch.cuda.is_available():
            raise RuntimeError("No NVIDIA GPU found (CUDA isn't available). Use Claude on this PC.")
        self.torch = torch
        if ADAPTER_DIR:
            adapter = Path(ADAPTER_DIR)
        else:
            send(status="downloading the model (first time only)")
            adapter = Path(snapshot_download(ADAPTER_REPO, revision=ADAPTER_VERSION))
        config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
        prompt_file = adapter / "prompt.txt"
        self.prompt = (prompt_file if prompt_file.exists() else Path(__file__).parent / "prompt.txt").read_text(
            encoding="utf-8"
        )
        base, revision = config["base_model_name_or_path"], config.get("revision")
        if not Path(base).is_dir():
            send(status="downloading the model (first time only)")
            base = snapshot_download(base, revision=revision)
            revision = None
        send(status="loading the model")
        self.processor = AutoProcessor.from_pretrained(base, revision=revision)
        model = AutoModelForImageTextToText.from_pretrained(
            base, revision=revision, dtype=torch.bfloat16, device_map="cuda"
        )
        self.model = PeftModel.from_pretrained(model, adapter).merge_and_unload()

        class StopOnLoop(StoppingCriteria):
            def __init__(self, tokenizer, prompt_len, every=32):
                self.tokenizer, self.prompt_len, self.every = tokenizer, prompt_len, every

            def __call__(self, input_ids, scores, **kwargs):
                new = input_ids.shape[1] - self.prompt_len
                if new == 0 or new % self.every:
                    return False
                text = self.tokenizer.decode(input_ids[0, self.prompt_len :], skip_special_tokens=True)
                # The last line may still be mid-way through a copy; judge only complete lines.
                return loop_start(text.splitlines()[:-1]) is not None

        self.StopOnLoop = StopOnLoop

    def page(self, image):
        with self.torch.inference_mode():
            messages = [
                {"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": self.prompt}]}
            ]
            inputs = self.processor.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
            ).to(self.model.device)
            prompt_len = inputs["input_ids"].shape[1]
            out = self.model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
                stopping_criteria=[self.StopOnLoop(self.processor.tokenizer, prompt_len)],
            )
            return clean_output(self.processor.decode(out[0, prompt_len:], skip_special_tokens=True))


def main():
    try:
        model = Model()
    except Exception as e:
        send(error=f"Could not start the local model: {e}")
        return
    send(ready=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            pages = json.loads(line)["pages"]
            bodies = []
            for i, path in enumerate(pages, 1):
                send(status=f"page {i}/{len(pages)}")
                bodies.append(model.page(load_image(path)))
            send(body="\n\n".join(bodies))
        except Exception as e:
            send(error=str(e))


if __name__ == "__main__":
    main()
