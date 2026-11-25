"""Costruzione della timeline tagliata, sincronizzata su tutte le tracce.

Il principio: **non si taglia, si ricostruisce**. Per ogni pezzo da tenere si
appende il sotto-range sorgente su *ogni* traccia (V1, V2, A1) con il proprio
`trackIndex`, quindi i tre appoggi cadono per costruzione sugli stessi frame.
Nessun razor, nessun ripple, nessuna clip che "sparisce".

Misure fatte sull'API 21.1 (vedi tools/probe_resolve.py, che le rifa' da zero):
  * `endFrame` e' ESCLUSIVO -> durata = endFrame - startFrame;
  * `trackIndex` e' rispettato se la traccia esiste (AddTrack prima);
  * `mediaType: 1` NON porta con se' l'audio della clip video  (Q3: A1 resta vuota)
  * `mediaType: 2` scrive l'audio sulla traccia audio indicata;
  * 3 segmenti su V1+V2+A1 finiscono alla stesso frame, verificato.

L'ultimo passaggio e' sempre una **verifica in lettura**: se una traccia non
combacia, si dice a voce alta invece di consegnare una timeline sfalsata.
"""
from __future__ import annotations

import time

import cutplan
import resolve_api as R


class BuildError(RuntimeError):
    pass


def _unique_name(base: str, tries: int = 40) -> str:
    p = R.project()
    existing = set()
    for i in range(1, int(p.GetTimelineCount()) + 1):
        t = p.GetTimelineByIndex(i)
        try:
            existing.add(t.GetName())
        except Exception:
            continue
    if base not in existing:
        return base
    for i in range(2, tries):
        cand = f"{base} {i}"
        if cand not in existing:
            return cand
    return f"{base} {int(time.time()) % 10000}"


def _src_delta(item: dict, frames_timeline: int, fps: float) -> int:
    """Converte N frame di timeline in frame del media (se il media ha altro fps)."""
    media_fps = float(item.get("media_fps") or fps)
    if abs(media_fps - fps) < 0.001:
        return frames_timeline
    return int(round(frames_timeline / fps * media_fps))


def pieces_for(track_items: list[dict], f0: int, f1: int, fps: float) -> list[dict]:
    """Sotto-range sorgente della traccia che coprono [f0, f1) in timeline."""
    out = []
    for it in track_items:
        if it.get("is_generator") or not it.get("path"):
            continue
        a = max(f0, it["tl_start"])
        b = min(f1, it["tl_end"])
        if b <= a:
            continue
        src_in = it["src_in"] + _src_delta(it, a - it["tl_start"], fps)
        out.append({
            "media_id": it["media_id"],
            "path": it.get("path"),
            "start": int(src_in),
            "end": int(src_in + _src_delta(it, b - a, fps)),
            "name": it.get("name"),
        })
    return out


def build(plan: dict, inv: dict, video_tracks: list[int], audio_track: int,
          fps: float | None = None, name: str = "", mark_cuts: bool = True,
          name_tracks: bool = True, progress=None) -> dict:
    """Crea la timeline tagliata. `video_tracks`/`audio_track` sono indici 1-based."""
    fps = fps or inv["fps"]
    segs = plan.get("segments") or []
    if not segs:
        raise BuildError("Nessun pezzo da tenere: controlla soglie e voci selezionate.")

    vmeta = {t["index"]: t for t in inv["video_tracks"]}
    ameta = {t["index"]: t for t in inv["audio_tracks"]}
    missing = [i for i in video_tracks if i not in vmeta] + \
              ([audio_track] if audio_track not in ameta else [])
    if missing:
        raise BuildError(f"Tracce non trovate nella timeline: {missing}")

    # indice dei media: unique_id -> MediaPoolItem. Va fatto DOPO aver creato la
    # timeline: creare/eliminare timeline rinfresca il pool e invalida gli handle
    # (un handle morto fa fallire AppendToTimeline restituendo None, senza errore).
    needed = set()
    for s in segs:
        for i in video_tracks:
            for pc in pieces_for(vmeta[i]["items"], s["f0"], s["f1"], fps):
                needed.add(pc["media_id"])
        for pc in pieces_for(ameta[audio_track]["items"], s["f0"], s["f1"], fps):
            needed.add(pc["media_id"])

    p = R.project()
    pool = R.media_pool()
    new_name = _unique_name(name or "CUT")
    tl = pool.CreateEmptyTimeline(new_name)
    if not tl:
        raise BuildError("Resolve non ha creato la timeline nuova.")
    ready = R.set_current_timeline(tl)
    if not ready:
        raise BuildError("Resolve non è passato alla timeline nuova: riprova "
                         "(a volte serve chiudere e riaprire la pagina Edit).")
    new_name = ready

    idx = R.media_index(with_folders=True)
    lost = [m for m in needed if m not in idx]
    if lost:
        raise BuildError(f"{len(lost)} clip non sono piu' nel media pool: ricarica il progetto.")
    R.reset_media_index()
    R._IDX.update(idx)  # riusa l'indice appena costruito
    pool = R.media_pool()

    def append(pc: dict, mtype: int, track: int, record: int) -> bool:
        return R.append_range(pc["media_id"], pc["start"], pc["end"], mtype, track,
                              path=pc.get("path"), record_frame=record)

    while int(tl.GetTrackCount("video")) < len(video_tracks):
        if not tl.AddTrack("video"):
            raise BuildError("Non riesco ad aggiungere una traccia video.")
    if int(tl.GetTrackCount("audio")) < 1:
        tl.AddTrack("audio")

    def log(msg, prog=None):
        if progress:
            progress(msg, prog)

    log(f"timeline «{new_name}» creata", 0.05)

    expected: dict[int | str, list[tuple[int, int]]] = {i: [] for i in video_tracks}
    expected["a"] = []
    try:
        base_frame = int(tl.GetStartFrame() or 0)
    except Exception:
        base_frame = 0
    offset = base_frame
    total = len(segs)
    appended = 0

    for n, s in enumerate(segs, start=1):
        dur = s["f1"] - s["f0"]
        if dur <= 0:
            continue
        for vi, ti in enumerate(video_tracks, start=1):
            pcs = pieces_for(vmeta[ti]["items"], s["f0"], s["f1"], fps)
            if not pcs or sum(pc["end"] - pc["start"] for pc in pcs) != dur:
                raise BuildError(
                    f"segmento {n}: la traccia V{ti} ha un buco nel range "
                    f"[{s['f0']}..{s['f1']}). Rilancia il piano con la validazione tracce.")
            sub_off = offset
            for pi, pc in enumerate(pcs):
                pc_dur = pc["end"] - pc["start"]
                if not append(pc, 1, vi, sub_off):
                    raise BuildError(f"append fallito su V{vi} (segmento {n}, pezzo {pi + 1})")
                expected[ti].append((sub_off, pc_dur))
                sub_off += pc_dur
                appended += 1
                if n == 1 and vi == 1 and pi == 0:
                    # primo append: se non e' atterrato, l'ambiente non e' pronto
                    landed = tl.GetItemListInTrack("video", 1) or []
                    if not landed:
                        raise BuildError("Resolve non ha registrato il primo clip: "
                                         "la timeline corrente non è quella nuova. Riprova.")

        pcs = pieces_for(ameta[audio_track]["items"], s["f0"], s["f1"], fps)
        if not pcs or sum(pc["end"] - pc["start"] for pc in pcs) != dur:
            raise BuildError(f"segmento {n}: la traccia audio ha un buco nel range [{s['f0']}..{s['f1']}).")
        sub_off = offset
        for pi, pc in enumerate(pcs):
            pc_dur = pc["end"] - pc["start"]
            if not append(pc, 2, 1, sub_off):
                raise BuildError(f"append fallito su A1 (segmento {n}, pezzo {pi + 1})")
            expected["a"].append((sub_off, pc_dur))
            sub_off += pc_dur
            appended += 1

        offset += dur
        if n % 5 == 0 or n == total:
            log(f"pezzo {n}/{total} appeso", 0.05 + 0.85 * n / total)

    if name_tracks:
        for vi, ti in enumerate(video_tracks, start=1):
            try:
                tl.SetTrackName("video", vi, f"CAM {ti}")
            except Exception:
                pass
        try:
            tl.SetTrackName("audio", 1, f"AUDIO {audio_track}")
        except Exception:
            pass

    if mark_cuts:
        at = 0
        for s in segs[:-1]:
            at += s["f1"] - s["f0"]
            try:
                tl.AddMarker(at, "Cyan", f"taglio {at}", "", 1, "cutlab")
            except Exception:
                pass

    log("verifica dei frame…", 0.92)
    report = _verify(tl, expected, video_tracks)
    ok = report["ok"]
    log("fatto" if ok else "verifica fallita", 1.0)
    total_frames = offset - base_frame
    return {
        "timeline": new_name,
        "frames": total_frames,
        "seconds": round(total_frames / fps, 2),
        "pieces": len(segs),
        "clips": appended,
        "video_tracks": video_tracks,
        "audio_track": 1,
        "from_audio_track": audio_track,
        "verification": report,
        "ok": ok,
    }


def _verify(tl, expected: dict, video_tracks: list[int]) -> dict:
    """Rilegge la timeline appena scritta e confronta posizioni e durate."""
    problems = []
    counts = {}
    for vi, ti in enumerate(video_tracks, start=1):
        got = [(int(x.GetStart()), int(x.GetDuration()))
               for x in (tl.GetItemListInTrack("video", vi) or [])]
        counts[f"V{vi}"] = len(got)
        if got != expected[ti]:
            problems.append(f"V{vi} diversa: {_diff(expected[ti], got)}")
    got_a = [(int(x.GetStart()), int(x.GetDuration()))
             for x in (tl.GetItemListInTrack("audio", 1) or [])]
    counts["A1"] = len(got_a)
    if got_a != expected["a"]:
        problems.append(f"A1 diversa: {_diff(expected['a'], got_a)}")

    # allineamento: copertura totale (start primo clip, end ultimo clip) coerente su tutte le tracce
    spans = {}
    for v in range(1, len(video_tracks) + 1):
        xs = tl.GetItemListInTrack("video", v) or []
        spans[f"V{v}"] = (int(xs[0].GetStart()), int(xs[-1].GetStart()) + int(xs[-1].GetDuration())) if xs else None
    xs_a = tl.GetItemListInTrack("audio", 1) or []
    spans["A1"] = (int(xs_a[0].GetStart()), int(xs_a[-1].GetStart()) + int(xs_a[-1].GetDuration())) if xs_a else None
    vals = list(spans.values())
    if vals and any(s != vals[0] for s in vals):
        problems.append("inizio/fine della timeline non coincidono tra le tracce")
    return {"ok": not problems, "problems": problems, "counts": counts,
            "pieces": len(expected.get("a", []))}


def _diff(want: list, got: list) -> str:
    for i, (w, g) in enumerate(zip(want, got)):
        if w != g:
            return f"pezzo {i + 1}: atteso {w}, trovato {g}"
    if len(want) != len(got):
        return f"attesi {len(want)} clip, trovati {len(got)}"
    return "differenza non localizzata"
