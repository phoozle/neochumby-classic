"""limits: the engine's limits, in one place.

The engine on the Chumby holds a fixed number of each thing a manifest can name, and
skips a line past a limit. A render over any of them is refused here, whole, so the
Chumby keeps its last good render instead of silently losing pages or screens. These are
the neochumby-classic engine's limits (v1; apps/classic/src/manifest.rs, the "limits"
block). The table in docs/widgets.md ("The engine's limits") must say the same:
tests/smoke.py checks it. When the engine changes a limit, change it here and there.
"""

MAXP = 128                      # slideshow pages
LIMITS = {                      # manifest directive -> how many the engine holds
    "page": MAXP,
    "screen": 512,              # each _night copy counts
    "slider": 512,
    "pane": 512,                # on screens and pages together
    "hot": 2048,
    "sprite": 256,
    "actor": 128,
    "glow": 32,
    "glowseq": 8,
    "flurry": 8,
    "station": 64,
    "sun": 32,
    "secsopts": 12,
    "livefont": 32,
    "live": 512,
}
ASSETS = 4096                   # different files the manifest names (glowseq frames and fonts included)
SCREEN_NAME = 48                # bytes in a screen name ("_night" included), a pagetap/hot argument,
                                # a pane or live target, a font name
FILE_NAME = 96                  # bytes in a file name
GLOWSEQ_PREFIX = 88             # bytes in a glowseq prefix (the frames are PREFIX_NNN.l8)
GLOWSEQ_FRAMES = 1000           # frames in one glowseq clip
ACTION = 15                     # bytes in a hot zone's action
PANE_VAR = 11                   # bytes in a pane's variable
MANIFEST_BYTES = 2 * 1024 * 1024   # manifest.txt and hashes.txt, each (the engine ignores a bigger one)
COORD = (-32768, 32767)         # positions and sizes
