import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze  # noqa: E402

sig = analyze.decode_16k("out/test/lav.wav")
regions = analyze.speech_regions(sig)
truth = json.load(open("out/test/truth.json", encoding="utf-8"))

for gap in (0.35, 0.55, 0.8):
    utts = analyze.merge_to_utterances(regions, gap_s=gap)
    x, method = analyze.embed_utterances(sig, utts, regions)
    lab, kk, sc = analyze.cluster(x, utts, k=2)
    names = {}
    for u, l in zip(utts, lab):
        votes = {}
        for g in truth["utterances"]:
            ov = min(u["end"], g["end"]) - max(u["start"], g["start"])
            if ov > 0.05:
                votes[g["speaker"]] = votes.get(g["speaker"], 0) + ov
        if votes:
            names.setdefault(l, []).append(max(votes, key=votes.get))
    major = {l: max(set(v), key=v.count) for l, v in names.items()}
    hit = 0
    for u, l in zip(utts, lab):
        votes = {}
        for g in truth["utterances"]:
            ov = min(u["end"], g["end"]) - max(u["start"], g["start"])
            if ov > 0.05:
                votes[g["speaker"]] = votes.get(g["speaker"], 0) + ov
        gt = max(votes, key=votes.get) if votes else "?"
        hit += major.get(l) == gt
    print(f"gap={gap}: {len(utts)} battute, k={kk}, sil={sc}, accuratezza {hit}/{len(utts)}")
    for u, l in zip(utts, lab):
        votes = {}
        for g in truth["utterances"]:
            ov = min(u["end"], g["end"]) - max(u["start"], g["start"])
            if ov > 0.05:
                votes[g["speaker"]] = votes.get(g["speaker"], 0) + ov
        gt = max(votes, key=votes.get) if votes else "?"
        mark = "ok " if major.get(l) == gt else "ERR"
        print(f"   {mark} {u['start']:6.2f}-{u['end']:6.2f} cluster{l}={major.get(l)} gt={gt}")
