"""Quarta sonda: come si scrive il testo e come si collega una spline.

    python tools/probe_fusion4.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import resolve_api as R  # noqa: E402


def main() -> int:
    t = R.timeline()
    item = (t.GetItemListInTrack("video", 1) or [])[0]
    comp = item.GetFusionCompByIndex(1) or item.AddFusionComp()
    txt = comp.AddTool("TextPlus")
    print("tool:", txt.GetAttrs().get("TOOLS_Name"))

    lst = txt.GetInputList() or {}
    print("\n-- input che contengono Text/Styled --")
    for k, v in lst.items():
        try:
            a = v.GetAttrs()
            n = a.get("INPS_Name") or ""
            if "text" in n.lower() or "styled" in n.lower():
                print(f"   id={a.get('INPS_ID')!r} name={n!r} type={a.get('INPS_DataType')!r}")
        except Exception:
            continue

    print("\n-- scrittura del testo --")
    for cand in ("StyledText", "Text"):
        try:
            r = txt.SetInput(cand, "MOTION LAB", 0)
            got = txt.GetInput(cand)
            print(f"   SetInput({cand!r}) -> {r!r} / riletto {got!r}")
        except Exception as e:
            print(f"   SetInput({cand!r}) eccezione: {e}")

    print("\n-- collegamento spline: quattro modi --")
    spline = comp.BezierSpline()
    spline.SetKeyFrames({0: 0.02, 20: 0.10, 40: 0.10})
    print("   keyframes:", spline.GetKeyFrames())

    ok = False
    # 1) SetInput con nome
    try:
        txt.SetInput("Size", spline, 0)
        v = txt.GetInput("Size")
        print("   1) SetInput('Size', spline) ->", type(v).__name__, v)
        ok = ok or type(v).__name__ == "PyRemoteObject"
    except Exception as e:
        print("   1) eccezione:", e)

    # 2) SetInput con id numerico dell'input
    try:
        num_id = None
        for k, v in lst.items():
            a = v.GetAttrs()
            if (a.get("INPS_Name") or "") == "Size":
                num_id = k
                break
        if num_id is not None:
            txt.SetInput(num_id, spline, 0)
            v = txt.GetInput("Size")
            print(f"   2) SetInput(id={num_id!r}, spline) ->", type(v).__name__, v)
            ok = ok or type(v).__name__ == "PyRemoteObject"
    except Exception as e:
        print("   2) eccezione:", e)

    # 3) assegnazione ad attributo
    try:
        txt.Size = spline
        v = txt.GetInput("Size")
        print("   3) txt.Size = spline ->", type(v).__name__, v)
        ok = ok or type(v).__name__ == "PyRemoteObject"
    except Exception as e:
        print("   3) eccezione:", e)

    # 4) Input.Set / Input.SetExpression
    try:
        inp = None
        for k, v in lst.items():
            a = v.GetAttrs()
            if (a.get("INPS_Name") or "") == "Size":
                inp = v
                break
        if inp is not None:
            for meth in ("Set", "SetExpression", "SetKeyFrames"):
                print(f"   4) Input ha {meth}:", hasattr(inp, meth))
            inp.Set(spline, 0)
            v = txt.GetInput("Size")
            print("   4) inp.Set(spline) ->", type(v).__name__, v)
            ok = ok or type(v).__name__ == "PyRemoteObject"
    except Exception as e:
        print("   4) eccezione:", e)

    print("\nesito collegamento:", "OK" if ok else "NESSUN METODO HA FUNZIONATO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
