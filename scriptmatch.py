"""Script → parlato: quali battute della ripresa sono nello script scritto.

Lo script (DOCX) e' la mappa di cio' che va tenuto: tutto il resto - prove,
ripetizioni, indicazioni del cameraman, silenzi - finisce nel taglio. Il testo
scritto non coincide mai parola per parola con l'ASR, quindi l'allineamento e'
fatto sui token con `difflib.SequenceMatcher`: i blocchi uguali identificano le
parti di ASR che corrispondono allo script.

`keep` sono indici di battuta (utts) e si passano a cutplan.build usando
labels = [0, 1, 2, ...]: cosi' "tieni i cluster" diventa "tieni queste battute".
"""
from __future__ import annotations

import difflib
import html
import re
import unicodedata
import zipfile

PUNCT = re.compile(r"[^0-9a-zàèéìòùç']+")


def read_docx_text(path: str) -> str:
    """Testo di un .docx senza dipendenze esterne (word/document.xml)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"<w:p[ >]", "\n<w:p ", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S)
    # i <w:t> sono nel ordine del documento; le interruzioni di paragrafo sono
    # già nel testo grezzo, quindi si unisce e si normalizza in un secondo momento
    raw = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
    text = raw if len(raw) > sum(len(p) for p in parts) else "".join(parts)
    text = html.unescape(text)
    return unicodedata.normalize("NFC", text)


def tokens(text: str) -> list[str]:
    """Token confrontabili: minuscole, senza punteggiatura, accenti a posto."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in PUNCT.split(t) if len(w) > 1]


def match_script(script_text: str, utts: list[dict], min_ratio: float = 0.55,
                 max_join: int = 3) -> dict:
    """Indici delle battute ASR che recitano lo script.

    L'ordine non conta: una ripresa a blocchi si registra fuori sequenza, quindi
    ogni riga dello script va cercata dovunque. Si provano anche finestre di piu'
    battute perche' una riga scritta puo' essere recitata in due riprese.

    Ritorna {"keep": [...], "coverage": f, "script_lines": n, "matched_lines": n,
    "script_tokens": n, "matched_tokens": n, "lines": [{ratio, utt}],
    "missing": [righe non trovate]}. coverage < 0.35 vuol dire che lo script non
    e' di questo girato: in quel caso keep va ignorato.
    """
    lines = [ln.strip() for ln in re.split(r"\n+", script_text or "")
             if len(tokens(ln)) >= 4]
    if not lines or not utts:
        return {"keep": [], "coverage": 0.0, "script_lines": len(lines),
                "matched_lines": 0, "script_tokens": 0, "matched_tokens": 0,
                "lines": [], "missing": []}
    tok_cache = [tokens(u.get("text", "")) for u in utts]
    keep: set[int] = set()
    hits: list[dict] = []
    missing: list[str] = []
    matched_tokens = 0
    total_tokens = 0
    for ln in lines:
        lt = tokens(ln)
        total_tokens += len(lt)
        best = None
        for w0 in range(len(utts)):
            for span in range(1, max_join + 1):
                if w0 + span > len(utts):
                    break
                at = [t for k in range(w0, w0 + span) for t in tok_cache[k]]
                if not at:
                    continue
                blocks = difflib.SequenceMatcher(None, lt, at, autojunk=False) \
                    .get_matching_blocks()
                size = sum(b.size for b in blocks)
                ratio = size / len(lt)
                # Preferisci: (1) ratio migliore (tolleranza 0.02 per take equivalenti),
                # (2) span piu' compatto (meno parole spurie), (3) l'ultimo take (w0 piu' alto)
                precision = size / max(1, len(at))
                cand = (ratio, precision, -span, w0, span, size)
                if best is None:
                    best = cand
                elif ratio > best[0] + 0.02:
                    best = cand
                elif abs(ratio - best[0]) <= 0.02 and (precision, -span, w0) > (best[1], best[2], best[3]):
                    best = cand
        if best and best[0] >= min_ratio:
            matched_tokens += best[5]
            for k in range(best[3], best[3] + best[4]):
                keep.add(k)
            hits.append({"ratio": round(best[0], 3), "utt": best[3],
                         "line": ln[:90]})
        else:
            missing.append(ln[:120])
    return {"keep": sorted(keep),
            "coverage": round(len(hits) / len(lines), 4),
            "script_lines": len(lines), "matched_lines": len(hits),
            "script_tokens": total_tokens, "matched_tokens": matched_tokens,
            "token_coverage": round(matched_tokens / max(1, total_tokens), 4),
            "lines": hits, "missing": missing[:15]}
