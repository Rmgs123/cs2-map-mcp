"""Deterministic railgun sound design. No recordings/downloads; PCM layers leave mix headroom."""
import numpy as np

RATE = 44100


def generate():
    rng = np.random.default_rng(907)
    def time(seconds):
        return np.arange(round(seconds * RATE)) / RATE
    def noise(t):
        return rng.uniform(-1, 1, len(t))
    def peak(x, gain):
        return (x * (gain / max(float(np.max(np.abs(x))), 1e-12))).reshape(-1, 1)
    t = time(.22)
    n = noise(t)
    crack = (n - np.roll(n, 1)) * np.exp(-t * 48) * (1 - np.exp(-t * 1800))
    t = time(.38)
    body = (np.sin(2 * np.pi * (90 * t - 38 * t * t)) + .3 * noise(t) * np.exp(-t * 30)) * np.exp(-t * 13)
    t = time(.85)
    tail = (np.sin(2 * np.pi * (1800 * t - 720 * t * t)) * .55 +
            np.sin(2 * np.pi * 2311 * t) * .22 + noise(t) * .3) * np.exp(-t * 6) * (1 - np.exp(-t * 75))
    t = time(.9)
    charge = (np.sin(2 * np.pi * (160 * t + 530 * t * t)) + .2 * np.sin(2 * np.pi * 1380 * t))
    charge *= np.sin(np.pi * np.minimum(t / .9, 1)) ** .65
    t = time(.09)
    empty = (noise(t) + .5 * np.sin(2 * np.pi * 950 * t)) * np.exp(-t * 110) * (1 - np.exp(-t * 1700))
    t = time(1.6)
    reload = np.zeros_like(t)
    for at, hz, gain in ((0, 260, .8), (.36, 170, .6), (1.12, 900, 1), (1.38, 420, .6)):
        u = np.maximum(0, t - at)
        reload += (t >= at) * (np.sin(2 * np.pi * hz * u) + .4 * noise(t)) * np.exp(-u * 55) * (1 - np.exp(-u * 1200)) * gain
    sounds = {'crack': peak(crack, .22), 'body': peak(body, .25), 'tail': peak(tail, .18),
              'charge': peak(charge, .35), 'empty': peak(empty, .22), 'reload': peak(reload, .28)}
    mix = np.zeros((len(sounds['tail']), 1))
    for name in ('crack', 'body', 'tail'):
        mix[:len(sounds[name])] += sounds[name]
    return sounds, {'shot_peak': float(np.max(np.abs(mix))), 'shot_peak_bound': .65,
                    'rate': RATE, 'layers': 3, 'seed': 907}
