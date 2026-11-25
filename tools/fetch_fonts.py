"""Scarica Manrope da Google Fonts e genera web/fonts.css con i path locali.

L'app deve funzionare offline: niente CDN a runtime, i woff2 stanno in web/fonts.
I sottoinsiemi (latin, latin-ext, ...) vanno dichiarati con il loro
`unicode-range`, altrimenti il browser puo' scegliere il file sbagliato e le
lettere accentate spariscono.
"""
from __future__ import annotations

import os
import re
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(HERE, "web")
FONTS = os.path.join(WEB, "fonts")
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
CSS_URL = ("https://fonts.googleapis.com/css2?"
           "family=Manrope:wght@400;500;600;700&display=swap")


def main() -> None:
    os.makedirs(FONTS, exist_ok=True)
    req = urllib.request.Request(CSS_URL, headers=UA)
    css = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

    blocks = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
    out = ["/* Manrope - generato da tools/fetch_fonts.py, licenza OFL */"]
    n = 0
    for b in blocks:
        fam = re.search(r"font-family:\s*'([^']+)'", b)
        weight = re.search(r"font-weight:\s*([0-9]+)", b)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", b)
        ur = re.search(r"unicode-range:\s*([^;]+);", b)
        if not (fam and weight and url):
            continue
        n += 1
        name = f"manrope-{weight.group(1)}-{n}.woff2"
        path = os.path.join(FONTS, name)
        if not os.path.exists(path):
            r = urllib.request.Request(url.group(1), headers=UA)
            with open(path, "wb") as fh:
                fh.write(urllib.request.urlopen(r, timeout=30).read())
        out.append(
            "@font-face {\n"
            f"  font-family: 'Manrope';\n  font-style: normal;\n"
            f"  font-weight: {weight.group(1)};\n  font-display: swap;\n"
            f"  src: url('fonts/{name}') format('woff2');\n"
            + (f"  unicode-range: {ur.group(1).strip()};\n" if ur else "")
            + "}")
    with open(os.path.join(WEB, "fonts.css"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"scritti {n} @font-face in web/fonts.css")


if __name__ == "__main__":
    main()
