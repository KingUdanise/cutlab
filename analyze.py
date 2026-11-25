"""Analisi audio di CutLab: segnale di riferimento, VAD, diarizzazione, ASR.

Idea portante: invece di analizzare "il file" si costruisce un **segnale di
riferimento allineato alla timeline**, cioe' un array di campioni che parte dal
frame `start` della timeline e arriva a `end`, con i pezzi audio della traccia
scelta messi al loro posto e zeri dove non c'e' audio.

Cosi' il tempo di analisi coincide con il tempo della timeline:
    frame_timeline = start + t_sec * fps
e non serve nessuna tabella di conversione (che e' la prima cosa che si sbaglia).

Livelli:
  1. `reference_signal()`  -> np.float32 16 kHz, allineato alla timeline
  2. `speech_regions()`    -> VAD Silero (dentro faster-whisper, ONNX, no torch)
  3. `embed_utterances()`  -> embedding di voce ECAPA (speechbrain) o fallback MFCC
  4. `cluster()`           -> 2+ voci con AgglomerativeClustering (cosine)
  5. `transcribe()`        -> opzionale, faster-whisper, per leggere le battute in UI
"""
from __future__ import annotations

import os
import subprocess
import wave

import numpy as np

SR = 16000                     # sample rate di analisi
SR_OUT = 48000                 # sample rate dell'anteprima audio

_DECODE_CACHE: dict[tuple[str, float, int], np.ndarray] = {}


# --------------------------------------------------------------------------- decodifica

def decode_16k(path: str, sr: int = SR) -> np.ndarray:
    """Decodifica un file audio/video in mono float32 via ffmpeg (nessun torch)."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime, st.st_size)
    except OSError:
        key = (path, 0.0, 0)
    if key in _DECODE_CACHE:
        return _DECODE_CACHE[key]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path,
           "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg non riesce a leggere {os.path.basename(path)}: "
                           f"{p.stderr.decode('utf-8', 'replace')[:200]}")
    data = np.frombuffer(p.stdout, dtype="<f4").astype(np.float32, copy=False)
    if len(_DECODE_CACHE) > 6:
        _DECODE_CACHE.clear()
    _DECODE_CACHE[key] = data
    return data


def reference_signal(track_items: list[dict], start: int, end: int, fps: float) -> np.ndarray:
    """Segnale 16 kHz allineato alla timeline nel range [start, end) in frame."""
    n = max(1, int(round((end - start) / fps * SR)))
    sig = np.zeros(n, dtype=np.float32)
    for it in track_items:
        if it.get("is_generator") or not it.get("path"):
            continue
        a = max(start, it["tl_start"])
        b = min(end, it["tl_end"])
        if b <= a or it.get("src_in") is None:
            continue
        media_fps = float(it.get("media_fps") or fps)
        try:
            full = decode_16k(it["path"])
        except Exception:
            continue
        off_frames = it["src_in"] + (a - it["tl_start"])
        s0 = int(round(off_frames / media_fps * SR))
        nd = int(round((b - a) / fps * SR))
        seg = full[s0:s0 + nd]
        if len(seg) == 0:
            continue
        o = int(round((a - start) / fps * SR))
        o = max(0, min(o, n))
        room = n - o
        seg = seg[:room]
        if len(seg):
            sig[o:o + len(seg)] = seg
    return sig


def peaks(sig: np.ndarray, buckets: int = 2400) -> dict:
    """min/max per bucket: e' quello che serve a disegnare la forma d'onda."""
    buckets = max(64, min(int(buckets), 20000))
    if len(sig) == 0:
        return {"min": [], "max": [], "buckets": buckets}
    idx = np.linspace(0, len(sig), buckets + 1).astype(np.int64)
    lo = np.empty(buckets, dtype=np.float32)
    hi = np.empty(buckets, dtype=np.float32)
    for i in range(buckets):
        a, b = idx[i], max(idx[i] + 1, idx[i + 1])
        chunk = sig[a:b]
        lo[i] = chunk.min()
        hi[i] = chunk.max()
    return {"min": lo.round(4).tolist(), "max": hi.round(4).tolist(), "buckets": buckets}


# --------------------------------------------------------------------------- VAD

def speech_regions(sig: np.ndarray, min_silence_ms: int = 120,
                   min_speech_ms: int = 120, pad_ms: int = 0,
                   threshold: float = 0.5) -> list[tuple[float, float]]:
    """Regioni di parlato in secondi (tempo di analisi = tempo di timeline).

    Silero VAD (quello di faster-whisper): lavora sulla probabilita' di voce,
    non sull'energia. E' la differenza abissale rispetto a una soglia in dB:
    il rumore di fondo e i respiri non contano, e una parola sussurrata si'.
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(
        threshold=threshold,
        min_speech_duration_ms=int(min_speech_ms),
        min_silence_duration_ms=int(min_silence_ms),
        speech_pad_ms=int(pad_ms),
    )
    stamps = get_speech_timestamps(sig, vad_options=opts, sampling_rate=SR)
    out = []
    for s in stamps:
        a = s["start"] / SR
        b = s["end"] / SR
        if b > a:
            out.append((round(a, 3), round(b, 3)))
    return out


# --------------------------------------------------------------------------- diarizzazione

def merge_to_utterances(regions: list[tuple[float, float]], gap_s: float = 0.35) -> list[dict]:
    """Unisce le regioni VAD in "battute": serve a dare a ogni voce un campione
    abbastanza lungo da poterla riconoscere."""
    out: list[dict] = []
    for a, b in regions:
        if out and a - out[-1]["end"] <= gap_s:
            out[-1]["end"] = b
        else:
            out.append({"start": a, "end": b})
    for u in out:
        u["dur"] = round(u["end"] - u["start"], 3)
    return out


def _mfcc_embed(sig: np.ndarray, utts: list[dict],
                regions: list[tuple[float, float]] | None = None) -> np.ndarray:
    """Senza reti neurali: statistica di MFCC + pitch + **livello**.

    Il livello (RMS in dB) sembra un dettaglio e invece e' il segnale piu' forte
    in questo caso d'uso: il lav e' addosso all'interlocutore, quindi il
    cameraman che parla da due metri arriva piu' basso e piu' riverberato. Con
    due voci dello stesso sesso e' spesso l'unica cosa che le distingue.
    """
    import librosa

    rows = []
    for u in utts:
        y = _speech_only(sig, u, regions)
        if len(y) < int(0.2 * SR):
            y = np.pad(y, (0, int(0.2 * SR) - len(y)))
        m = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=20)
        f0 = librosa.yin(y, fmin=70, fmax=400, sr=SR)
        rms = float(np.sqrt(np.mean(y ** 2)) + 1e-9)
        rows.append(np.concatenate([
            m.mean(axis=1), m.std(axis=1),
            [np.median(f0), np.std(f0)],
            [20.0 * np.log10(rms), rms],
        ]))
    x = np.vstack(rows)
    return (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-6)


_ECAPA = None
_LAST_EMBED_ERROR: list[str] = []


def _ecapa():
    """Encoder ECAPA-TDNN (speechbrain). Modello ~80 MB, scaricato una volta."""
    global _ECAPA
    if _ECAPA is not None:
        return _ECAPA
    from speechbrain.inference.speaker import EncoderClassifier

    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "models")
    os.makedirs(cache, exist_ok=True)
    # Su questa macchina (RTX 5090, sm_120) la build torch cu124 non ha kernel:
    # qualunque operazione in CUDA esplode. ECAPA e' un modello minuscolo, in CPU
    # va piu' che bene. Override con CUTLAB_SPK_DEVICE=cuda se un giorno servisse.
    dev = os.environ.get("CUTLAB_SPK_DEVICE", "cpu")
    _ECAPA = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=cache, run_opts={"device": dev})
    return _ECAPA


def _z(x: np.ndarray) -> np.ndarray:
    return (x - x.mean(axis=0)) / (x.std(axis=0) + 1e-6)


def _speech_only(sig: np.ndarray, u: dict, regions: list[tuple[float, float]] | None) -> np.ndarray:
    """Campioni della battuta **senza i silenzi interni**.

    Un embedding calcolato su 2 s di parlato + 1 s di pausa descrive la pausa
    (rumore di fondo) quanto la voce: qui si concatenano solo le parti in cui
    il VAD ha sentito voce.
    """
    if not regions:
        return sig[int(u["start"] * SR):int(u["end"] * SR)].astype(np.float32)
    parts = []
    for a, b in regions:
        if b <= u["start"] or a >= u["end"]:
            continue
        a2, b2 = max(a, u["start"]), min(b, u["end"])
        if b2 > a2:
            parts.append(sig[int(a2 * SR):int(b2 * SR)])
    if not parts:
        return sig[int(u["start"] * SR):int(u["end"] * SR)].astype(np.float32)
    return np.concatenate(parts).astype(np.float32)


def embed_utterances(sig: np.ndarray, utts: list[dict],
                     regions: list[tuple[float, float]] | None = None) -> tuple[np.ndarray, str]:
    """Embedding per battuta. Restituisce (matrice, nome del metodo usato).

    Si usano **due** rappresentazioni insieme: ECAPA (timbro, rete neurale) e
    la statistica di MFCC+pitch (robusta quando i timbri sono diversi ma la rete
    e' incerta). Da sole, ognuna sbaglia su questo materiale; insieme separano.
    Ogni blocco viene standardizzato prima di concatenare, altrimenti le 192
    dimensioni ECAPA schiacciano le 42 di MFCC.
    """
    if not utts:
        return np.zeros((0, 192), dtype=np.float32), "vuoto"
    blocks, names = [], []
    try:
        clf = _ecapa()
        import torch

        with torch.no_grad():
            rows = []
            for u in utts:
                y = _speech_only(sig, u, regions)
                if len(y) < int(0.35 * SR):
                    y = np.pad(y, (0, int(0.35 * SR) - len(y)))
                t = torch.from_numpy(y).unsqueeze(0)
                rows.append(clf.encode_batch(t).reshape(-1).cpu().numpy())
        e = np.vstack(rows).astype(np.float32)
        e = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)
        blocks.append(_z(e))
        names.append("ecapa")
    except Exception as exc:  # noqa: BLE001
        _LAST_EMBED_ERROR.append(f"{type(exc).__name__}: {exc}")
    try:
        m = _mfcc_embed(sig, utts, regions)
        blocks.append(_z(m))
        names.append("mfcc")
    except Exception as exc:  # noqa: BLE001
        _LAST_EMBED_ERROR.append(f"mfcc {type(exc).__name__}: {exc}")
    if not blocks:
        raise RuntimeError("nessun metodo di embedding disponibile")
    x = np.hstack(blocks).astype(np.float32)
    x = x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)
    return x, "+".join(names)


def _silhouette(x: np.ndarray, labels: np.ndarray) -> float:
    try:
        from sklearn.metrics import silhouette_score
        if len(set(labels)) < 2:
            return -1.0
        return float(silhouette_score(x, labels, metric="cosine"))
    except Exception:
        return -1.0


def cluster(x: np.ndarray, utts: list[dict], k: int | str = "auto",
            max_k: int = 4) -> tuple[list[int], int, float]:
    """Raggruppa le battute per voce. Restituisce (label per battuta, k, qualita').

    Con k="auto" prova 1..max_k e tiene il k con silhouette migliore, ma solo se
    ogni cluster ha almeno 2 battute e almeno 1.2 s di parlato: un cluster di
    una sola sillaba e' quasi sempre un falso positivo.
    """
    from sklearn.cluster import AgglomerativeClustering

    n = len(utts)
    if n == 0:
        return [], 0, 0.0
    if n < 3:
        return [0] * n, 1, 0.0

    if k != "auto":
        # numero di voci chiesto esplicitamente: si rispetta, a meno che un
        # cluster resti vuoto (allora meglio dirlo che inventare)
        kk = int(k)
        if kk < 2 or kk >= n:
            return [0] * n, 1, 0.0
        labels = AgglomerativeClustering(
            n_clusters=kk, metric="cosine", linkage="average").fit_predict(x)
        if len(set(labels.tolist())) < 2:
            return [0] * n, 1, 0.0
        return [int(v) for v in labels], len(set(labels.tolist())), _silhouette(x, labels)

    cands = list(range(1, min(max_k, n - 1) + 1))
    best = ([0] * n, -2.0, 1)
    for kk in cands:
        if kk == 1:
            labels = np.zeros(n, dtype=int)
            score = -1.0
        else:
            labels = AgglomerativeClustering(
                n_clusters=kk, metric="cosine", linkage="average").fit_predict(x)
            score = _silhouette(x, labels)
            # un cluster di una battuta sola e' quasi sempre un falso positivo
            if any(sum(1 for l in labels if l == c) < 2 for c in set(labels.tolist())):
                continue
        if score > best[1]:
            best = (labels, score, kk)
    labels, score, kk = best
    return [int(v) for v in labels], kk, round(score, 3)


# --------------------------------------------------------------------------- ASR (opzionale)

WHISPER_MODEL = os.environ.get("CUTLAB_WHISPER", "small")
_WHISPER_CACHE: dict[tuple[str, str, str], object] = {}
_CUDA_DLLS_READY = False


def _register_cuda_dlls() -> None:
    """Registra eventuali DLL CUDA installate via pip (nvidia-cublas/cudnn) su Windows."""
    global _CUDA_DLLS_READY
    if _CUDA_DLLS_READY or os.name != "nt":
        return
    _CUDA_DLLS_READY = True
    try:
        import site
        for base in site.getsitepackages():
            nv = os.path.join(base, "nvidia")
            if not os.path.isdir(nv):
                continue
            for pkg in os.listdir(nv):
                bindir = os.path.join(nv, pkg, "bin")
                if os.path.isdir(bindir):
                    try:
                        os.add_dll_directory(bindir)
                    except Exception:
                        pass
                    if bindir not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def _whisper_model(name: str, dev: str, ctype: str):
    key = (name, dev, ctype)
    m = _WHISPER_CACHE.get(key)
    if m is None:
        from faster_whisper import WhisperModel
        m = WhisperModel(name, device=dev, compute_type=ctype)
        _WHISPER_CACHE[key] = m
    return m


def transcribe(sig: np.ndarray, language: str | None = "it", model: str | None = None):
    """Trascrizione con word timestamps: serve *leggere* le battute in UI e
    verificare che il cameraman sia sparito. Non serve al taglio."""
    _register_cuda_dlls()
    name = model or WHISPER_MODEL
    audio = sig.astype(np.float32)

    candidates: list[tuple[str, str]] = []
    forced_dev = os.environ.get("CUTLAB_WHISPER_DEVICE", "").strip().lower()
    if forced_dev == "cpu":
        candidates = [("cpu", "int8")]
    else:
        try:
            import torch
            if torch.cuda.is_available():
                candidates.append(("cuda", "float16"))
        except Exception:
            pass
        candidates.append(("cpu", "int8"))

    last_err: Exception | None = None
    for dev, ctype in candidates:
        try:
            m = _whisper_model(name, dev, ctype)
            segs, info = m.transcribe(audio, language=language,
                                      word_timestamps=True, vad_filter=False)
            words = []
            for s in segs:
                for w in (s.words or []):
                    words.append({"start": round(w.start, 3), "end": round(w.end, 3),
                                  "text": w.word.strip()})
            return {"words": words, "language": info.language, "device": dev, "model": name}
        except Exception as exc:  # noqa: BLE001
            # Su GPU recenti (es. RTX 5090 sm_120) CTranslate2/cuBLAS puo' fallire
            # durante l'iterazione di segs: si riprova pulitamente in CPU.
            last_err = exc
            _WHISPER_CACHE.pop((name, dev, ctype), None)
            if dev == "cpu":
                raise
    raise last_err or RuntimeError("trascrizione Whisper fallita")


def text_for_utterances(words: list[dict], utts: list[dict]) -> None:
    """Assegna a ogni battuta le parole che la coprono (in place)."""
    for u in utts:
        parts = [w["text"] for w in words
                 if w["end"] > u["start"] + 0.02 and w["start"] < u["end"] - 0.02]
        u["text"] = " ".join(parts).strip()


# --------------------------------------------------------------------------- file audio

def write_wav(path: str, sig: np.ndarray, sr: int) -> str:
    """Scrive un WAV 16 bit mono (usato per l'anteprima e per i test)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    x = np.clip(sig, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path
