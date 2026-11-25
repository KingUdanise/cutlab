"""Test del motore di analisi sul materiale generato da tools/make_test_media.py.

Misura: VAD, diarizzazione contro la ground truth, e quanto taglia il piano.
    python tests/test_analyze.py
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import analyze  # noqa: E402
import cutplan  # noqa: E402

TEST = os.path.join(ROOT, "out", "test")


def load_truth() -> dict:
    with open(os.path.join(TEST, "truth.json"), encoding="utf-8") as fh:
        return json.load(fh)


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def main() -> int:
    truth = load_truth()
    t0 = time.time()
    sig = analyze.decode_16k(os.path.join(TEST, "lav.wav"))
    print(f"decodifica            {len(sig) / analyze.SR:7.2f}s   [{time.time() - t0:.1f}s]")

    # --- VAD
    t0 = time.time()
    regions = analyze.speech_regions(sig)
    utts = analyze.merge_to_utterances(regions)
    print(f"VAD Silero            {len(utts)} battute      [{time.time() - t0:.1f}s]")
    for u, gt in zip(utts, truth["utterances"]):
        print(f"   {u['start']:6.2f}-{u['end']:6.2f}  (gt {gt['start']:6.2f}-{gt['end']:6.2f}"
              f"  iou {iou((u['start'], u['end']), (gt['start'], gt['end'])):.2f})")

    # --- diarizzazione
    t0 = time.time()
    x, method = analyze.embed_utterances(sig, utts)
    labels, k, score = analyze.cluster(x, utts, k="auto")
    print(f"diarizzazione         metodo={method} k={k} silhouette={score} "
          f"[{time.time() - t0:.1f}s]")

    # mappa cluster -> nome vero (per cluster, majority)
    names = {}
    for lab in set(labels):
        votes = {}
        for u, gt, l in zip(utts, truth["utterances"], labels):
            if l == lab:
                votes[gt["speaker"]] = votes.get(gt["speaker"], 0) + 1
        names[lab] = max(votes, key=votes.get) if votes else "?"
    hit = sum(1 for u, gt, l in zip(utts, truth["utterances"], labels)
              if names.get(l) == gt["speaker"])
    print(f"accuratezza speaker   {hit}/{len(utts)}")
    for u, gt, l in zip(utts, truth["utterances"], labels):
        mark = "ok " if names.get(l) == gt["speaker"] else "ERR"
        print(f"   {mark} {u['start']:6.2f}-{u['end']:6.2f}  cluster {l} = {names.get(l):13}"
              f" gt={gt['speaker']}")

    # --- piano di taglio: tiene solo l'interlocutore
    keep_speaker = [lab for lab, n in names.items() if n == "interlocutore"]
    plan = cutplan.build(
        utts, labels, fps=truth["fps"], keep_clusters=keep_speaker,
        min_pause_ms=350, pad_start_ms=120, pad_end_ms=150,
        min_speech_ms=250, total_frames=int(round(truth["duration"] * truth["fps"])))
    stats = cutplan.stats(plan, truth["fps"])
    print(json.dumps(stats, indent=1, ensure_ascii=False))

    # verifica: l'audio tagliato contiene solo l'interlocutore?
    kept_s = 0.0
    leaked = 0.0
    for seg in plan["segments"]:
        a, b = seg["sec"]
        kept_s += b - a
        for gt in truth["utterances"]:
            if gt["speaker"] == "cameraman":
                leaked += max(0.0, min(b, gt["end"]) - max(a, gt["start"]))
    print(f"parlato cameraman rimasto nel taglio: {leaked:.2f}s")
    print(f"durata  {stats['total_s']:.2f}s -> {stats['kept_s']:.2f}s "
          f"(-{stats['removed_pct']:.1f}%)  in {len(plan['segments'])} pezzi")

    ok = hit == len(utts) and leaked < 0.15 and stats["kept_s"] > 20
    print("\nESITO:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
