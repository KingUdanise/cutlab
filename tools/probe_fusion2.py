"""Seconda sonda Fusion: nomi degli input, scrittura valori, keyframe.

Domande:
  G1  TextPlus: quale input contiene il testo? (`Text`, `StyledText`, ...)
  G2  `SetInput` scrive davvero? (lo rileggo con GetInput)
  G3  `spline.SetKeyFrames({t: v})` accetta un dizionario?
  G4  `tool.SetInput("Size", spline)` collega la spline? lo si vede dal valore?
  G5  quali nodi servono per un motion graphic: Background, RectangleMask,
      Transform, Merge, EllipseMask?
  G6  come si impostano gli attributi del TextPlus (Font, Size, Tracking,
      Center, Red1/Green1/Blue1, Enabled1)?

    python tools/probe_fusion2.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import resolve_api as R  # noqa: E402

TEXT_CANDIDATES = ["StyledText", "Text", "Text1", "String"]
STYLE_INPUTS = ["Font", "Style", "Size", "Tracking", "Center", "Red1", "Green1", "Blue1",
                "Enabled1", "Thickness1", "VerticalJustification", "HorizontalJustification"]


def main() -> int:
    fusion = R._app().Fusion()
    t = R.timeline()
    item = (t.GetItemListInTrack("video", 1) or [])[0]
    comp = item.GetFusionCompByIndex(1) or item.AddFusionComp()
    print("comp:", item.GetFusionCompNameList())

    txt = None
    for tool in (comp.GetToolList() or {}).values():
        if tool.GetAttrs().get("TOOLS_RegID") == "TextPlus":
            txt = tool
            break
    if txt is None:
        txt = comp.AddTool("TextPlus")
    print("F3 tool:", txt.GetAttrs().get("TOOLS_Name"))

    # G1: elenco input reali
    try:
        ids = list(txt.GetInputList().keys())
        print(f"G1 input disponibili ({len(ids)}):", ids[:28])
    except Exception as e:
        print("G1 GetInputList eccezione:", e)
        ids = []

    # G1/G2: scrivere il testo giusto
    for name in TEXT_CANDIDATES:
        if ids and name not in ids:
            continue
        txt.SetInput(name, "MOTION LAB", 0)
        got = txt.GetInput(name)
        print(f"G2 SetInput({name!r}) -> riletto: {got!r}")
        if got:
            break

    # G6: stile
    for name in STYLE_INPUTS:
        if ids and name not in ids:
            continue
        try:
            if name in ("Center",):
                txt.SetInput(name, [0.5, 0.35], 0)
            elif name.startswith(("Red", "Green", "Blue")):
                txt.SetInput(name, 1.0 if "Red" in name else 0.6, 0)
            elif name in ("VerticalJustification", "HorizontalJustification"):
                txt.SetInput(name, 1, 0)
            else:
                txt.SetInput(name, 0.09 if name == "Size" else 1, 0)
            print(f"G6 {name} -> {txt.GetInput(name)!r}")
        except Exception as e:
            print(f"G6 {name} eccezione: {e}")

    # G3: keyframes
    spline = comp.BezierSpline()
    for label, val in (("dict", {0.0: 0.02, 24.0: 0.12}), ("list", [(0.0, 0.02), (24.0, 0.12)])):
        try:
            r = spline.SetKeyFrames(val)
            keys = spline.GetKeyFrames()
            print(f"G3 SetKeyFrames({label}) -> {r!r}, keys={keys!r}")
            if keys:
                break
        except Exception as e:
            print(f"G3 SetKeyFrames({label}) eccezione: {e}")

    # G4: collegare la spline all'input
    try:
        ok = txt.SetInput("Size", spline, 0)
        print("G4 SetInput(Size, spline) ->", ok, "valore ora:", txt.GetInput("Size"))
    except Exception as e:
        print("G4 eccezione:", e)

    # G5: nodi per il motion graphic
    for name in ("Background", "RectangleMask", "EllipseMask", "Transform", "Merge",
                 "TextPlus", "Paint", "Gradient"):
        try:
            tool = comp.AddTool(name)
            print(f"G5 {name} ->", None if tool is None else tool.GetAttrs().get("TOOLS_Name"))
        except Exception as e:
            print(f"G5 {name} eccezione: {e}")

    print("nodi finali:", [v.GetAttrs().get("TOOLS_Name")
                           for v in (comp.GetToolList() or {}).values()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
