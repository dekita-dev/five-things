"""Talking to a local open-weight model.

One HTTP endpoint (`llama-server`'s OpenAI-compatible route), no SDK, no key,
no retry loop. If the model is not there, that is a fact and the caller says so
instead of pretending.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8791"


class EngineError(RuntimeError):
    pass


def _post(url, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url + "/v1/chat/completions", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    # A local server still gets a plain UA: some builds front it with a proxy.
    req.add_header("User-Agent", "five-things/0.1")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LocalEngine:
    """Client for an already-running llama-server."""

    def __init__(self, url=None, timeout=180):
        self.url = (url or os.environ.get("FIVE_THINGS_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.seconds = 0.0

    def healthy(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=3) as r:
                return b'"ok"' in r.read()
        except (urllib.error.URLError, OSError):
            return False

    def chat(self, prompt, max_tokens=200, temperature=0.0):
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        started = time.time()
        try:
            data = _post(self.url, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            raise EngineError("HTTP %s from the model server" % exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EngineError("no answer from %s (%s)" % (self.url, exc))
        self.seconds += time.time() - started
        self.calls += 1
        usage = data.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise EngineError("response had no message content")

    def tok_per_second(self):
        return round(self.completion_tokens / self.seconds, 1) if self.seconds > 0 else 0.0


def find_binary():
    for key in ("FIVE_THINGS_LLAMA_BIN",):
        val = os.environ.get(key)
        if val and os.path.exists(val):
            return val
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


def find_model():
    return os.environ.get("FIVE_THINGS_MODEL") or ""


def spawn(binary=None, model=None, port=8791, ctx=2048, threads=6):
    """Start llama-server as a child process. Returns the Popen handle.

    Kept deliberately dumb: the caller owns the process and is expected to stop
    it. No shell, no redirection games, no retry.
    """
    binary = binary or find_binary()
    model = model or find_model()
    if not binary:
        raise EngineError("llama-server not found: set FIVE_THINGS_LLAMA_BIN")
    if not model or not os.path.exists(model):
        raise EngineError("model weights not found: set FIVE_THINGS_MODEL to a .gguf path")
    cmd = [binary, "-m", model, "-c", str(ctx), "-t", str(threads),
           "--port", str(port), "--no-webui"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(url, seconds=90):
    deadline = time.time() + seconds
    probe = LocalEngine(url)
    while time.time() < deadline:
        if probe.healthy():
            return True
        time.sleep(1.0)
    return False
