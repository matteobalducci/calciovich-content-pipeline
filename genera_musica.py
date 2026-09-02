#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_musica.py — sintetizza una traccia ORIGINALE in stile marimba centroamericana/cumbia
(suono tipico salvadoregno), copyright-free al 100%, sicura per la monetizzazione YouTube.
v2: piu' immersiva (pad caldo + riverbero) e piu' ritmata (shaker/guiro + basso groovy).
La minore pentatonica, dolce e malinconica, pensata per stare SOTTO la voce.

USO:
  python3 genera_musica.py                       # -> assets/calciovich-marimba.mp3 (loop)
Dipendenze: numpy + imageio-ffmpeg (gia' installate).
"""
import os, wave, subprocess, tempfile
import numpy as np
import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "assets", "calciovich-marimba.mp3")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
SR   = 44100
BPM  = 100
BEAT = 60.0 / BPM
BAR  = BEAT * 4
rng  = np.random.default_rng(7)

A1,E2,A2,B2,E2b,G2,C3,D3,E3,A3,C4,D4,E4,G4,A4,B4,C5,D5,E5 = (
 55,82.41,110,123.47,82.41,98,130.81,146.83,164.81,220,261.63,293.66,329.63,392,440,493.88,523.25,587.33,659.25)

def env_exp(t, tau): return np.exp(-t/tau)

def marimba(freq, dur, amp=0.5):
    t = np.arange(int(dur*SR))/SR
    e = env_exp(t, 0.16 + 60/freq*0.002)
    w  = np.sin(2*np.pi*freq*t)
    w += 0.55*np.sin(2*np.pi*4*freq*t)*env_exp(t,0.07)   # armonica 4:1 (timbro a barre)
    w += 0.12*np.sin(2*np.pi*9.2*freq*t)*env_exp(t,0.03)
    return amp*e*w

def bass(freq, dur, amp=0.5):
    t = np.arange(int(dur*SR))/SR
    e = env_exp(t, 0.22)
    w = np.sin(2*np.pi*freq*t) + 0.3*np.sin(2*np.pi*2*freq*t)*env_exp(t,0.1)
    return amp*e*w

def pad(freq, dur, amp=0.18):
    t = np.arange(int(dur*SR))/SR
    a = np.minimum(t/0.6, 1.0) * np.minimum((dur-t)/0.6, 1.0)   # fade in/out morbido
    w = (np.sin(2*np.pi*freq*t) + np.sin(2*np.pi*freq*1.5*t)*0.5
         + np.sin(2*np.pi*freq*2*t)*0.3)
    return amp*np.clip(a,0,1)*w

def shaker(dur, amp=0.25):
    n = int(dur*SR); t = np.arange(n)/SR
    e = env_exp(t, 0.02)
    noise = rng.standard_normal(n)
    # passa-alto rudimentale: differenza prima
    noise = np.diff(noise, prepend=0.0)
    return amp*e*noise

def place(buf, sig, start, panL=1.0, panR=1.0):
    s = int(start*SR); e = min(s+len(sig), len(buf[0]))
    buf[0][s:e] += sig[:e-s]*panL
    buf[1][s:e] += sig[:e-s]*panR

# progressione i - III - VII - i, 8 battute (loop)
BARS = [(A2,[A3,C4,E4,A4]),(C3,[C4,E4,G4,C5]),(G2,[G2*2,D4,E4,G4]),(A2,[A3,C4,E4,A4]),
        (A2,[A3,E4,A4,C5]),(C3,[C4,G4,C5,E5]),(G2,[G2*2,D4,G4,D5]),(A2,[A3,C4,E4,A4])]
PAD_ROOT = [A3,C4,G2*2,A3, A3,C4,G2*2,A3]
ARP = [0,1,2,3,2,3,1,2]

def main():
    total = int(len(BARS)*BAR*SR) + SR
    L = np.zeros(total); R = np.zeros(total); buf=(L,R)
    for bi,(root,notes) in enumerate(BARS):
        b0 = bi*BAR
        # pad caldo (immersione), tutta la battuta
        place(buf, pad(PAD_ROOT[bi]/2, BAR*1.02, 0.16), b0, 0.9, 0.9)
        # basso groovy: 1, &2, 3 (feel cumbia)
        for off,a in ((0,0.5),(1.5,0.34),(2,0.46)):
            place(buf, bass(root, 0.9, a), b0+off*BEAT, 0.95, 0.75)
        # marimba ostinato (crome) — pan dx
        for k in range(8):
            f=notes[ARP[k]]; amp=0.26*(0.85+0.15*(k%2==0))
            place(buf, marimba(f,0.6,amp), b0+k*0.5*BEAT, 0.7, 0.95)
        # marimba contrappunto alto, sparso (immersione melodica) — pan sx
        for k in (2,6):
            place(buf, marimba(notes[(ARP[k]+2)%4]*2, 0.5, 0.12), b0+k*0.5*BEAT, 0.9, 0.6)
        # shaker su tutte le crome con accenti (ritmo)
        for k in range(8):
            a = 0.18 if k%2 else 0.10
            place(buf, shaker(0.09, a), b0+k*0.5*BEAT, 0.8, 0.85)
    # riverbero multi-tap (spazio/immersione)
    for d,g in ((0.13,0.22),(0.27,0.14),(0.41,0.08)):
        s=int(d*SR); L[s:]+=g*R[:total-s]; R[s:]+=g*L[:total-s]
    # normalizza morbido
    peak=max(np.max(np.abs(L)),np.max(np.abs(R)),1e-6)
    L*=0.72/peak; R*=0.72/peak
    pcm=(np.clip(np.stack([L,R],axis=1),-1,1)*32767).astype('<i2')

    wav=tempfile.mktemp(suffix=".wav")
    with wave.open(wav,'w') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff,"-y","-i",wav,"-c:a","libmp3lame","-b:a","160k",OUT],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    os.remove(wav)
    print("OK ->", OUT, f"({len(L)/SR:.1f}s)")

if __name__ == "__main__":
    main()
