"""limits: the engine's limits, in one place.

The engine on the Chumby holds a fixed number of each thing a manifest can name (MAXP,
MAXSCR, ... in its source). A render over any of them is refused here, whole, so the
Chumby keeps its last good render instead of silently losing pages or screens. The
table in docs/widgets.md ("The engine's limits") must say the same: tests/smoke.py
checks it. When the engine raises a limit, change it here and in that table.
"""

MAXP = 48                       # slideshow pages
LIMITS = {                      # manifest directive -> how many the engine holds (its name)
    "page": MAXP,               # MAXP
    "screen": 160,              # MAXSCR: each _night copy counts
    "pane": 128,                # MAXPANE
    "hot": 768,                 # MAXHOT
    "sprite": 64,               # MAXS
    "actor": 64,                # MAXA
    "glow": 16,                 # MAXGLOW
    "glowseq": 4,               # MAXGSQ
    "station": 16,              # MAXST
    "sun": 16,                  # MAXSUN
}
ASSETS = 640                    # MAXASSET: different files the manifest names
SCREEN_NAME = 23                # characters in a screen name, "_night" included
FILE_NAME = 63                  # characters in a file name
GLOWSEQ_FRAMES = 1000           # MAXGSQ_FRAMES: frames in one glowseq clip
