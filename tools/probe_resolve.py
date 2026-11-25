"""Sonda: scopre sul campo come si comporta l'API Resolve 21.1 su AppendToTimeline.

Domande a cui risponde, con misure e non con ipotesi:
  Q1  `endFrame` e' inclusivo o esclusivo?  (appendo 0..48: 48 o 49 frame?)
  Q2  `trackIndex` viene rispettato su una timeline vuota?
  Q3  `mediaType: 1` evita che l'audio della clip video finisca su A1?
  Q4  `mediaType: 2` mette davvero l'audio su una traccia audio?
  Q5  aggiungere una traccia video/audio: AddTrack funziona su timeline vuota?
  Q6  se una clip video porta con se' l'audio, su quale traccia lo mette?

Non distrugge niente: crea un bin "CutLab TEST" nel progetto corrente, lavora
li' dentro e alla fine rimette la timeline e il bin come stavano.
    python tools/probe_resolve.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import resolve_api as R  # noqa: E402

TEST = os.path.join(ROOT, "out", "test")
BIN = "CutLab TEST"
OUT = os.path.join(ROOT, "out", "probe_resolve.json")


def dump_tracks(t, label=""):
    rows = []
    for kind in ("video", "audio"):
        n = t.GetTrackCount(kind)
        for i in range(1, n + 1):
            for it in t.GetItemListInTrack(kind, i) or []:
                mp = it.GetMediaPoolItem()
                rows.append({
                    "track": f"{kind[0].upper()}{i}",
                    "name": it.GetName(),
                    "tl": [int(it.GetStart()), int(it.GetStart()) + int(it.GetDuration())],
                    "dur": int(it.GetDuration()),
                    "src": [int(it.GetSourceStartFrame()), int(it.GetSourceEndFrame())],
                    "file": os.path.basename(mp.GetClipProperty("File Path") or "") if mp else None,
                })
    print(f"  --- {label} ---")
    for r in rows:
        print(f"   {r['track']:3} {r['name'][:26]:26} tl={r['tl']} dur={r['dur']:4}"
              f" src={r['src']} {r['file']}")
    return rows


def main() -> int:
    report: dict = {}
    p = R.project()
    pm_ = R.pm()
    pool = R.media_pool()
    orig_tl = R.timeline().GetName()
    print(f"progetto {p.GetName()} / timeline {orig_tl}")
    print("resolve", R._app().GetVersionString(), "fps", R.fps())

    root = pool.GetRootFolder()
    made_bin = None
    made_tls = []
    try:
        made_bin = pool.AddSubFolder(root, BIN)
        pool.SetCurrentFolder(made_bin)
        files = [os.path.join(TEST, f) for f in ("cam_a.mp4", "cam_b.mp4", "lav.wav")]
        items = pool.ImportMedia(files) or []
        print(f"importati {len(items)} file nel bin {BIN!r}")
        by_name = {os.path.basename(i.GetClipProperty("File Path")): i for i in items}

        # ---------- Q1/Q2/Q3: video su V1, poi su V2
        tl = pool.CreateEmptyTimeline("CutLab PROBE 1")
        made_tls.append("CutLab PROBE 1")
        assert tl, "CreateEmptyTimeline ha fallito"
        p.SetCurrentTimeline(tl)
        print(f"\ntimeline nuova: video={tl.GetTrackCount('video')} audio={tl.GetTrackCount('audio')}")

        cam_a = by_name["cam_a.mp4"]
        ok = pool.AppendToTimeline([{"mediaPoolItem": cam_a, "startFrame": 0, "endFrame": 48,
                                     "mediaType": 1, "trackIndex": 1}])
        print(f"Q1 append startFrame=0 endFrame=48  -> {ok}")
        rows = dump_tracks(tl, "dopo 1 append video su V1")
        report["q1_dur_0_48"] = rows[0]["dur"] if rows else None

        print(f"Q5a AddTrack video -> {tl.AddTrack('video')}  (ora {tl.GetTrackCount('video')} tracce)")
        ok2 = pool.AppendToTimeline([{"mediaPoolItem": by_name["cam_b.mp4"], "startFrame": 100,
                                      "endFrame": 148, "mediaType": 1, "trackIndex": 2}])
        print(f"Q2 append su trackIndex=2 -> {ok2}")
        rows = dump_tracks(tl, "dopo append su V2")
        report["q2_v2_ok"] = any(r["track"] == "V2" for r in rows)
        report["q3_video_audio_pollution"] = [r for r in rows if r["track"].startswith("A")]

        # ---------- Q4: audio su A1
        print(f"\nQ5b AddTrack audio -> {pool and tl.AddTrack('audio')} (ora {tl.GetTrackCount('audio')} audio)")
        ok3 = pool.AppendToTimeline([{"mediaPoolItem": by_name["lav.wav"], "startFrame": 0,
                                      "endFrame": 96, "mediaType": 2, "trackIndex": 1}])
        print(f"Q4 append audio su A1 -> {ok3}")
        rows = dump_tracks(tl, "dopo append lav su A1")
        report["q4_audio_rows"] = [r for r in rows if r["track"].startswith("A")]

        # ---------- allineamento: 2 video + 1 audio per segmento, 3 segmenti
        tl2 = pool.CreateEmptyTimeline("CutLab PROBE 2")
        made_tls.append("CutLab PROBE 2")
        p.SetCurrentTimeline(tl2)
        tl2.AddTrack("video")
        segs = [(0, 48), (480, 528), (960, 1080)]
        for a, b in segs:
            for idx, name in ((1, "cam_a.mp4"), (2, "cam_b.mp4")):
                pool.AppendToTimeline([{"mediaPoolItem": by_name[name], "startFrame": a,
                                        "endFrame": b, "mediaType": 1, "trackIndex": idx}])
            pool.AppendToTimeline([{"mediaPoolItem": by_name["lav.wav"], "startFrame": a,
                                    "endFrame": b, "mediaType": 2, "trackIndex": 1}])
        rows = dump_tracks(tl2, "PROBE 2: 3 segmenti su V1+V2+A1")
        report["q6_alignment"] = rows

        # ---------- Q7: DeleteTrack su una traccia audio con roba dentro
        n_audio = tl2.GetTrackCount("audio")
        deleted = None
        if n_audio > 1:
            deleted = tl2.DeleteTrack("audio", 1)
        print(f"\nQ7 DeleteTrack audio 1 -> {deleted} (audio ora {tl2.GetTrackCount('audio')})")
        dump_tracks(tl2, "PROBE 2 dopo delete A1")

        # ---------- Q8: SetTrackName
        try:
            print("Q8 SetTrackName ->", tl2.SetTrackName("video", 1, "CAM A"))
        except Exception as e:
            print("Q8 SetTrackName eccezione:", e)

    finally:
        # pulizia: rimuovo le timeline di prova e il bin
        try:
            for name in made_tls:
                p.SetCurrentTimeline(tl)  # noqa: F821
                p.DeleteTimeline(name)
        except Exception:
            pass
        try:
            pm_ = R.pm()
            if made_bin is not None:
                pm_.GetCurrentProject().GetMediaPool().DeleteFolders([made_bin])
        except Exception as e:
            print("pulizia bin:", e)
        # rimetto la timeline originale
        for t_ in (p.GetTimelineByIndex(i) for i in range(1, int(p.GetTimelineCount()) + 1)):
            try:
                if t_ and t_.GetName() == orig_tl:
                    p.SetCurrentTimeline(t_)
                    break
            except Exception:
                continue
        print(f"\ntimeline corrente ripristinata: {p.GetCurrentTimeline().GetName()}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print("report:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
