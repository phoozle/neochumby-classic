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
    *.raw *.l8 *.path *.idx *.clip   the files the manifest names
    apps.json                        for the web page's previews only: not delivered
    .<name>.<pid>.tmp                a write in progress: never read, never deliver
```

- `<chumby>` appears when the renderer first renders that Chumby. It isn't removed when a
  Chumby is unpaired (the companion only serves paired Chumbys anyway).
- **Permissions** (install.sh, and docker-compose.yml's first steps): `$NEOCHUMBY_CLASSIC_OUT`
  and each `<chumby>` directory are owned by the renderer's user and by neochumbyd's group,
  mode `2750` (setgid, so new directories keep the group). Files are `0640`. The companion,
  running as neochumbyd, reads them through the group. The renderer and the web page run
  as their own user (`neochumby-classic`), never as neochumbyd's. Under Docker,
  neochumbyd's group is their only group, and they see none of neochumbyd's state
  directory, only its admin socket and its app tree.
- **neochumbyd with `--plugin-uids`** (johnmerchant/neochumby#53, if it's merged) runs each
  app's companion under a uid of the app's own, with no other groups, so neochumbyd's
  group no longer reaches it. Then, as root, give `$NEOCHUMBY_CLASSIC_OUT`, `refresh/` and
  `info/` (and what's in them) to the classic app's group in place of neochumbyd's: its
  uid, which is also its gid, is on the `classic` line of neochumbyd's
  `<state>/plugin-uids`. The setgid bit keeps new files in that group. Untested so far.
  Without the flag, nothing changes.
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
| `.idx`, `.clip` | A `clip`'s index and records (clip format v1, [neochumby-scummvm's docs/clip-format.md](https://github.com/phoozle/neochumby-scummvm/blob/main/docs/clip-format.md)). A few MB each: stream them, don't cache them whole. |
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

## Home → APPS

Home's APPS button opens screen `apps_p0` (and `apps_p1`, ... six apps a page, each with a
`_night` copy): a tile for every app in neochumbyd's tree (`$NEOCHUMBY_TREE`, else
`$NEOCHUMBY_CLASSIC_TREE`) whose `apps/<name>/manifest` has a `title` and an `exec`, except
`classic` and `home`. Players (`player = true`: apps that play music) come first, with a
note badge; the Chumby's player (its `player=` setting from neochumbyd's `paired` list, else `somafm` if
the tree has it, else the first by name) has a star. A tile shows the app's `icon.png` (from its `files/`), or its initial on
its `colour`, and its title in that colour. A tap on it is a hot zone with the engine's action
`app <name>`, which the engine sends to the session as `open <name>`. The list is read at
every render, so a new app shows at the next one.

For neochumby's emulator CI (`dev/emulate`'s classic scenario, johnmerchant/neochumby#28):
this screen replaces the `app doom` hot zone that scenario injects into Settings. Tap Home's
APPS (`hot home ... screen apps_p0`), then the DOOM tile (`hot apps_p0 ... app doom`).

Settings' WEB PAGE button (formerly APPS) still opens the QR code for the web page
(`configure`).

## Facts from the Chumby

The Chumby reads most of what Settings → Network and Chumby Info show itself, and draws it
live (the engine's live values, [widgets.md](widgets.md#live-values)): its name, firmware,
uptime, free memory and USB space, the network's name, its IP address and the signal (the
bars on Home and Network, and the percentage). The rest it can't read, and the companion
can supply: if it learns them (from the Chumby's session), it can write them to
`$NEOCHUMBY_CLASSIC_STATE/info/<chumby>` (atomically: a temp file, then rename), as
`key=value` lines, UTF-8, one per line. They show at the next render. A key that is
missing, empty or malformed shows "?" (so does every row without the file); keys not in
this table are ignored, those the Chumby reads itself included. Values are cut to 80
characters, and control characters are dropped.

| Key | Format (example) | Shown as |
| --- | --- | --- |
| `signal` | signal level in dBm, an integer (`-61`) | Network → Signal, after the live bars and percentage (`-61 dBm`) |
| `ap` | the access point's MAC (`aa:bb:cc:dd:ee:ff`) | Network → Access point |
| `gateway` | the default route's gateway (`192.0.2.1`) | Network → Gateway |
| `dns` | name servers, comma-separated (`192.0.2.1, 192.0.2.2`) | Network → DNS |
| `mac` | the WiFi interface's MAC | Network → MAC address |
| `rate` | the link's bit rate, as text (`54 Mb/s`) | Network → Link rate |
| `kernel` | `uname -r` (`2.6.28-chumby`) | Chumby Info → Kernel, as `Linux 2.6.28-chumby` |
| `engine` | the engine's version or build, free text (`neochumby 0.4.0`) | Chumby Info → Engine |
| `asof` | when the Chumby reported these, free text (`7:30 PM, 28 Oct`) | the line under both screens: `Live from the Chumby; the rest as it reported at ...` |

Chumby Info's last row, Feed, comes from the widgets (the weather's observation time),
not from this file. Don't write values that change every minute: each change re-renders
both screens, and the Chumby fetches them again. Update `asof` only when you write the
file.

`refresh/` and `info/` are mode `2770` in neochumbyd's group, so the companion can write
there.

## Which Chumbys

The renderer renders every Chumby neochumbyd lists as paired (`paired` on its admin socket,
asked every 30 s), or the names in `NEOCHUMBY_CLASSIC_CHUMBYS` if that is set. Each Chumby
plays its own channel if `config.json` assigns one (`chumbys`), else the default (the web page
has only the default channel for now).
