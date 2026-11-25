"""Terza sonda Fusion: nomi veri degli input e verifica del collegamento spline.

    python tools/probe_fusion3.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import resolve_api as R  # noqa: E402


def input_names(tool) -> dict:
    """id numerico -> nome leggibile dell'input (INPS_Name / INPS_ID)."""
    out = {}
    try:
        for k, v in (tool.GetInputList() or {}).items():
            try:
                a = v.GetAttrs()
                out[a.get("INPS_ID") or a.get("INPS_Name") or str(k)] = a.get("INPS_Name")
            except Exception:
                continue
    except Exception as e:
        print("GetInputList eccezione:", e)
    return out


def main() -> int:
    t = R.timeline()
    item = (t.GetItemListInTrack("video", 1) or [])[0]
    comp = item.GetFusionCompByIndex(1) or item.AddFusionComp()
    txt = None
    for tool in (comp.GetToolList() or {}).values():
        if tool.GetAttrs().get("TOOLS_RegID") == "TextPlus":
            txt = tool
            break
    if txt is None:
        txt = comp.AddTool("TextPlus")

    names = input_names(txt)
    wanted = ["StyledText", "Text", "Font", "Style", "Size", "Tracking", "Center",
              "Red1", "Green1", "Blue1", "Enabled1", "Thickness1", "LayoutType",
              "VerticalJustificationNew", "HorizontalJustificationNew"]
    print("input che ci servono:")
    for w in wanted:
        where = [k for k, v in names.items() if v == w]
        print(f"   {w:26} {'OK ' + where[0] if where else '— assente'}")

    print(f"\nprimi 40 input: {sorted(set(names.values()))[:40]}")

    # scrivere il testo con il nome giusto
    for cand in ("StyledText", "Text"):
        if cand in names.values():
            txt.SetInput(cand, "MOTION LAB", 0)
            got = txt.GetInput(cand)
            print(f"\n{cand} -> riletto {type(got).__name__}: {got!r}")
            if got:
                break

    # stile
    txt.SetInput("Font", "Open Sans", 0) if "Font" in names.values() else None
    if "Size" in names.values():
        txt.SetInput("Size", 0.08, 0)
        print("Size ->", txt.GetInput("Size"))
    if "Center" in names.values():
        txt.SetInput("Center", [0.5, 0.6], 0)
        print("Center ->", txt.GetInput("Center"))

    # spline collegata?
    spline = comp.BezierSpline()
    spline.SetKeyFrames({0: 0.02, 20: 0.09, 40: 0.09})
    txt.SetInput("Size", spline, 0)
    val = txt.GetInput("Size")
    print("Size dopo SetInput(spline) ->", type(val).__name__, val)
    if hasattr(val, "GetKeyFrames"):
        print("   keyframe letti:", val.GetKeyFrames())
    return 0


if __name__ == "__main__":
    sys.exit(main())
