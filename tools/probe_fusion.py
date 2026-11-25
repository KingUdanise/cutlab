"""Sonda dell'API Fusion dentro Resolve 21.1.

Serve a scoprire, con prove e non con ricordi:
  F1  `resolve.Fusion()` esiste e cosa restituisce?
  F2  `TimelineItem.AddFusionComp()` / `GetFusionCompByIndex(1)` funzionano?
  F3  `comp.AddTool("TextPlus")` crea un nodo vero? e `SetInput` scrive?
  F4  come si crea una **animazione**: `comp.BezierSpline()` /
      `fusion.BezierSpline()` esistono? e `tool.SetInput(nome, spline)` accetta
      una spline come valore?
  F5  `tool.SetExpression(...)` esiste? (strada alternativa all'animazione)
  F6  `comp.GetToolList()` per leggere i nodi creati.

    python tools/probe_fusion.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import resolve_api as R  # noqa: E402


def main() -> int:
    r = R._app()
    print("F1 resolve.Fusion ->", hasattr(r, "Fusion"))
    fusion = None
    try:
        fusion = r.Fusion()
        print("   fusion:", type(fusion).__name__, fusion)
    except Exception as e:
        print("   Fusion() eccezione:", e)
        return 1

    t = R.timeline()
    items = t.GetItemListInTrack("video", 1) or []
    if not items:
        print("nessun clip su V1: serve una timeline con almeno un clip")
        return 1
    item = items[0]
    print("F2 clip di prova:", item.GetName(), item.GetStart())

    comp = None
    for how in ("GetFusionCompByIndex", "AddFusionComp"):
        try:
            if how == "AddFusionComp":
                comp = item.AddFusionComp()
            else:
                comp = item.GetFusionCompByIndex(1)
            print(f"   {how} ->", "None" if comp is None else type(comp).__name__)
            if comp is not None:
                break
        except Exception as e:
            print(f"   {how} eccezione:", e)
    if comp is None:
        return 1

    print("F3 nomi comp:", [n for n in (item.GetFusionCompNameList() or [])])

    txt = None
    for name in ("TextPlus", "Text1"):
        try:
            txt = comp.AddTool(name)
            if txt is not None:
                print(f"   AddTool({name}) -> ok")
                break
        except Exception as e:
            print(f"   AddTool({name}) eccezione:", e)
    if txt is None:
        print("   nessun tool creato")
        return 1

    attrs = txt.GetAttrs()
    print("   tool:", attrs.get("TOOLS_Name"), attrs.get("TOOLS_RegID"))
    print("   SetInput Text ->", txt.SetInput("Text", "CutLab test", 0))
    print("   GetInput Text ->", txt.GetInput("Text"))

    print("F4 spline: comp.BezierSpline ->", hasattr(comp, "BezierSpline"),
          "| fusion.BezierSpline ->", hasattr(fusion, "BezierSpline"))
    spline = None
    for label, fn in (("comp", lambda: comp.BezierSpline()),
                      ("fusion", lambda: fusion.BezierSpline())):
        try:
            spline = fn()
            if spline is not None:
                print(f"   spline creata con {label}:", type(spline).__name__)
                break
        except Exception as e:
            print(f"   {label}.BezierSpline eccezione:", e)

    if spline is not None:
        for meth in ("AddKey", "SetKeyFrames", "GetKeyFrames"):
            print(f"   spline ha {meth}:", hasattr(spline, meth))
        try:
            spline.AddKey(0.0, 0.05)
            spline.AddKey(1.0, 0.15)
            print("   AddKey x2 ok")
        except Exception as e:
            print("   AddKey eccezione:", e)
        try:
            ok = txt.SetInput("Size", spline, 0)
            print("   SetInput(Size, spline) ->", ok, "| ora:", txt.GetInput("Size"))
        except Exception as e:
            print("   SetInput(Size, spline) eccezione:", e)

    print("F5 SetExpression ->", hasattr(txt, "SetExpression"))
    if hasattr(txt, "SetExpression"):
        try:
            print("   SetExpression(Size, ...) ->", txt.SetExpression("Size", "0.06+0.02*sin(time*3)"))
        except Exception as e:
            print("   SetExpression eccezione:", e)

    print("F6 nodi nella comp:",
          [v.GetAttrs().get("TOOLS_Name") for v in (comp.GetToolList() or {}).values()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
