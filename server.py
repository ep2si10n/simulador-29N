"""Sirve el simulador y POST /api/update-polls (solo desde localhost).

Arranque desde la raíz del proyecto:  python server.py [puerto]   (por defecto $PORT o 8029)
La actualización ejecuta, en orden, los scrapers de encuestas y build_polls.py.
"""
import json, os, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable
STEPS = ["pipeline/scrape/polls_national.py", "pipeline/scrape/polls_electomania.py", "pipeline/scrape/polls_senate.py", "pipeline/build/build_polls.py"]
TIMEOUT = 600  # s for the whole update
LOCK = threading.Lock()


def run_update():
    steps, ok, deadline = [], True, time.time() + TIMEOUT
    for script in STEPS:
        if not (ROOT / script).exists():
            continue
        t0 = time.time()
        try:
            p = subprocess.run([PY, script], cwd=ROOT, capture_output=True, timeout=max(1, deadline - t0))
            out, good = (p.stdout + p.stderr).decode("utf-8", "replace"), p.returncode == 0
        except subprocess.TimeoutExpired:
            out, good = "tiempo agotado", False
        steps.append({"cmd": script, "ok": good, "seconds": round(time.time() - t0, 1),
                      "tail": "\n".join(out.strip().splitlines()[-5:])})
        if not good:
            ok = False
            break
    polls = json.loads((ROOT / "data" / "polls.json").read_text("utf-8"))
    return {"ok": ok, "steps": steps, "polls_generated": polls["generated"], "n_polls": len(polls["polls"]),
            "error": None if ok else f"Falló {steps[-1]['cmd']}"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")  # revalidate every load: edited js/data never served stale
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/update-polls":
            return self.send_error(404)
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_error(403)
        if not LOCK.acquire(blocking=False):
            return self.reply(409, {"ok": False, "error": "Ya hay una actualización en curso"})
        try:
            out = run_update()
        finally:
            LOCK.release()
        self.reply(200 if out["ok"] else 500, out)

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8029))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
