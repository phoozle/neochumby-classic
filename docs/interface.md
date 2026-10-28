# The output interface (for neochumby's companion)

The renderer writes each Chumby's pictures into a directory of its own. A neochumby
companion reads that directory and delivers it to the Chumby. This page is the contract
between the two. The renderer is the only writer; the companion only reads.

Interface version: **1**.

## Layout

```
$NEOCHUMBY_CLASSIC_OUT/              default /var/lib/neochumby-classic/out
  <chumby>/                          one per Chumby, named as neochumbyd names it: [a-z0-9_-]{1,32}
    version                          the "done" marker: written last (below)
    manifest.txt                     what the engine draws, one directive per line
    hashes.txt                       md5sum format: every file the manifest names
    *.raw *.l8 *.path                the files the manifest names
    apps.json                        for the web page's previews only: not delivered
    .<name>.<pid>.tmp                a write in progress: never read, never deliver
```

- `<chumby>` appears when the renderer first renders that Chumby. It isn't removed when a
  Chumby is unpaired (the companion only serves paired Chumbys anyway).
- **Permissions** (install.sh): `$NEOCHUMBY_CLASSIC_OUT` and each `<chumby>` directory are
  owned by the renderer's user and by neochumbyd's group, mode `2750` (setgid, so new
  directories keep the group). Files are `0640`. The companion, running as neochumbyd,
  reads them through the group.
- Other names may appear in future (they will start with a letter). Deliver only what
  `hashes.txt` lists, plus `manifest.txt` and `hashes.txt` themselves.

## How files are written

- **Atomically.** Every file is written under `.<name>.<pid>.tmp` in the same directory,
  then `rename(2)`d over `<name>`. A reader opening `<name>` gets the whole old file or the
  whole new one, never half of either.
- **Only when it changes.** A file whose bytes would be the same is not touched: same
  inode, same mtime. A render that changes nothing writes nothing at all, `version`
  included.
- **Stable names.** A picture keeps its name from render to render (`weather_current.raw`,
  `stocks_d2.raw`), so an unchanged picture never needs sending again.
- **In order.** A render that changes something writes:
  1. the pictures (`*.raw`, `*.l8`, `*.path`) that changed, in any order;
  2. `hashes.txt`;
  3. `manifest.txt`;
  4. `version`, **last**.
- **Old files.** A file the current manifest no longer names, and a `.tmp` file left by a
  crash, is deleted once it is an hour old.

## `version`: the marker to watch

One line, ASCII:

```
VERSION DIGEST\n
```

- `VERSION` is a decimal integer. It goes up with every render that changes anything,
  and never goes down or repeats for a Chumby. It is close to the render's Unix time, but
  don't rely on that.
- `DIGEST` is 16 lowercase hex digits: a SHA-256 prefix over `hashes.txt` and the
  manifest's directives.

The same two values open `manifest.txt`:

```
# classic DIGEST
version VERSION
...directives...
```

So `version`, `manifest.txt` and `hashes.txt` belong together when `version`'s values
match the manifest's first two lines.

## Reading a version (what the companion should do)

1. **Notice a change** in `<chumby>/version`: inotify on the directory (`IN_MOVED_TO` for
   `version`), or stat it every few seconds. Nothing else needs watching.
2. **Read** `version`, then `manifest.txt`. If the manifest's `# classic` digest or its
   `version` line differs from `version`'s, a render is part-way through: wait a second
   and start again.
3. **Read** `hashes.txt`. It lists every file to deliver: `MD5  NAME` per line, names
   matching `[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}`. Refuse anything else (no `/`, no leading
   `.`).
4. **Copy or hash each file** and compare its md5 with `hashes.txt`. A mismatch means the
   next render replaced it while you read: start again from 2. (md5 here only detects a
   race with the renderer. It is not a security check: the renderer is local and trusted,
   and sync computes its own SHA-256 for the Chumby.)
5. **Read `version` again.** If it changed, start again from 2.
6. **Deliver** `manifest.txt`, `hashes.txt` and the listed files, then tell the Chumby
   (the earlier companion sent one frame: `[0]` followed by `VERSION` in ASCII digits).

Renders of one Chumby are at least seconds apart (one runs at a time per server, and a
render takes seconds), so step 4 rarely has to retry.

## The files

| Kind | Format |
| --- | --- |
| `.raw` | RGB565, little-endian, row-major, `W × H × 2` bytes, the size the directive gives. `0xF81F` is transparent in sprites, logos and knobs. Sprite sheets stack their frames vertically. |
| `.l8` | One light frame for `glowseq`: a u32 span count, then per span a u32 `start | len << 17` (`start = y*320 + x`, `len ≤ 320`, one row) and `len` intensity bytes. |
| `.path` | An `actor`'s path: int16 x, y per frame, at 25 fps. |
| `manifest.txt` | UTF-8 text, one directive per line; `#` lines are comments. The directives are in [widgets.md](widgets.md#what-a-widget-may-write). |
| `hashes.txt` | `md5sum` format, sorted by name. The engine also uses it to key its cache by content. |

A full render (every built-in widget on) is about 18 MB in about 150 files. The largest single file is the aurora's
960 × 240 panorama (460,800 bytes).

## Asking for a render

The companion (or anything in neochumbyd's group) can ask:

| Create | Effect |
| --- | --- |
| `$NEOCHUMBY_CLASSIC_STATE/refresh/<chumby>` | that Chumby is rendered within a second or so; the file is removed |
| touch `$NEOCHUMBY_CLASSIC_STATE/refresh-request` | every Chumby is rendered (the web page does this on every save) |

Otherwise each Chumby is rendered when it first appears, then every
`NEOCHUMBY_CLASSIC_EVERY` seconds (default 600).

## Facts from the Chumby

If the companion learns facts about the Chumby (from its session), it can write them to
`$NEOCHUMBY_CLASSIC_STATE/info/<chumby>` (atomically: a temp file, then rename), as
`key=value` lines: `ssid ap rate signal quality ip mac gateway dns hostname firmware
kernel engine uptime_s memfree usb asof`. The Settings → Network and Chumby Info screens
show them at the next render; without the file they show "?". Don't include values that
change every minute (an uptime is fine at the companion's own pace, but each change
re-renders those two screens).

`refresh/` and `info/` are mode `2770` in neochumbyd's group, so the companion can write
there.

## Which Chumbys

The renderer renders every Chumby neochumbyd lists as paired (`paired` on its admin socket,
asked every 30 s), or the names in `NEOCHUMBY_CLASSIC_CHUMBYS` if that is set. Each Chumby
plays its own channel if `config.json` assigns one (`chumbys`), else the default (the web page
has only the default channel for now).
