"""Test end-to-end dentro DaVinci Resolve.

Crea nel progetto corrente (dentro un bin 'CutLab TEST') una timeline grezza come
quella vera: V1 = cam A, V2 = cam B (con 2 secondi di offset nella sorgente, per
provare che la mappa sorgente per traccia sia giusta), A1 = lav.

Poi: inventario -> VAD -> diarizzazione -> piano -> build -> verifica in lettura.

Non tocca nessuna timeline esistente: lavora solo dentro il bin di prova.
    python tests/test_e2e_resolve.py [--keep]
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import analyze  # noqa: E402
import build as builder  # noqa: E402
import cutplan  # noqa: E402
import resolve_api as R  # noqa: E402

TEST = os.path.join(ROOT, "out", "test")
BIN = "CutLab TEST"
RAW = "CutLab RAW test"
FPS_EXPECT = 24


def bin_folder():
    """La cartella di prova, sempre rifetchata: un proxy preso prima di un
    refresh/create/delete e' vecchio e rende il pool incoerente."""
    pool = R.media_pool()
    pool.RefreshFolders()
    for f in pool.GetRootFolder().GetSubFolderList() or []:
        if f.GetName() == BIN:
            return f
    return pool.AddSubFolder(pool.GetRootFolder(), BIN)


def ensure_media() -> dict:
    pool = R.media_pool()
    folder = bin_folder()
    pool.SetCurrentFolder(folder)
    if not os.path.exists(os.path.join(TEST, "lav.wav")):
        raise RuntimeError("manca out/test: lancia prima tools/make_test_media.py")
    have = {os.path.basename(c.GetClipProperty("File Path") or ""): c
            for c in folder.GetClipList() or []}
    want = ["cam_a.mp4", "cam_b.mp4", "lav.wav"]
    miss = [w for w in want if w not in have]
    if miss:
        pool.ImportMedia([os.path.join(TEST, m) for m in miss])
        folder = bin_folder()
        pool.SetCurrentFolder(folder)
        have = {os.path.basename(c.GetClipProperty("File Path") or ""): c
                for c in folder.GetClipList() or []}
    return have


def make_raw(media: dict, frames: int, cam_b_offset: int = 48) -> str:
    """Timeline grezza come quella vera. Nome unico e nessuna cancellazione:
    cancellare timeline dentro lo stesso processo lascia il media pool in uno
    stato in cui AppendToTimeline fallisce in silenzio."""
    p = R.project()
    pool = R.media_pool()
    name = f"{RAW} {time.strftime('%H%M%S')}"
    tl = pool.CreateEmptyTimeline(name)
    if tl is None:
        raise RuntimeError(f"CreateEmptyTimeline({name!r}) ha restituito None")
    if not R.set_current_timeline(tl):
        raise RuntimeError("Resolve non è passato alla timeline di prova")
    tl.AddTrack("video")
    R.reset_media_index()
    seq = [("cam_a.mp4", 0, frames, 1, 1),
           ("cam_b.mp4", cam_b_offset, cam_b_offset + frames, 1, 2),
           ("lav.wav", 0, frames, 2, 1)]
    for fname, a, b, mt, tr in seq:
        path = os.path.join(TEST, fname)
        if not R.append_range(None, a, b, mt, tr, path=path):
            raise RuntimeError(f"append fallito su traccia {tr} ({fname})")
    got = (len(tl.GetItemListInTrack("video", 1) or []),
           len(tl.GetItemListInTrack("video", 2) or []),
           len(tl.GetItemListInTrack("audio", 1) or []))
    if got != (1, 1, 1):
        raise RuntimeError(f"timeline grezza incompleta: V1/V2/A1 = {got}")
    return name


def main() -> int:
    keep = "--keep" in sys.argv
    media = ensure_media()
    frames = 1704           # 71 s a 24 fps
    raw_name = make_raw(media, frames)
    print(f"timeline grezza «{raw_name}»: 3 clip, {frames} frame")

    inv = R.inventory(use_marks=False)
    assert inv["fps"] == FPS_EXPECT, inv["fps"]
    print(f"inventario: video={[t['index'] for t in inv['video_tracks']]} "
          f"audio={[t['index'] for t in inv['audio_tracks']]} range={inv['start']}..{inv['end']}")

    v1, v2 = inv["video_tracks"][0], inv["video_tracks"][1]
    a1 = inv["audio_tracks"][0]
    start, end = inv["start"], inv["end"]

    # --- segnale di riferimento allineato alla timeline
    sig = analyze.reference_signal(a1["items"], start, end, inv["fps"])
    print(f"segnale riferimento: {len(sig) / analyze.SR:.2f}s "
          f"(atteso {(end - start) / inv['fps']:.2f}s)")

    regions = analyze.speech_regions(sig)
    utts = analyze.merge_to_utterances(regions)
    x, method = analyze.embed_utterances(sig, utts)
    labels, k, score = analyze.cluster(x, utts, k="auto")
    print(f"VAD {len(utts)} battute, diarizzazione metodo={method} k={k} sil={score}")

    # chi parla di piu' = interlocutore (in UI lo decide l'utente, qui e' il default)
    dur = {}
    for u, lab in zip(utts, labels):
        dur[lab] = dur.get(lab, 0.0) + u["dur"]
    keep_cluster = max(dur, key=dur.get) if dur else 0
    print(f"parlato per voce: {[(c, round(d, 1)) for c, d in sorted(dur.items())]} "
          f"-> tengo {keep_cluster}")

    plan = cutplan.build(utts, labels, fps=inv["fps"], keep_clusters=[keep_cluster],
                         min_pause_ms=350, pad_start_ms=120, pad_end_ms=150,
                         min_speech_ms=250, total_frames=end - start,
                         frame_offset=start)
    tracks = [v1["items"], v2["items"], a1["items"]]
    plan = cutplan.fit_to_tracks(plan, tracks)
    st = cutplan.stats(plan, inv["fps"], total_frames=end - start)
    print("piano:", json.dumps(st, ensure_ascii=False))
    if plan.get("dropped"):
        print(f"  scartati {plan['dropped_frames']} frame non coperti da tutte le tracce")

    # --- build
    res = builder.build(plan, inv, video_tracks=[1, 2], audio_track=1,
                        fps=inv["fps"], name="CutLab E2E test",
                        progress=lambda m, p=None: print(f"   · {m}"))
    print("build:", json.dumps({k2: v for k2, v in res.items() if k2 != 'verification'},
                               ensure_ascii=False))
    print("verifica:", json.dumps(res["verification"], ensure_ascii=False))

    # --- controllo extra: la mappa sorgente per traccia e' rispettata?
    tl = R.project().GetCurrentTimeline()
    va = [int(i.GetSourceStartFrame()) for i in (tl.GetItemListInTrack("video", 1) or [])]
    vb = [int(i.GetSourceStartFrame()) for i in (tl.GetItemListInTrack("video", 2) or [])]
    off = {(b - a) for a, b in zip(va, vb)}
    print(f"offset sorgente V2-V1 per pezzo: {off} (atteso {{48}})")

    a_paths = {os.path.basename(i.GetMediaPoolItem().GetClipProperty("File Path"))
               for i in (tl.GetItemListInTrack("audio", 1) or [])}
    print(f"file presenti su A1: {a_paths} (atteso solo lav.wav)")

    ok = (res["ok"] and off == {48} and a_paths == {"lav.wav"}
          and len(va) == len(plan["segments"]) and st["kept_s"] > 20)

    if not keep:
        try:
            R.delete_timelines([res["timeline"], raw_name])
            print(f"timeline di prova eliminate")
        except Exception as e:
            print("non ho eliminato le timeline:", e)

    print("\nESITO:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
