"""fakeclips: synthetic clips in clip format v1 (neochumby-scummvm's docs/clip-format.md).

Made-up pictures only: plain colour bands that slide. Nothing from any game is in this
repository or its tests.

    write(dir, name, frames=6, fps=12)    NAME.idx and NAME.clip
    listing(dir, [(name, game, title)])   clips.txt, as the converter writes it (last)
"""
import os
import struct

W, H = 320, 240


def _span(start, n, colour):
    """A fill span: one RGB565 pixel repeated n times, padded to 4 bytes."""
    return struct.pack("<IHH", start | n << 17 | 1 << 31, colour, 0)


def _record(kind, spans):
    return struct.pack("<BBHI", kind, 0, 0, len(spans)) + b"".join(spans)


def write(d, name, frames=6, fps=12, seed=0):
    colours = [(0x1F + seed * 0x0841 + k * 0x2104) & 0xFFFF for k in range(frames)]
    recs = [_record(0, [_span(y * W, W, colours[0]) for y in range(H)])]              # frame 0: a key
    for k in range(1, frames):                                                         # deltas: one band moves
        recs.append(_record(1, [_span(y * W + 40 * (k % 6), 40, colours[k]) for y in range(100, 140)]))
    recs.append(_record(1, []))                                                        # the wrap: nothing changes
    data = b"".join(recs)
    idx = b"NCCLIP01" + struct.pack("<HHHHIIII", 1, W, H, fps, frames, 0, max(len(r) for r in recs), len(data))
    off = 0
    for r in recs:
        idx += struct.pack("<II", off, len(r))
        off += len(r)
    os.makedirs(d, exist_ok=True)
    for ext, blob in ((".clip", data), (".idx", idx)):
        tmp = os.path.join(d, f"{name}{ext}.tmp")
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, os.path.join(d, name + ext))
    return frames, len(data)


def listing(d, clips, fps=12):
    lines = []
    for k, (name, game, title) in enumerate(clips):
        frames, size = write(d, name, frames=4 + k, fps=fps, seed=k)
        lines.append(f"{name} {game} {fps} {frames} {frames / fps:.1f} {size} {title}\n")
    tmp = os.path.join(d, "clips.txt.tmp")
    with open(tmp, "w") as fh:
        fh.writelines(lines)
    os.replace(tmp, os.path.join(d, "clips.txt"))
