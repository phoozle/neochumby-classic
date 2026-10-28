"""assets: the files this package ships (fonts), and pictures it doesn't draw itself.

Only our own files are bundled: the open fonts (with their licences) and the app icons.
Logos and footage that belong to someone else are never committed, so each widget draws a
placeholder of its own when the file isn't there. To use your own copies on your own
server, put them in a directory and name it in NEOCHUMBY_CLASSIC_ASSETS (several,
separated by ':'). Those directories are searched first. The names the widgets look for:

  logo.png             the aurora screensaver's logo (RGBA, about 220 x 64)
  octopus.png          the small mark on Home, Settings and the leave dialog (RGBA)
  weather-badge.png    the weather pages' badge, top left (RGBA)
  raysclip.png         the aurora's light-ray clip (tools/raysclip.py makes it from a video)
  abc-logo.png, abc-swirls.png   the news header's logo and swirls (news.py make_assets)
  whyallanews-wordmark.png       Whyalla News' masthead wordmark (alpha mask, recoloured per theme)
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FONTS = os.path.join(DATA, "fonts") + os.sep          # the bundled open fonts (data/fonts/README.md)
DEFAULT_DIR = "/etc/neochumby-classic/assets"


def dirs():
    """Where owners' own pictures are looked for, first to last."""
    env = os.environ.get("NEOCHUMBY_CLASSIC_ASSETS")
    extra = [d for d in (env.split(os.pathsep) if env is not None else [DEFAULT_DIR]) if d]
    return extra + [DATA]


def find(name):
    """The path of asset `name`, or None if no asset directory has it."""
    if os.sep in name or name.startswith("."):
        return None
    for d in dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None
