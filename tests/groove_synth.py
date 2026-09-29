import random
# rhythms per bar: list of (beat position within bar in beats, relative strength)
BARS = {
 3: [[(0,1.0),(1,0.6),(2,0.6)], [(0,1.0),(2,0.6)], [(0,1.0),(1,0.6),(1.5,0.5),(2,0.6)], [(0,1.0),(1.5,0.5),(2,0.6)]],
 4: [[(0,1.0),(1,0.6),(2,0.8),(3,0.6)], [(0,1.0),(1.5,0.5),(2,0.8),(3,0.6)], [(0,1.0),(0.5,0.5),(1,0.6),(2,0.8),(2.5,0.5),(3,0.6)], [(0,1.0),(2,0.8),(3,0.6),(3.5,0.5)]],
}
def notes(meter, bars=12, swing=0.0, downbeat=1, seed=0, jitter=0.02, accent=True):
    """(t, beat, pos, velocity) with beat numbers such that beat b is '1' when (b-downbeat)%meter==0."""
    rng=random.Random(seed); out=[]; period=0.6
    for bar in range(bars):
        for pos_in_bar, strength in rng.choice(BARS[meter]):
            beat = downbeat + bar*meter + int(pos_in_bar)
            frac = pos_in_bar - int(pos_in_bar)
            if frac == 0.5: frac = (1+swing)/2
            frac = frac + rng.uniform(-jitter, jitter)
            b, f = (beat, frac) if frac >= 0 else (beat-1, frac+1)
            vel = int(60 + (35*strength if accent else 20) + rng.uniform(-10,10))
            out.append(((b+f)*period, b, f, vel))
    return sorted(out)
