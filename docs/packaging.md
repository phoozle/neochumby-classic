# Packaging: how this gets onto a server

Status: **(A) is what exists today. (A′) is proposed and waits on neochumby's maintainer.**
The repository is laid out so that either works without moving code.

## (A) A standalone Python package (today)

- `pyproject.toml` at the root; the package is in `files/python/neochumby_classic/`.
- `install.sh` makes a venv, installs the package, creates a user in neochumbyd's group and
  writes two systemd units (renderer, web page). `docker-compose.yml` is the alternative.
- neochumbyd is installed separately and knows nothing about this program. A Rust companion
  in neochumby reads the renders ([interface.md](interface.md)).

## (A′) Also a neochumby app crate (proposed)

The same repository would also be an app that neochumby's `tools/tree` installs from git:

```sh
tools/tree OUT git+https://github.com/phoozle/neochumby-classic#REV
```

- `Cargo.toml` at the root, with
  ```toml
  [package.metadata.neochumby]
  companion = "neochumby-classic-companion"
  daemon = "neochumby-classic-daemon"
  neochumbyd = "required"
  ```
- `files/` is copied into the app's directory in the tree, so the Python arrives as
  `<tree>/apps/classic/python/neochumby_classic/`.
- `neochumby-classic-daemon`: a thin Rust binary that checks `python3` (3.11+) and Pillow
  are present, then starts and restarts `python3 -m neochumby_classic.service` and
  `python3 -m neochumby_classic.web`, with `PYTHONPATH` set to the app's `python/`
  directory and `NEOCHUMBY_STATE` / `NEOCHUMBY_ADMIN` passed on as
  `NEOCHUMBY_CLASSIC_STATE` / `NEOCHUMBY_CLASSIC_ADMIN`.
- `neochumby-classic-companion`: the Rust companion that reads the renders, as in (A).

What already makes (A′) cheap:

- Every path comes from the environment or an argument: `NEOCHUMBY_CLASSIC_STATE`,
  `_OUT`, `_ADMIN` (or `NEOCHUMBY_ADMIN`), `_TREE` (or `NEOCHUMBY_TREE`), `_CONFIG`,
  `_ASSETS`, `_WEB`. Nothing assumes `/opt` or a venv.
- The package runs from a plain directory on `PYTHONPATH`. Without installed metadata,
  the built-in widgets are found through `widget.BUILTIN` (kept equal to
  `pyproject.toml`'s entry points by the smoke test).
- The renderer runs each render as `python -m neochumby_classic.render` with the same
  interpreter and environment, so it works the same from a tree.

Not written yet, on purpose: the Rust daemon and companion, until the maintainer agrees.
