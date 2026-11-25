"""Quale operazione uccide gli handle del media pool?

Quattro varianti, ognuna su una timeline nuova, con append identico (48 frame).
Serve a capire *esattamente* dopo quale chiamata AppendToTimeline inizia a
restituire None, invece di rincorrere il sintomo.

    python tools/diag_handles.py
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import resolve_api as R  # noqa: E402
from test_e2e_resolve import ensure_media  # noqa: E402


def fresh_item(pool, name: str):
    """Handle sempre nuovo: refresh, root rifetchata, cartella rifetchata."""
    pool.RefreshFolders()
    root = pool.GetRootFolder()
    for folder in root.GetSubFolderList() or []:
        clips = folder.GetClipList() or []
        for c in clips:
            if os.path.basename(c.GetClipProperty("File Path") or "") == name:
                pool.SetCurrentFolder(folder)
                return c
    return None


def trial(label: str, use_addtrack: bool, refresh_after: bool,
          refresh_before: bool) -> str:
    pool = R.media_pool()
    p = R.project()
    name = f"CutLab DIAG {label}"
    R.delete_timelines([name])
    time.sleep(0.2)
    tl = pool.CreateEmptyTimeline(name)
    if not tl:
        return f"{label:24} CreateEmptyTimeline -> None"
    if not R.set_current_timeline(tl):
        return f"{label:24} set_current fallito"
    if refresh_before:
        pool.RefreshFolders()
    if use_addtrack:
        tl.AddTrack("video")
    if refresh_after:
        pool.RefreshFolders()
    item = fresh_item(pool, "cam_a.mp4")
    if item is None:
        return f"{label:24} clip non trovato"
    got = pool.AppendToTimeline([{"mediaPoolItem": item, "startFrame": 0,
                                  "endFrame": 48, "mediaType": 1, "trackIndex": 1}])
    n = len(tl.GetItemListInTrack("video", 1) or [])
    R.delete_timelines([name])
    return f"{label:24} append={'ok' if got else 'NONE'}  clip_in_V1={n}"


def main() -> int:
    ensure_media()
    print("progetto:", R.project().GetName(), "current:", R.timeline().GetName())
    rows = [
        trial("A base", False, False, False),
        trial("B +AddTrack", True, False, False),
        trial("C +AddTrack +refresh", True, True, False),
        trial("D refresh prima", False, True, True),
        trial("E AddTrack prima del refresh", False, True, False),
    ]
    for r in rows:
        print(" ", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
