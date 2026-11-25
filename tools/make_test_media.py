"""Genera materiale di test realistico per CutLab.

Produce in out/test/:
  cam_a.mp4   camera 1 (720p24, timecode bruciato, audio d'ambiente con entrambe le voci)
  cam_b.mp4   camera 2 (idem, come se fosse l'altra angolazione)
  lav.wav     48 kHz mono: la traccia audio "buona" (lav dell'interlocutore) in cui
              pero' si sente anche il cameraman che parla -> e' il caso da risolvere
  truth.json  ground truth: chi parla, quando, e le pause da rimuovere

Le voci vengono da SAPI (Cosimo = interlocutore, Elsa = cameraman): due timbri
realmente diversi, cosi' la diarizzazione si puo' misurare davvero.

    python tools/make_test_media.py
"""
from __future__ import annotations

import json
import os
import subprocess
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "out", "test")
TTS = os.path.join(OUT, "tts")
SR = 48000
FPS = 24

INT = ("Microsoft Cosimo", "it-IT", "male")     # interlocutore
CAM = ("Microsoft Elsa", "it-IT", "female")     # cameraman

# (pausa_prima_ms, chi, testo)
SCRIPT = [
    (2000, "sil", ""),
    (0, "int", "Oggi parliamo di come funziona il nostro laboratorio e di cosa facciamo ogni giorno con i clienti."),
    (900, "cam", "Si, vai tranquillo."),
    (700, "int", "Il lavoro principale e la rigenerazione delle batterie al litio, ma facciamo anche diagnostica elettronica avanzata su tutti i moduli."),
    (1400, "cam", "Scusa, un attimo, il microfono e a posto?"),
    (600, "int", "Si, perfetto. Quindi dicevo, la parte piu delicata e la misura della capacita residua."),
    (2500, "int", "Poi c'e il test di carica e scarica, che dura alcune ore per ogni pacco batteria."),
    (800, "cam", "Quanto tempo ci vuole in media?"),
    (500, "int", "Dipende dal modello, ma in media tra le sei e le otto ore."),
    (3000, "int", "Alla fine consegniamo un report con tutti i dati misurati e le curve di scarica."),
    (1500, "sil", ""),
]

PS_SYNTH = r"""
param([string]$Voice, [string]$Text, [string]$Out)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($Voice)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($Out, $fmt)
$s.Speak($Text)
$s.Dispose()
"""


def synth(text: str, voice: str, path: str) -> None:
    ps = os.path.join(OUT, "_tts.ps1")
    with open(ps, "w", encoding="utf-8") as fh:
        fh.write(PS_SYNTH)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
                    "-Voice", voice, "-Text", text, "-Out", path],
                   check=True, capture_output=True)


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def main() -> None:
    os.makedirs(TTS, exist_ok=True)
    rng = np.random.default_rng(7)

    chunks: list[np.ndarray] = []
    truth = {"fps": FPS, "sample_rate": SR, "utterances": [], "silences": []}
    cursor = 0  # campioni

    for i, (pause_ms, who, text) in enumerate(SCRIPT):
        pause = int(SR * pause_ms / 1000)
        if pause:
            if who == "sil" and i == 0:
                # pre-roll: silenzio + rumore di fondo
                truth["silences"].append([cursor / SR, (cursor + pause) / SR])
            chunks.append(np.zeros(pause, dtype=np.float32))
            cursor += pause
        if who == "sil":
            continue

        voice = INT[0] if who == "int" else CAM[0]
        p = os.path.join(TTS, f"u{i:02d}_{who}.wav")
        if not os.path.exists(p):
            synth(text, voice, p)
        a = read_wav(p)
        start = cursor
        chunks.append(a)
        cursor += len(a)
        truth["utterances"].append({
            "id": i, "speaker": "interlocutore" if who == "int" else "cameraman",
            "start": round(start / SR, 3), "end": round(cursor / SR, 3),
            "text": text,
        })

    total = cursor
    out = np.zeros(total, dtype=np.float32)
    at = 0
    for c in chunks:
        out[at:at + len(c)] += c
        at += len(c)

    # rumore di fondo costante (-66 dBFS): il VAD a soglia RMS di aeoncut qui si rompe
    out += rng.normal(0, 10 ** (-66 / 20), total).astype(np.float32)
    out = np.clip(out, -1, 1)

    lav = os.path.join(OUT, "lav.wav")
    with wave.open(lav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((out * 32767).astype("<i2").tobytes())

    dur = total / SR
    truth["duration"] = round(dur, 3)
    with open(os.path.join(OUT, "truth.json"), "w", encoding="utf-8") as fh:
        json.dump(truth, fh, ensure_ascii=False, indent=1)

    # due camere: video + audio d'ambiente (le voci, piu' basse e con rumore)
    for name, color, shift in (("cam_a.mp4", "0x1b2a4a", 0), ("cam_b.mp4", "0x3a2418", 0)):
        dst = os.path.join(OUT, name)
        if os.path.exists(dst):
            continue
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=1280x720:r={FPS}:d={dur:.3f}",
            "-i", lav,
            "-filter_complex",
            f"[0:v]drawtext=text='{name[4]}\\:  %{{pts\\:hms}}':x=40:y=40:fontsize=44:fontcolor=white,"
            f"drawgrid=w=160:h=90:t=1:c=white@0.12[v];"
            f"[1:a]volume=0.35,highpass=f=80,lowpass=f=9000,aformat=channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", dst,
        ], check=True)
        print("fatto", dst)

    print(f"lav.wav  {dur:.2f}s   {len(truth['utterances'])} battute")
    for u in truth["utterances"]:
        print(f"  {u['start']:7.2f}-{u['end']:7.2f}  {u['speaker']:13} {u['text'][:60]}")


if __name__ == "__main__":
    main()
