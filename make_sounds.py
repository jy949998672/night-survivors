"""make_sounds.py: build the short sound effects for Night Survivors.

Run this on your own computer:  python make_sounds.py
It uses only Python's standard library (wave, math, struct), so it needs
nothing installed. It writes small .wav files into sounds/ that main.py loads.
main.py never imports this file (a browser cannot run it), it only plays the
finished files.

Each sound is a tiny melody of sine tones with a quick fade so there are no
clicks at the start or end. Numbers are 16-bit mono at RATE samples/second.
"""

import math
import os
import struct
import wave

RATE = 22050          # samples per second
AMPLITUDE = 11000     # out of 32767, so it is not too loud
FADE = 0.006          # seconds of fade in/out at the edges of every tone


def tone(freq, dur, vol=1.0):
    """A sine wave at freq Hz for dur seconds, with a short fade at both ends."""
    n = int(RATE * dur)
    samples = []
    fade_n = max(1, int(RATE * FADE))
    for i in range(n):
        env = 1.0
        if i < fade_n:
            env = i / fade_n
        elif i > n - fade_n:
            env = (n - i) / fade_n
        samples.append(vol * env * math.sin(2.0 * math.pi * freq * (i / RATE)))
    return samples


def sequence(notes):
    """Join a list of (freq, dur) or (freq, dur, vol) tones into one sample list."""
    out = []
    for note in notes:
        freq, dur = note[0], note[1]
        vol = note[2] if len(note) > 2 else 1.0
        out.extend(tone(freq, dur, vol))
    return out


def write_wav(path, samples):
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        frames = bytearray()
        for s in samples:
            v = max(-1.0, min(1.0, s))
            frames += struct.pack("<h", int(v * AMPLITUDE))
        f.writeframes(bytes(frames))


def build():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "sounds")
    os.makedirs(out_dir, exist_ok=True)

    sounds = {
        # a quick high blip every time a weapon hits
        "hit": sequence([(880.0, 0.05)]),
        # two rising pings when you collect a gem
        "gem": sequence([(660.0, 0.06), (990.0, 0.08)]),
        # a happy ascending run when you level up
        "levelup": sequence([(523.0, 0.09), (659.0, 0.09), (784.0, 0.09), (1046.0, 0.16)]),
        # a low buzz when the hero is hurt
        "hurt": sequence([(170.0, 0.12, 1.0), (120.0, 0.12, 0.9)]),
        # a falling tone for the end screen
        "end": sequence([(660.0, 0.14), (440.0, 0.14), (330.0, 0.22)]),
    }

    for name, samples in sounds.items():
        path = os.path.join(out_dir, name + ".wav")
        write_wav(path, samples)
        seconds = len(samples) / RATE
        print(f"[sounds] wrote {os.path.relpath(path, here)} ({seconds:.2f}s)")


if __name__ == "__main__":
    build()
