"""Dal parlato ai pezzi da tenere.

Tre decisioni, in quest'ordine, e ognuna ha un perche':

1. **filtro voce** — le battute del cameraman non si "abbassano": si tolgono.
2. **padding** — un taglio a filo parola mangia le consonanti (la "s" e la "t"
   iniziali stanno prima dell'attacco che il VAD segna), quindi si allarga di
   ~120 ms davanti e ~150 ms dietro.
3. **fusione** — sotto i `min_pause_ms` la pausa non si tocca: toglierla crea un
   salto innaturale. Sopra quella soglia si taglia.

Il risultato e' sempre in **frame** e sempre crescente, perche' deve essere
identico su tutte le tracce.
"""
from __future__ import annotations


def build(utts: list[dict], labels: list[int], fps: float, keep_clusters,
          min_pause_ms: int = 350, pad_start_ms: int = 120, pad_end_ms: int = 150,
          min_speech_ms: int = 250, total_frames: int = 0,
          total_s: float | None = None, frame_offset: int = 0) -> dict:
    """Costruisce il piano di taglio.

    utts   : [{"start","end","dur","text"?}] in secondi di analisi
    labels : cluster di voce per battuta
    keep   : cluster da tenere (None = tieni tutto, serve per "solo pause")
    """
    keep = None if keep_clusters is None else set(int(c) for c in keep_clusters)
    total_s = total_s if total_s is not None else (total_frames / fps if fps else 0.0)

    pad_a = pad_start_ms / 1000.0
    pad_b = pad_end_ms / 1000.0

    cands: list[list[float]] = []
    for u, lab in zip(utts, labels):
        if keep is not None and int(lab) not in keep:
            continue
        a = max(0.0, u["start"] - pad_a)
        b = min(total_s, u["end"] + pad_b)
        if b > a:
            cands.append([a, b])
    cands.sort()

    # fusione delle pause troppo corte per essere tagliate
    gap = min_pause_ms / 1000.0
    merged: list[list[float]] = []
    for a, b in cands:
        if merged and a - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    # scarta i pezzi troppo brevi (un colpo di tosse, un "mm" isolato)
    min_len = min_speech_ms / 1000.0
    merged = [m for m in merged if (m[1] - m[0]) >= min_len] or merged

    segments = []
    for a, b in merged:
        f0 = int(round(a * fps)) + int(frame_offset)
        f1 = int(round(b * fps)) + int(frame_offset)
        if f1 <= f0:
            continue
        if total_frames:
            f0 = max(int(frame_offset), min(f0, int(frame_offset) + total_frames))
            f1 = max(int(frame_offset), min(f1, int(frame_offset) + total_frames))
        if f1 <= f0:
            continue
        if segments and f0 <= segments[-1]["f1"]:
            segments[-1]["f1"] = max(segments[-1]["f1"], f1)
            segments[-1]["sec"] = [segments[-1]["sec"][0], round(b, 3)]
            continue
        segments.append({"f0": f0, "f1": f1, "sec": [round(a, 3), round(b, 3)]})

    return {"segments": segments, "keep_clusters": sorted(keep) if keep else None,
            "params": {"min_pause_ms": min_pause_ms, "pad_start_ms": pad_start_ms,
                       "pad_end_ms": pad_end_ms, "min_speech_ms": min_speech_ms}}


def stats(plan: dict, fps: float, total_frames: int = 0) -> dict:
    segs = plan["segments"]
    kept_frames = sum(s["f1"] - s["f0"] for s in segs)
    total = total_frames or (int(round(max((s["f1"] for s in segs), default=0))))
    total_s = total / fps if fps else 0.0
    kept_s = kept_frames / fps if fps else 0.0
    durs = [(s["f1"] - s["f0"]) / fps for s in segs]
    return {
        "total_s": round(total_s, 2),
        "kept_s": round(kept_s, 2),
        "removed_s": round(total_s - kept_s, 2),
        "removed_pct": round(100.0 * (total_s - kept_s) / total_s, 1) if total_s else 0.0,
        "pieces": len(segs),
        "cuts": max(0, len(segs) - 1) * 2,
        "shortest_s": round(min(durs), 2) if durs else 0.0,
        "longest_s": round(max(durs), 2) if durs else 0.0,
    }


# --------------------------------------------------------------------------- validita' per traccia

def _covers(items: list[dict], f0: int, f1: int) -> bool:
    for it in items:
        if it.get("is_generator") or not it.get("path"):
            continue
        if it["tl_start"] <= f0 and it["tl_end"] >= f1:
            return True
    return False


def _boundaries(tracks: list[list[dict]]) -> list[int]:
    b = set()
    for tr in tracks:
        for it in tr:
            if it.get("path") and not it.get("is_generator"):
                b.add(it["tl_start"])
                b.add(it["tl_end"])
    return sorted(b)


def fit_to_tracks(plan: dict, tracks: list[list[dict]]) -> dict:
    """Spezza i pezzi sui confini dei clip e scarta quelli che una traccia non copre.

    Perche' e' necessario: se un pezzo da tenere attraversa un buco (o la fine di
    una clip) su *una* traccia, appendendo i sub-range quella traccia diventa piu'
    corta e tutto il resto scivola fuori sync. Meglio perdere quei frame e dirlo.
    """
    if not tracks:
        return plan
    bounds = _boundaries(tracks)
    out, dropped = [], []
    for seg in plan["segments"]:
        f0, f1 = seg["f0"], seg["f1"]
        cuts = [f0] + [b for b in bounds if f0 < b < f1] + [f1]
        for a, b in zip(cuts, cuts[1:]):
            if b <= a:
                continue
            if all(_covers(tr, a, b) for tr in tracks):
                out.append({"f0": a, "f1": b, "sec": seg.get("sec")})
            else:
                dropped.append([a, b])
    # i pezzi NON si ri-fondono: se un pezzo da tenere attraversa il confine fra
    # due clip, serve davvero spezzarlo, altrimenti il builder non puo'
    # rappresentarlo con un solo sub-range sorgente.
    plan = dict(plan)
    plan["segments"] = sorted(out, key=lambda s: s["f0"])
    plan["dropped"] = dropped
    plan["dropped_frames"] = sum(b - a for a, b in dropped)
    return plan


def clip_ranges(track_items: list[dict], f0: int, f1: int) -> list[dict]:
    """Sotto-range sorgente della traccia che coprono [f0,f1) in timeline."""
    out = []
    for it in track_items:
        if it.get("is_generator") or not it.get("path"):
            continue
        a = max(f0, it["tl_start"])
        b = min(f1, it["tl_end"])
        if b <= a:
            continue
        src_in = it["src_in"] + (a - it["tl_start"])
        out.append({
            "media_id": it["media_id"],
            "src_in": int(src_in),
            "src_out": int(src_in + (b - a)),
            "frames": int(b - a),
            "name": it.get("name"),
        })
    return out
