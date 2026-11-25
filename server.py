"""CutLab - backend locale.

    python server.py            -> http://127.0.0.1:8778

Espone quattro cose: l'inventario della timeline di Resolve, l'analisi audio
(VAD + voci), il piano di taglio (ricalcolabile in millisecondi dall'UI) e la
costruzione della timeline tagliata.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback

import numpy as np
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import analyze  # noqa: E402
import build as builder  # noqa: E402
import cutplan  # noqa: E402
import resolve_api as R  # noqa: E402

OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
STATE_FILE = os.path.join(OUT, "state.json")

app = FastAPI(title="CutLab")


# --------------------------------------------------------------------------- job

class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"
        self.progress = 0.0
        self.log: list[str] = []
        self.error = None
        self.result: dict | None = None

    def add(self, msg: str, prog: float | None = None):
        with self.lock:
            self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
            self.log = self.log[-300:]
            if prog is not None:
                self.progress = float(prog)

    def snap(self) -> dict:
        with self.lock:
            return {"state": self.state, "progress": self.progress,
                    "log": list(self.log), "error": self.error,
                    "result": self.result}


JOB = Job()

# stato dell'analisi: segnale, battute, voci
A: dict = {}


def _save_state():
    try:
        keep = {
            "meta": A.get("meta", {}),
            "turns": A.get("turns", []),
            "params": A.get("params", {}),
            "plan": A.get("plan", {}),
        }
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, ensure_ascii=False, indent=1)
    except Exception:
        traceback.print_exc()


def _load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            saved = json.load(fh)
        A["meta"] = saved.get("meta", {})
        A["turns"] = saved.get("turns", [])
        A["params"] = saved.get("params", {})
        A["plan"] = saved.get("plan", {})
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------- API

@app.get("/api/inventory")
def api_inventory(use_marks: bool = True):
    try:
        inv = R.inventory(use_marks=use_marks)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "offline": True,
                             "error": f"{type(e).__name__}: {e}"}, status_code=200)
    rng = max(1, inv["end"] - inv["start"])
    video = []
    for t in inv["video_tracks"]:
        cov = R.coverage(t, inv["start"], inv["end"])
        video.append({
            "index": t["index"], "clips": t["clip_count"], "generators": t["generators"],
            "names": t["names"][:4], "coverage": round(cov, 3),
            "seconds": round((t["last"] or 0) - (t["first"] or 0), 1) / max(1, inv["fps"]),
        })
    audio = []
    for t in inv["audio_tracks"]:
        cov = R.coverage(t, inv["start"], inv["end"])
        audio.append({
            "index": t["index"], "clips": t["clip_count"],
            "names": t["names"][:4], "coverage": round(cov, 3),
        })
    # default intelligenti: le due tracce video piu' coperte, l'audio piu' coperto
    vids = sorted([v for v in video if v["coverage"] > 0.5], key=lambda v: -v["coverage"])[:2]
    auds = sorted([a for a in audio if a["coverage"] > 0.5], key=lambda a: -a["coverage"])[:1]
    return {
        "ok": True, "resolve": inv["resolve"], "project": inv["project"],
        "timeline": inv["timeline"], "fps": inv["fps"],
        "width": inv["width"], "height": inv["height"],
        "start": inv["start"], "end": inv["end"],
        "seconds": round(rng / inv["fps"], 2),
        "used_marks": inv["used_marks"], "marks": inv["marks"],
        "video_tracks": video, "audio_tracks": audio,
        "default_video": [v["index"] for v in vids],
        "default_audio": auds[0]["index"] if auds else None,
    }


class AnalyzeBody(BaseModel):
    audio_track: int = 1
    video_tracks: list[int] = []
    use_marks: bool = True
    min_pause_ms: int = 350
    pad_start_ms: int = 120
    pad_end_ms: int = 150
    min_speech_ms: int = 250
    speakers: int | str = 2
    transcribe: bool = True
    whisper: str = "small"


@app.post("/api/analyze")
def api_analyze(body: AnalyzeBody):
    if JOB.state == "running":
        return JSONResponse({"ok": False, "error": "analisi gia' in corso"}, status_code=409)

    def run():
        JOB.state, JOB.progress, JOB.error, JOB.log, JOB.result = "running", 0.0, None, [], None
        try:
            JOB.add("leggo la timeline da Resolve", 0.03)
            inv = R.inventory(use_marks=body.use_marks)
            A.clear()
            A["inv"] = inv
            A["params"] = body.model_dump()
            fps = inv["fps"]
            start, end = inv["start"], inv["end"]
            tr = next((t for t in inv["audio_tracks"] if t["index"] == body.audio_track), None)
            if tr is None:
                raise RuntimeError(f"traccia audio {body.audio_track} non trovata")

            JOB.add(f"decodifico l'audio di A{body.audio_track}", 0.12)
            sig = analyze.reference_signal(tr["items"], start, end, fps)
            if float(np.abs(sig).max()) < 1e-4:
                raise RuntimeError("la traccia audio scelta e' muta o vuota")
            A["sig"] = sig
            JOB.add(f"{len(sig) / analyze.SR:.1f}s di audio, rilevo il parlato", 0.3)

            regions = analyze.speech_regions(sig)
            turns = analyze.merge_to_utterances(regions, gap_s=0.55)
            if not turns:
                raise RuntimeError("nessuna voce rilevata: controlla la traccia audio")
            JOB.add(f"{len(turns)} battute rilevate, distinguo le voci", 0.45)

            x, method = analyze.embed_utterances(sig, turns, regions)
            labels, k, score = analyze.cluster(x, turns, k=body.speakers)
            # il cluster che parla di piu' e' l'interlocutore
            dur = {}
            for t, lab in zip(turns, labels):
                dur[lab] = dur.get(lab, 0.0) + (t["end"] - t["start"])
            for t, lab in zip(turns, labels):
                t["speaker"] = int(lab)
                t["keep"] = True
            JOB.add(f"{k} voci (silhouette {score}), trascrivo", 0.6)

            info = {"method": method, "k": k, "silhouette": score}
            if body.transcribe:
                try:
                    res = analyze.transcribe(sig, model=body.whisper)
                    analyze.text_for_utterances(res["words"], turns)
                    info["asr"] = {"model": res["model"], "device": res["device"],
                                   "language": res["language"]}
                    JOB.add(f"trascritte {len(res['words'])} parole ({res['device']})", 0.85)
                except Exception as e:  # noqa: BLE001
                    info["asr_error"] = f"{type(e).__name__}: {e}"
                    JOB.add(f"trascrizione non disponibile: {e}", 0.85)
            for t in turns:
                t.pop("dur", None)
                t["start"] = round(t["start"], 3)
                t["end"] = round(t["end"], 3)
                t["text"] = t.get("text", "")

            A["turns"] = turns
            A["peaks"] = analyze.peaks(sig, 2400)
            A["meta"] = {
                "project": inv["project"], "timeline": inv["timeline"],
                "fps": fps, "start": start, "end": end, "seconds": round((end - start) / fps, 2),
                "audio_track": body.audio_track, "video_tracks": body.video_tracks,
                "diarization": info,
                "speakers": [{"id": int(c), "seconds": round(d, 1),
                              "turns": sum(1 for t in turns if t["speaker"] == c)}
                             for c, d in sorted(dur.items())],
            }
            A["params"] = body.model_dump()
            _recompute_plan()
            _save_state()
            JOB.result = public_state()
            JOB.add("analisi completata", 1.0)
            JOB.state = "done"
        except Exception as e:  # noqa: BLE001
            JOB.error = f"{type(e).__name__}: {e}"
            JOB.add("ERRORE: " + JOB.error)
            traceback.print_exc()
            JOB.state = "error"

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/job")
def api_job():
    return JOB.snap()


@app.get("/api/state")
def api_state():
    return public_state()


def public_state(include_turns: bool = True) -> dict:
    d = {
        "meta": A.get("meta", {}),
        "peaks": A.get("peaks"),
        "plan": A.get("plan", {}),
        "params": A.get("params", {}),
        "offline": not bool(A.get("turns")),
    }
    if include_turns:
        d["turns"] = A.get("turns", [])
    return d


class PlanBody(BaseModel):
    min_pause_ms: int | None = None
    pad_start_ms: int | None = None
    pad_end_ms: int | None = None
    min_speech_ms: int | None = None
    turns: list[dict] | None = None      # overrides completi dall'UI
    preview: bool = True


@app.post("/api/plan")
def api_plan(body: PlanBody):
    if not A.get("turns"):
        return JSONResponse({"ok": False, "error": "nessuna analisi: lancia prima l'analisi"},
                            status_code=200)
    try:
        if body.turns is not None:
            A["turns"] = [_clean_turn(t) for t in body.turns]
        p = A.setdefault("params", {})
        for k in ("min_pause_ms", "pad_start_ms", "pad_end_ms", "min_speech_ms"):
            v = getattr(body, k)
            if v is not None:
                p[k] = int(v)
        A["params"] = p
        plan = _recompute_plan()
        prev = None
        if body.preview:
            prev = _make_preview(plan)
        return {"ok": True, "plan": A["plan"], "preview": prev, "state": public_state(False)}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)


def _clean_turn(t: dict) -> dict:
    return {
        "start": float(t["start"]), "end": float(t["end"]),
        "speaker": int(t.get("speaker", 0)),
        "keep": bool(t.get("keep", True)),
        "text": str(t.get("text", ""))[:400],
    }


def _recompute_plan() -> dict:
    meta = A["meta"]
    fps = meta["fps"]
    start, end = meta["start"], meta["end"]
    p = A.get("params", {})
    turns = A.get("turns", [])
    kept = [t for t in turns if t.get("keep")]
    utts = [{"start": t["start"], "end": t["end"], "dur": t["end"] - t["start"]} for t in kept]
    labels = [0] * len(utts)
    plan = cutplan.build(
        utts, labels, fps=fps, keep_clusters=[0],
        min_pause_ms=int(p.get("min_pause_ms", 350)),
        pad_start_ms=int(p.get("pad_start_ms", 120)),
        pad_end_ms=int(p.get("pad_end_ms", 150)),
        min_speech_ms=int(p.get("min_speech_ms", 250)),
        total_frames=end - start, total_s=(end - start) / fps, frame_offset=start)

    inv = A.get("inv") or {}
    tracks = []
    vt = [t for t in (inv.get("video_tracks") or [])
          if t["index"] in (meta.get("video_tracks") or [])]
    at = [t for t in (inv.get("audio_tracks") or []) if t["index"] == meta.get("audio_track")]
    for t in vt:
        tracks.append(t["items"])
    for t in at:
        tracks.append(t["items"])
    if tracks:
        plan = cutplan.fit_to_tracks(plan, tracks)
    stats = cutplan.stats(plan, fps, total_frames=end - start)
    A["plan"] = {"segments": plan["segments"], "stats": stats,
                 "dropped_frames": plan.get("dropped_frames", 0),
                 "params": plan.get("params", {})}
    return plan


# --------------------------------------------------------------------------- anteprima

def _make_preview(plan: dict) -> dict | None:
    sig = A.get("sig")
    if sig is None:
        return None
    meta = A["meta"]
    start = meta["start"]
    fps = meta["fps"]
    chunks = []
    for s in plan["segments"]:
        a = int(round((s["f0"] - start) / fps * analyze.SR))
        b = int(round((s["f1"] - start) / fps * analyze.SR))
        a, b = max(0, a), min(len(sig), b)
        if b > a:
            chunks.append(sig[a:b])
    if not chunks:
        return None
    joined = np.concatenate(chunks)
    h = hashlib.sha1(f"{len(joined)}:{int(np.abs(joined).sum())}:{len(plan['segments'])}"
                     .encode()).hexdigest()[:12]
    raw = os.path.join(OUT, f"preview_{h}.wav")
    if not os.path.exists(raw):
        analyze.write_wav(raw, joined, analyze.SR)
        mp3 = raw.replace(".wav", ".mp3")
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw,
                            "-ar", "44100", "-b:a", "128k", mp3], check=True)
            raw = mp3
        except Exception:
            pass
    for old in os.listdir(OUT):
        if old.startswith("preview_") and not old.endswith(h + ".mp3") \
                and not old.endswith(h + ".wav"):
            try:
                os.remove(os.path.join(OUT, old))
            except OSError:
                pass
    return {"url": f"/api/preview/{os.path.basename(raw)}", "seconds": round(len(joined) / analyze.SR, 2)}


@app.get("/api/preview/{name}")
def api_preview(name: str):
    p = os.path.join(OUT, os.path.basename(name))
    if not os.path.exists(p):
        return JSONResponse({"error": "non trovato"}, status_code=404)
    return FileResponse(p, media_type="audio/mpeg" if p.endswith(".mp3") else "audio/wav")


# --------------------------------------------------------------------------- build

class BuildBody(BaseModel):
    video_tracks: list[int] = []
    audio_track: int = 1
    name: str = ""
    mark_cuts: bool = True
    name_tracks: bool = True
    dry_run: bool = False


@app.post("/api/build")
def api_build(body: BuildBody):
    if not A.get("plan"):
        return JSONResponse({"ok": False, "error": "nessun piano: analizza e rivedi prima"},
                            status_code=200)
    if JOB.state == "running":
        return JSONResponse({"ok": False, "error": "operazione in corso"}, status_code=409)

    def run():
        JOB.state, JOB.progress, JOB.error, JOB.log, JOB.result = "running", 0.0, None, [], None
        try:
            inv = A["inv"]
            plan = {"segments": A["plan"]["segments"]}
            if body.dry_run:
                JOB.result = {"ok": True, "dry_run": True, "plan": A["plan"]}
                JOB.state = "done"
                return
            JOB.add("salvo un .drp di sicurezza", 0.02)
            backup = R.backup_project("cutlab")
            name = body.name or f"{inv['timeline']} · CUT"
            JOB.add(f"creo la timeline «{name}»", 0.06)
            res = builder.build(
                plan, inv, video_tracks=body.video_tracks, audio_track=body.audio_track,
                fps=inv["fps"], name=name, mark_cuts=body.mark_cuts,
                name_tracks=body.name_tracks,
                progress=lambda m, p=None: JOB.add(m, p))
            res["backup"] = backup
            JOB.result = res
            JOB.add("timeline pronta" if res.get("ok") else "verifica fallita", 1.0)
            JOB.state = "done"
        except Exception as e:  # noqa: BLE001
            JOB.error = f"{type(e).__name__}: {e}"
            JOB.add("ERRORE: " + JOB.error)
            traceback.print_exc()
            JOB.state = "error"

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.post("/api/backup")
def api_backup():
    return {"ok": True, "path": R.backup_project("manuale")}


@app.get("/api/health")
def api_health():
    return {"ok": True, "pid": os.getpid()}


app.mount("/", StaticFiles(directory=os.path.join(HERE, "web"), html=True), name="web")

_load_state()

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CUTLAB_PORT", "8778"))
    print(f"CutLab -> http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
