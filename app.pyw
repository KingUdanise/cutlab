"""CutLab - applicazione desktop.

Avvia il backend in un thread e apre la UI in una finestra nativa (WebView2):
nessun browser, nessuna barra degli indirizzi.

    pythonw C:\\aivinci\\cutlab\\app.pyw     # finestra
    python  C:\\aivinci\\cutlab\\app.pyw     # con console, per vedere gli errori
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

LOG = os.path.join(HERE, "out", "app.log")
PORT = int(os.environ.get("CUTLAB_PORT") or 8778)


def _hook_streams():
    """Con pythonw stdout/stderr sono None: uvicorn esplode. Li dirottiamo."""
    if sys.stdout is None or sys.stderr is None:
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            fh = open(LOG, "a", encoding="utf-8", buffering=1)
        except Exception:
            import io
            fh = io.StringIO()
        if sys.stdout is None:
            sys.stdout = fh
        if sys.stderr is None:
            sys.stderr = fh


def log(msg: str):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_available(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.6) as r:
            return r.status == 200
    except Exception:
        return False


def _wait(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _up(port):
            return True
        time.sleep(0.25)
    return False


def main() -> int:
    _hook_streams()
    port = PORT if _port_available(PORT) else _free_port()
    os.environ["CUTLAB_PORT"] = str(port)
    log(f"--- avvio CutLab (porta {port}, python {sys.version.split()[0]})")

    if not _up(port):
        import uvicorn
        from server import app as fastapi_app

        def serve():
            try:
                uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
            except Exception:
                import traceback
                log("ERRORE backend:\n" + traceback.format_exc())

        threading.Thread(target=serve, daemon=True, name="cutlab-http").start()
        if not _wait(port):
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "Il backend di CutLab non si è avviato.\nControlla le dipendenze Python.",
                "CutLab", 0x10)
            return 1

    log("backend pronto, apro la finestra")
    import webview

    webview.create_window(
        "CutLab — taglio multicam per DaVinci Resolve",
        f"http://127.0.0.1:{port}",
        width=1560, height=960, min_size=(1180, 720),
        background_color="#131209",
    )
    try:
        webview.start(debug=False, private_mode=False, gui="edgechromium")
    except Exception:
        import traceback
        log("ERRORE webview:\n" + traceback.format_exc())
        raise
    log("finestra chiusa, esco")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        log("ERRORE fatale:\n" + traceback.format_exc())
        sys.exit(1)
