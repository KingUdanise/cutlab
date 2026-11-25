"""Test del backend via HTTP (come fa l'interfaccia).

Presuppone `python server.py` in esecuzione su 127.0.0.1:8778 e una timeline
aperta in Resolve (quella di prova va benissimo).
    python tests/test_api.py [--build]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8778"


def _prepare_timeline() -> str:
    """Crea una timeline grezza di prova e la rende corrente."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import resolve_api as R
    from test_e2e_resolve import ensure_media, make_raw

    media = ensure_media()
    name = make_raw(media, 1704)
    for i in range(1, int(R.project().GetTimelineCount()) + 1):
        t = R.project().GetTimelineByIndex(i)
        if t and t.GetName() == name:
            R.set_current_timeline(t)
            break
    return name


def call(path: str, body=None, timeout=600):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wait_job(label: str):
    while True:
        time.sleep(0.8)
        j = call("/api/job")
        if j["log"]:
            print("   ", j["log"][-1])
        if j["state"] in ("done", "error"):
            if j["state"] == "error":
                raise SystemExit(f"{label}: {j['error']}")
            return j


def main() -> int:
    do_build = "--build" in sys.argv
    if "--reuse" not in sys.argv:
        name = _prepare_timeline()
        print("timeline di prova:", name)
    inv = call("/api/inventory")
    if not inv.get("ok"):
        print("Resolve non disponibile:", inv.get("error"))
        return 1
    print(f"timeline «{inv['timeline']}» {inv['fps']}fps {inv['seconds']}s "
          f"video={[t['index'] for t in inv['video_tracks']]} "
          f"audio={[t['index'] for t in inv['audio_tracks']]}")
    print("default:", inv["default_video"], inv["default_audio"])

    vids = inv["default_video"] or [t["index"] for t in inv["video_tracks"]][:2]
    aud = inv["default_audio"] or 1
    print("analizzo…")
    t0 = time.time()
    r = call("/api/analyze", {"audio_track": aud, "video_tracks": vids,
                              "speakers": 2, "transcribe": True})
    if not r.get("ok"):
        print("errore analisi:", r)
        return 1
    j = wait_job("analisi")
    print(f"analisi in {time.time() - t0:.1f}s")

    st = call("/api/state")
    meta = st["meta"]
    print("voci:", json.dumps(meta.get("diarization"), ensure_ascii=False))
    print("speaker:", json.dumps(meta.get("speakers"), ensure_ascii=False))
    for t in st["turns"][:4]:
        print(f"   {t['start']:6.2f}-{t['end']:6.2f} v{t['speaker']} "
              f"keep={t['keep']} «{(t['text'] or '')[:52]}»")

    # scarto la voce 2 (cameraman) e ricalcolo
    turns = [dict(t) for t in st["turns"]]
    for t in turns:
        t["keep"] = t["speaker"] == meta["speakers"][0]["id"] if meta["speakers"] else True
    plan = call("/api/plan", {"turns": turns, "preview": True})
    if not plan.get("ok"):
        print("errore piano:", plan)
        return 1
    print("piano:", json.dumps(plan["plan"]["stats"], ensure_ascii=False))
    print("anteprima:", plan["preview"]["url"], plan["preview"]["seconds"], "s")

    ok = plan["plan"]["stats"]["kept_s"] > 5 and plan["preview"]["seconds"] > 5

    if do_build:
        print("costruisco la timeline…")
        call("/api/build", {"video_tracks": vids, "audio_track": aud})
        j = wait_job("build")
        res = j["result"] or {}
        print("build:", json.dumps({k: v for k, v in res.items() if k != "verification"},
                                   ensure_ascii=False))
        print("verifica:", json.dumps(res.get("verification"), ensure_ascii=False))
        ok = ok and bool(res.get("ok"))

    print("\nESITO:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
