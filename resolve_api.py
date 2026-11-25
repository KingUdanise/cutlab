"""Livello Resolve di CutLab.

Cosa serve a CutLab e perche' non basta l'API "seleziona una clip":

* il materiale e' multi-camera: 2 tracce video (stessa ripresa, 2 angolazioni)
  + 1 traccia audio separata (lav dell'interlocutore, es. WAV del DJI Mic);
* il taglio deve avvenire **agli stessi frame** su tutte le tracce, altrimenti
  il multicam si sfasa e la traccia audio va fuori sync.

Quindi qui non si lavora sulla "clip selezionata": si legge la timeline intera
(tutte le tracce), si costruisce una mappa `frame timeline -> frame sorgente`
per ogni traccia, e si ricostruisce una timeline nuova appendendo **sub-range
sorgente** (startFrame/endFrame + trackIndex), che e' l'unico modo per avere
tagli al frame esatto senza passare da razor multipli in GUI.

Regole imparate su Resolve 21.1 (non cambiarle):
  * AppendToTimeline opera sulla timeline CORRENTE -> SetCurrentTimeline prima;
  * `GetDuration()` e' esclusivo sull'end: end = start + duration;
  * `GetSourceEndFrame()` a volte e' sballato di 1: si usa src_in + duration;
  * i proxy remoti perdono i metodi ("NoneType" not callable) -> _retry;
  * generatori/transizioni/titoli non hanno MediaPoolItem: vanno saltati e
    segnalati (non si possono riappendere come sub-range).
"""
from __future__ import annotations

import os
import sys
import time

RESOLVE_LIB = r"C:\Program Files\Blackmagic Design\DaVinci Resolve"
MODULES = os.path.join(RESOLVE_LIB, "ResolvePython", "lib", "modules")

os.environ.setdefault("PYTHONPATH", MODULES)
os.environ.setdefault("RESOLVE_SCRIPT_LIB", os.path.join(RESOLVE_LIB, "fusionscript.dll"))
if MODULES not in sys.path:
    sys.path.insert(0, MODULES)

import DaVinciResolveScript as dvr  # noqa: E402


class ResolveError(RuntimeError):
    pass


# --------------------------------------------------------------------------- connessione

_APP = None
_PM = None


def _app():
    """Handle dell'applicazione, **cachato**.

    `dvr.scriptapp("Resolve")` non e' una lettura: apre una connessione. Resolve
    ne tiene un numero limitato e, superato quello, le chiamate successive
    tornano None invece di sollevare — il sintomo e' "AppendToTimeline che
    fallisce a caso dopo un po'". Quindi: una connessione, riusata.
    """
    global _APP
    if _APP is None:
        _APP = dvr.scriptapp("Resolve")
    if _APP is None:
        raise ResolveError("Resolve non raggiungibile: avvialo e riprova.")
    return _APP


def _retry(fn, attempts: int = 4, delay: float = 0.35):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except TypeError as e:
            last = e
            if "NoneType" not in str(e) or i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))
    raise last  # pragma: no cover


def pm():
    global _PM
    if _PM is None:
        _PM = _retry(lambda: _app().GetProjectManager())
    if _PM is None:
        raise ResolveError("Resolve non raggiungibile: avvialo e riprova.")
    return _PM


def project():
    p = _retry(lambda: pm().GetCurrentProject())
    if p is None:
        raise ResolveError("Nessun progetto aperto in Resolve.")
    return p


def timeline():
    t = _retry(lambda: pm().GetCurrentProject().GetCurrentTimeline())
    if t is None:
        raise ResolveError("Nessuna timeline aperta.")
    return t


def media_pool():
    return project().GetMediaPool()


def fps(t=None) -> float:
    t = t or timeline()
    try:
        return float(t.GetSetting("timelineFrameRate"))
    except Exception:
        try:
            return float(project().GetSetting("timelineFrameRate"))
        except Exception:
            return 24.0


# --------------------------------------------------------------------------- inventario

def _item_dict(it, track_kind: str, track_index: int) -> dict:
    """Un clip della timeline, normalizzato. None-safe."""
    start = int(it.GetStart())
    dur = int(it.GetDuration())
    mp_item = None
    try:
        mp_item = it.GetMediaPoolItem()
    except Exception:
        mp_item = None
    d = {
        "name": it.GetName(),
        "track_kind": track_kind,
        "track": track_index,
        "tl_start": start,
        "tl_end": start + dur,          # esclusivo
        "dur": dur,
        "src_in": None,
        "src_out": None,
        "media_id": None,
        "path": None,
        "media_fps": None,
        "is_generator": mp_item is None,
    }
    if mp_item is not None:
        try:
            d["src_in"] = int(it.GetSourceStartFrame())
        except Exception:
            d["src_in"] = None
        if d["src_in"] is None or d["src_in"] < 0:
            d["src_in"] = 0
        # niente GetSourceEndFrame(): si usa la durata, che e' affidabile
        d["src_out"] = d["src_in"] + dur
        try:
            d["media_id"] = mp_item.GetUniqueId()
        except Exception:
            d["media_id"] = None
        try:
            d["path"] = mp_item.GetClipProperty("File Path")
        except Exception:
            d["path"] = None
        try:
            f = mp_item.GetClipProperty("FPS")
            d["media_fps"] = float(f) if f else None
        except Exception:
            d["media_fps"] = None
    return d


def _track_items(t, kind: str, index: int) -> list[dict]:
    out = []
    for it in t.GetItemListInTrack(kind, index) or []:
        try:
            out.append(_item_dict(it, kind, index))
        except Exception:
            continue
    out.sort(key=lambda d: d["tl_start"])
    return out


def marks(t=None) -> tuple[int | None, int | None]:
    t = t or timeline()
    try:
        mk = t.GetMarkInOut() or {}
    except Exception:
        return None, None
    vid = mk.get("video") or {}
    aud = mk.get("audio") or {}
    mi = vid.get("in", aud.get("in"))
    mo = vid.get("out", aud.get("out"))
    return (int(mi) if mi is not None else None,
            int(mo) if mo is not None else None)


def inventory(use_marks: bool = True) -> dict:
    """Fotografia della timeline: tutte le tracce, tutti i clip, il range utile."""
    p = project()
    t = timeline()
    f = fps(t)

    # Dopo un SetCurrentTimeline / una creazione, Resolve puo' restituire una
    # timeline "vuota" per qualche istante (GetEndFrame a 0, clip di 1 frame).
    # Si aspetta che si assesti invece di leggere numeri falsi.
    tl_start, tl_end, video, audio = 0, 0, [], []
    deadline = time.time() + 4.0
    while True:
        tl_start = int(t.GetStartFrame())
        tl_end = int(t.GetEndFrame())
        video, audio = [], []
        for kind, sink in (("video", video), ("audio", audio)):
            try:
                n = int(t.GetTrackCount(kind))
            except Exception:
                n = 0
            for i in range(1, n + 1):
                items = _track_items(t, kind, i)
                sink.append({
                    "index": i,
                    "kind": kind,
                    "items": items,
                    "clip_count": len(items),
                    "generators": sum(1 for d in items if d["is_generator"]),
                    "first": items[0]["tl_start"] if items else None,
                    "last": items[-1]["tl_end"] if items else None,
                    "names": sorted({os.path.basename(d["path"] or d["name"]) for d in items}),
                })
        real = [d[k] for tr in (video + audio) for d in tr["items"]
                for k in ("tl_start", "tl_end") if not d["is_generator"]]
        span = (max(real) - min(real)) if real else 0
        if not real or span > 2 or time.time() > deadline:
            break
        time.sleep(0.25)

    # GetEndFrame() su una timeline appena creata restituisce 0 (Resolve non ha
    # ancora "aperto" la timeline nel viewer): il range vero e' quello dei clip.
    real_edges = [d[k] for tr in (video + audio) for d in tr["items"]
                  for k in ("tl_start", "tl_end") if not d["is_generator"]]
    if real_edges:
        if tl_end <= tl_start:
            tl_start, tl_end = min(real_edges), max(real_edges)
        else:
            tl_end = max(tl_end, max(real_edges))

    start, end = tl_start, tl_end
    mi, mo = marks(t)
    if use_marks and (mi is not None or mo is not None):
        start = tl_start if mi is None else max(tl_start, mi)
        end = tl_end if mo is None else min(tl_end, mo)

    return {
        "resolve": _app().GetVersionString(),
        "project": p.GetName(),
        "timeline": t.GetName(),
        "fps": f,
        "width": int(t.GetSetting("timelineResolutionWidth") or 0),
        "height": int(t.GetSetting("timelineResolutionHeight") or 0),
        "start": start,
        "end": end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
        "marks": [mi, mo],
        "used_marks": bool(use_marks and (mi is not None or mo is not None)),
        "video_tracks": video,
        "audio_tracks": audio,
    }


def coverage(track: dict, start: int, end: int) -> float:
    """Percentuale del range [start,end) coperta da clip veri della traccia."""
    if end <= start:
        return 0.0
    tot = 0
    for d in track["items"]:
        if d["is_generator"]:
            continue
        a, b = max(start, d["tl_start"]), min(end, d["tl_end"])
        if b > a:
            tot += b - a
    return tot / (end - start)


# --------------------------------------------------------------------------- media pool

def media_index(with_folders: bool = False) -> dict:
    """unique_id -> MediaPoolItem (o (item, folder) con with_folders).

    Da fare ogni volta prima di appendere, e sempre partendo da una **root
    rifetchata dopo RefreshFolders**: i proxy di cartella presi prima del refresh
    restituiscono handle morti, e un handle morto fa fallire AppendToTimeline
    restituendo None (senza eccezione: sembra che non sia successo niente).
    """
    pool = media_pool()
    try:
        pool.RefreshFolders()
    except Exception:
        pass
    root = pool.GetRootFolder()
    found: dict = {}

    def walk(folder, depth=0):
        if folder is None or depth > 12:
            return
        for clip in folder.GetClipList() or []:
            try:
                uid = clip.GetUniqueId()
            except Exception:
                continue
            if not uid:
                continue
            found[uid] = (clip, folder) if with_folders else clip
        for sub in folder.GetSubFolderList() or []:
            walk(sub, depth + 1)

    walk(root)
    return found


def set_current_timeline(tl, timeout: float = 8.0) -> str | None:
    """Rende `tl` la timeline corrente e **aspetta che Resolve sia d'accordo**.

    `SetCurrentTimeline` risponde False anche quando funziona, ed e' asincrona:
    appendere subito dopo puo' finire sulla timeline sbagliata (o sparire).
    Qui si rilegge il nome finche' non combacia, e si restituisce il nome vero.
    """
    name = None
    try:
        name = tl.GetName()
    except Exception:
        return None
    try:
        project().SetCurrentTimeline(tl)
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            cur = project().GetCurrentTimeline()
            if cur is not None and cur.GetName() == name:
                return name
        except Exception:
            pass
        time.sleep(0.15)
    return None


_IDX: dict = {}
_FOLDER_CACHE: dict = {}          # path normalizzato -> Folder del media pool


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _clip_in_folder(folder, path: str):
    """Rilegge la clip *ora*: un handle riusato dopo un po' e' morto."""
    key = _norm(path)
    for c in folder.GetClipList() or []:
        try:
            got = c.GetClipProperty("File Path") or ""
        except Exception:
            continue
        if got and _norm(got) == key:
            return c
    return None


def _folders_with(path: str) -> list:
    """Tutte le cartelle che contengono una clip con quel path (con cache)."""
    key = _norm(path)
    cached = _FOLDER_CACHE.get(key)
    if cached:
        return cached
    pool = media_pool()
    try:
        pool.RefreshFolders()
    except Exception:
        pass
    root = pool.GetRootFolder()
    out = []

    def walk(f, depth=0):
        if f is None or depth > 12:
            return
        if _clip_in_folder(f, path) is not None:
            out.append(f)
        for sub in f.GetSubFolderList() or []:
            walk(sub, depth + 1)

    walk(root)
    if out:
        _FOLDER_CACHE[key] = out
    return out


def _locate_folder(path: str):
    folders = _folders_with(path)
    return folders[0] if folders else None


def _candidates(path: str, uid: str | None) -> list:
    """Ogni entry del pool che punta a quel file, in ordine di tentativo.

    Ce ne puo' essere piu' di una (import ripetuti, copie in cartelle diverse):
    alcune sono entry morte che fanno fallire l'append *senza errore*, quindi
    si prova la successiva invece di arrendersi.
    """
    out = []
    for folder in _folders_with(path):
        for c in folder.GetClipList() or []:
            try:
                got = c.GetClipProperty("File Path") or ""
            except Exception:
                continue
            if got and _norm(got) == _norm(path):
                out.append((c, folder))
    if not out and uid:
        global _IDX
        if not _IDX:
            _IDX = media_index(with_folders=True)
        entry = _IDX.get(uid)
        if entry:
            out.append(entry)
    return out


_ITEM_CACHE: dict = {}             # path -> MediaPoolItem vivo


def _safe_name(folder) -> str:
    try:
        return folder.GetName() if folder is not None else "-"
    except Exception:
        return "?"


def _fresh_item(path: str):
    """Handle garantito vivo per un file, passando da ImportMedia.

    E' l'unica strada che regge: `ImportMedia` su un file gia' nel pool **non
    crea duplicati** e restituisce l'item esistente con un handle appena
    costruito. Gli handle presi da `GetClipList()` possono essere gia' morti e
    fanno fallire l'append restituendo None. E si evita di cambiare cartella
    corrente: spostarsi nel pool durante una build e' cio' che rompeva tutto.
    """
    pool = media_pool()
    for attempt in range(3):
        try:
            got = pool.ImportMedia([path])
        except Exception:
            got = None
        if got:
            return got[0]
        time.sleep(0.2 + 0.2 * attempt)
    return None


def append_range(uid: str | None, start: int, end: int, mtype: int, track: int,
                 retries: int = 4, path: str | None = None,
                 record_frame: int | None = None) -> bool:
    """Appende un sotto-range sorgente alla timeline corrente.

    Ordine dei tentativi, dal piu' economico al piu' affidabile:
      1. handle in cache (dopo il primo append riuscito);
      2. `ImportMedia` sul path, che restituisce sempre un handle vivo;
      3. (ultima spiaggia) ogni entry del pool che punta al file.

    `AppendToTimeline` fallisce restituendo None, non sollevando: senza questa
    scala di tentativi una build intera si ferma al primo handle scaduto.
    """
    if not path and not uid:
        return False
    pool = media_pool()

    def try_append(item) -> bool:
        info = {
            "mediaPoolItem": item, "startFrame": int(start), "endFrame": int(end),
            "mediaType": int(mtype), "trackIndex": int(track),
        }
        # recordFrame esplicito: senza, Resolve appende "in coda" e sugli audio
        # l'arrotondamento a campione sposta i clip di 1 frame (deriva cumulativa).
        if record_frame is not None:
            info["recordFrame"] = int(record_frame)
        got = pool.AppendToTimeline([info])
        if os.environ.get("CUTLAB_DEBUG"):
            try:
                cur = project().GetCurrentTimeline()
                cur_name = cur.GetName() if cur else None
            except Exception:
                cur_name = "?"
            print(f"   [append] {os.path.basename(path or '?')} {start}..{end} "
                  f"mt={mtype} tr={track} tl={cur_name!r} -> {bool(got)}")
        return bool(got)

    key = _norm(path or "")
    for attempt in range(retries):
        if attempt:
            _ITEM_CACHE.pop(key, None)
        cached = _ITEM_CACHE.get(key) if path else None
        if cached is not None and try_append(cached):
            return True
        if path:
            item = _fresh_item(path)
            if item is not None and try_append(item):
                _ITEM_CACHE[key] = item
                return True
        if attempt >= retries - 2:
            for item, _folder in _candidates(path or "", uid):
                if try_append(item):
                    return True
        time.sleep(0.2 + 0.25 * attempt)
    return False


def reset_media_index() -> None:
    global _IDX
    _IDX = {}


def delete_timelines(names: list[str]) -> bool:
    """Elimina timeline per nome.

    Su 21.1 `Project.DeleteTimeline` non esiste e `MediaPool.DeleteTimelines`
    risponde False anche con gli oggetti giusti. La strada che funziona: le
    timeline *sono* clip del media pool, nella cartella in cui sono state create,
    quindi si cercano per nome in tutte le cartelle e si usa `DeleteClips`.
    """
    if not names:
        return True
    pool = media_pool()
    want = set(names)
    ok_all = True
    for folder in _all_folders(pool.GetRootFolder()):
        hits = [c for c in (folder.GetClipList() or []) if c.GetName() in want]
        if not hits:
            continue
        pool.SetCurrentFolder(folder)
        try:
            ok_all = bool(pool.DeleteClips(hits)) and ok_all
        except Exception:
            ok_all = False
    return ok_all


def _all_folders(root, depth=0):
    if root is None or depth > 12:
        return
    yield root
    for sub in root.GetSubFolderList() or []:
        yield from _all_folders(sub, depth + 1)


def timeline_names() -> list[str]:
    p = project()
    out = []
    for i in range(1, int(p.GetTimelineCount()) + 1):
        t = p.GetTimelineByIndex(i)
        try:
            out.append(t.GetName())
        except Exception:
            continue
    return out


def duplicate_timeline(name: str | None = None):
    """Copia della timeline corrente (per lavorare in sicurezza)."""
    t = timeline()
    new_name = name or f"{t.GetName()} · CUT"
    dup = t.DuplicateTimeline(new_name)
    return dup


def backup_project(tag: str = "cutlab") -> str | None:
    """Esporta un .drp di sicurezza prima di scrivere."""
    try:
        p = project()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "out", "backup", f"{p.GetName()}_{tag}_{stamp}.drp")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path if pm().ExportProject(p.GetName(), path, True) else None
    except Exception:
        return None
