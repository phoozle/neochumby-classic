"""whyallanews: The Whyalla News (whyallanewsonline.com.au, the local paper) as a widget.

Source: the site's WordPress RSS feed, https://whyallanewsonline.com.au/feed/ (the
homepage answers scripts with 403; we don't try to get round that). Each item carries a
title, link, date, author (dc:creator), categories (News / Sport / In Business ...), a
300x225 photo in the description, and the text in content:encoded.

The paywall is respected: most stories are MemberPress-protected, and the feed then holds
only a short excerpt (div.mepr-unauthorized-excerpt) followed by a "Subscribe or Login"
message and a login form. We show the excerpt and end it with "Continue at
whyallanewsonline.com.au". Full text is shown only when the feed itself carries it
(syndicated "In Business" pieces). Forms, scripts and "The post ... appeared first on ..."
are dropped. Feed HTML is untrusted: it is parsed to plain text with html.parser and only
ever drawn as pixels; names come from slot numbers.

Files (stable slot names, written only when the pixels change, no per-run times). The
prefix is "wn_", not the widget's id: the engine's screen names are at most 23
characters, and "whyallanews_s11_p2_night" would be 24.
  wn_slide<k>[_night]       slideshow page k: masthead + 3 headlines with photos
  wn_l<k>[_night]           list page k: 4 rows of photo, headline, category and date
  wn_s<i>_p<j>[_night]      story i, page j: photo, headline, byline + date, text

The masthead's wordmark is the paper's, so it is not bundled: without
whyallanews-wordmark.png in the asset directories (assets.py) the masthead shows the
paper's name in a bundled font instead.

  python -m neochumby_classic.widgets.whyallanews icon          # redraw icons/whyallanews.png
  python -m neochumby_classic.widgets.whyallanews wordmark DIR  # typeset the wordmark into DIR
                                                                # (macOS: needs its Superclarendon)
"""
import datetime as dt
import email.utils
import html
import io
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from .. import assets, config
from . import news                            # shared machinery: fetching, wrapping, buttons, writes
from .news import W, H, Rendered, font, wrap, ellipsize, crop_to, grad, button, _tri, _put, clean
from ..widget import Rendered as Rendered1

FEED = "https://whyallanewsonline.com.au/feed/"
SITE = "whyallanewsonline.com.au"
TZ = ZoneInfo(config.DEFAULT_TZ)              # render() sets the configured one
SERIF = news.HEAD                             # headlines: bold sans, as on the site (name kept from v1)
SANS, SANSB = news.BODY, news.BODYB
WORDMARK = "whyallanews-wordmark.png"         # an owner's asset (assets.py): alpha mask, recoloured per theme
IMAGE_TTL = 86400
MAX_PHOTO_PIXELS = 4_000_000
SLIDE_ROWS = 3
LIST_ROWS = 4
MAX_STORY_PAGES = 3                           # only full-text stories ever need more than one

API = 1
APP = {"id": "whyallanews", "name": "Whyalla News", "icon": "icons/whyallanews.png", "seconds": 12,
       "description": "Headlines and extracts from The Whyalla News, the local paper. "
                      "Tap a slide to read; subscriber-only stories show their free extract.",
       "subslides": []}
CONFIG_SCHEMA = [
    {"key": "stories", "type": "number", "label": "Stories", "default": 8, "min": 3, "max": 12, "integer": True,
     "help": "How many of the latest stories the list shows when you tap in."},
    {"key": "slides", "type": "number", "label": "Headline slides", "default": 2, "min": 1, "max": 3,
     "integer": True, "help": "Each slide shows three headlines."},
    {"key": "category", "type": "choice", "label": "Show", "default": "all",
     "choices": [["all", "All stories"], ["News", "News"], ["Sport", "Sport"], ["In Business", "In Business"]]},
]


def page_count(subslide, opts):
    stories = int(opts.get("stories", 8))
    return max(1, min(int(opts.get("slides", 2)), math.ceil(stories / SLIDE_ROWS)))


# ---------------------------------------------------------------- look: the paper's website
# White page, a red wordmark, black bold sans headlines, grey dates and excerpts (darker
# than the site's, for the washed-out LCD), red rules.
RED = (217, 58, 43)
LIGHT = dict(paper=(255, 255, 255), ink=(17, 17, 17), sub=(68, 68, 68), red=RED, mark=RED, shadow=(214, 214, 214),
             sep=(222, 222, 222), none=(236, 236, 236), edge=(200, 200, 200), strip=(255, 255, 255), strip_text=(17, 17, 17),
             # for news.button / footers
             foot=((248, 248, 248), (236, 236, 236)), foot_line=(214, 214, 214), btn=((232, 82, 66), (196, 44, 32)),
             btn_off=((230, 230, 230), (214, 214, 214)), btn_ink=(255, 255, 255), label=(68, 68, 68))
NIGHT = dict(paper=(18, 18, 20), ink=(245, 245, 245), sub=(196, 196, 196), red=(244, 88, 72), mark=(240, 76, 60),
             shadow=(52, 52, 56), sep=(52, 52, 56), none=(40, 40, 44), edge=(70, 70, 74), strip=(18, 18, 20),
             strip_text=(245, 245, 245), foot=((30, 30, 33), (22, 22, 25)), foot_line=(56, 56, 60),
             btn=((190, 56, 44), (140, 32, 24)), btn_off=((44, 44, 48), (36, 36, 40)), btn_ink=(250, 240, 236),
             label=(196, 196, 196))
THEMES = [("", LIGHT), ("_night", NIGHT)]
_mark = {}


def wordmark_mask():
    """The wordmark as an alpha mask: the owner's whyallanews-wordmark.png if there is one,
    else the paper's name typeset in a bundled font (our placeholder)."""
    path = assets.find(WORDMARK)
    if path:
        return Image.open(path).getchannel("A")
    f = font(news.HEAD, 120)
    box = f.getbbox("WHYALLA NEWS")
    im = Image.new("L", (box[2] - box[0] + 8, box[3] - box[1] + 8), 0)
    ImageDraw.Draw(im).text((4 - box[0], 4 - box[1]), "WHYALLA NEWS", font=f, fill=255)
    return im


def masthead(img, T, h, mark_h, right=None):
    """The site's header: a white bar, the red wordmark centred, a thin grey shadow under it."""
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, h], fill=T["paper"])
    key = (mark_h, T["mark"])
    if key not in _mark:
        a = wordmark_mask()
        a = a.resize((round(a.width * mark_h / a.height), mark_h), Image.LANCZOS)
        _mark[key] = (Image.new("RGB", a.size, T["mark"]), a)
    col, a = _mark[key]
    img.paste(col, ((W - a.width) // 2, (h - 2 - a.height) // 2), a)
    if right:
        d.text((W - 8, h / 2), right, font=font(SANSB, 12), fill=T["sub"], anchor="rm")
    d.line([(0, h - 2), (W, h - 2)], fill=T["shadow"], width=2)


def make_wordmark(out_dir="."):
    """Typeset WHYALLA NEWS once, as an alpha mask PNG, in macOS's Superclarendon Bold, which
    matches the paper's Clarendon-style wordmark. That font is not redistributable, and the
    result is the paper's branding: keep it in your asset directory, never in this repository."""
    from PIL import ImageFont
    f = ImageFont.truetype("/System/Library/Fonts/Supplemental/SuperClarendon.ttc", 120, index=5)
    box = f.getbbox("WHYALLA NEWS")
    im = Image.new("RGBA", (box[2] - box[0] + 8, box[3] - box[1] + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - box[0], 4 - box[1]), "WHYALLA NEWS", font=f, fill=RED + (255,))
    path = os.path.join(out_dir, WORDMARK)
    im.save(path, optimize=True)
    print("wrote", path, im.size)


def date_str(pub):
    """The site's date style, 28/10/2026: stable, so it never forces a rewrite."""
    return pub.astimezone(TZ).strftime("%d/%m/%Y") if pub else ""


# ---------------------------------------------------------------- feed parsing (untrusted HTML -> text)
class _Text(HTMLParser):
    """Paragraph texts from HTML: p / h1-h4 / li / blockquote, never script, style or forms."""
    SKIP = {"script", "style", "form", "noscript", "button", "label", "input", "select", "textarea", "svg"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "li", "blockquote", "div"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.buf, self.skip, self.kind = [], [], 0, "p"

    def _flush(self):
        t = clean("".join(self.buf))
        if t:
            self.paras.append((self.kind, t))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self._flush()
            self.kind = "h" if tag in ("h1", "h2", "h3", "h4", "h5") else "p"
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self._flush()
            self.kind = "p"

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_paras(fragment):
    p = _Text()
    try:
        p.feed(fragment or "")
        p.close()
    except Exception:
        pass
    return p.paras


def extract(content):
    """(paras, paywalled) from content:encoded."""
    content = content or ""
    if "mepr-unauthorized" in content:
        m = re.search(r'<div class="mepr-unauthorized-excerpt">(.*?)</div>', content, re.S)
        paras = html_paras(m.group(1)) if m else []
        out = []
        for kind, t in paras:
            t = re.sub(r"\s*\[(…|\.\.\.|&hellip;)\]\s*$", "…", t)
            out.append((kind, t))
        return out, True
    paras = [(k, t) for k, t in html_paras(content)
             if not (t.startswith("The post ") and "appeared first on" in t)
             and not re.search(r"(Subscribe|Login) .*to see the rest", t)]
    return paras, False


def parse_feed(xml, limit, category="all"):
    ns = {"content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/"}
    ch = ET.fromstring(xml).find("channel")
    out = []
    for it in ch.findall("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith(f"https://{SITE}/"):
            continue
        cats = [clean(c.text) for c in it.findall("category") if c.text]
        if category != "all" and category not in cats:
            continue
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            pub = None
        m = re.search(r'<img[^>]+src="(https://' + re.escape(SITE) + r'/wp-content/uploads/[^"]+)"',
                      it.findtext("description") or "")
        paras, paywalled = extract(it.findtext("content:encoded", namespaces=ns))
        if not paras:                                    # no excerpt at all: the description, as text
            paras = [(k, t) for k, t in html_paras(it.findtext("description"))
                     if not (t.startswith("The post ") and "appeared first on" in t)]
            paywalled = True
        out.append({"title": title, "link": link, "pub": pub, "cat": cats[0] if cats else "",
                    "by": clean(it.findtext("dc:creator", namespaces=ns)), "img": html.unescape(m.group(1)) if m else None,
                    "paras": paras, "paywalled": paywalled, "photo": None})
        if len(out) >= limit:
            break
    return out


def gather(cache_dir, limit, category):
    """The feed (fetched every render) and its photos (cached a day). Never raises."""
    f = news.Fetcher(cache_dir)
    try:
        items = parse_feed(f.get(FEED), limit, category)
        if len(items) < limit and category != "all":    # a filter can leave too few: one more page
            more = parse_feed(f.get(FEED + "?paged=2"), limit - len(items), category)
            seen = {i["link"] for i in items}
            items += [i for i in more if i["link"] not in seen]
    except Exception as e:
        print(f"whyallanews: feed unavailable: {e}", file=sys.stderr)
        return {"ok": False, "items": [], "fetches": f.count}
    for it in items:
        if not it["img"]:
            continue
        try:
            im = Image.open(io.BytesIO(f.get(it["img"], IMAGE_TTL)))
            if im.width * im.height > MAX_PHOTO_PIXELS:
                raise ValueError("too big")
            it["photo"] = im.convert("RGB")
        except Exception as e:
            print(f"whyallanews: photo failed {it['img']}: {e}", file=sys.stderr)
    return {"ok": bool(items), "items": items, "fetches": f.count}


def _cache_dir(out):
    try:
        d = os.path.join(config.state_dir(), "whyallanews-cache")
        os.makedirs(d, exist_ok=True)
        if os.access(d, os.W_OK):
            return d
    except Exception:
        pass
    return os.path.join(out, ".wncache")


# ---------------------------------------------------------------- drawing
def photo_box(img, T, it, x, y, w, h):
    d = ImageDraw.Draw(img)
    if it.get("photo") is not None:
        img.paste(crop_to(it["photo"], w, h), (x, y))
    else:
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=T["none"])
        d.text((x + w / 2, y + h / 2), "WN", font=font(SANSB, max(12, h // 3)), fill=T["edge"], anchor="mm")


def cat_label(it):
    return it.get("cat") or "News"


def section(img, T, y, label, right=None):
    """The site's section heading: bold black sans with a 3 px red rule under it."""
    d = ImageDraw.Draw(img)
    d.text((10, y), label, font=font(SANSB, 15), fill=T["ink"])
    if right:
        d.text((W - 10, y + 2), right, font=font(SANSB, 12), fill=T["sub"], anchor="ra")
    d.rectangle([10, y + 21, W - 10, y + 23], fill=T["red"])


M_H, MARK_H = 34, 18                            # masthead bar, wordmark cap height
S_TOP, S_FOOT = M_H + 28, 216                   # slide rows between the section rule and the strip
S_ROW = (S_FOOT - S_TOP) // SLIDE_ROWS          # 50
TW, TH = 64, 46


def slide_image(items, ok, k, n, T):
    img = Image.new("RGB", (W, H), T["paper"])
    masthead(img, T, M_H, MARK_H)
    section(img, T, M_H + 4, "Latest news", f"{k + 1} of {n}" if n > 1 else None)
    d = ImageDraw.Draw(img)
    rows = items[k * SLIDE_ROWS:(k + 1) * SLIDE_ROWS]
    if not ok or not rows:
        d.text((W / 2, 120), "Whyalla News is unavailable", font=font(SANSB, 15), fill=T["ink"], anchor="mm")
        d.text((W / 2, 146), "It will try again soon.", font=font(SANSB, 13), fill=T["sub"], anchor="mm")
    hf, mf = font(SERIF, 16), font(SANS, 12)
    tx = 10 + TW + 10
    tw = W - tx - 10
    for r, it in enumerate(rows):
        y = S_TOP + r * S_ROW
        photo_box(img, T, it, 10, y + (S_ROW - TH) // 2, TW, TH)
        hl = ellipsize(wrap(it["title"], hf, tw), 2, hf, tw)
        block = len(hl) * 18 + 13
        yy = y + (S_ROW - block) / 2 - 1
        for j, ln in enumerate(hl):
            d.text((tx, yy + j * 18), ln, font=hf, fill=T["ink"])
        cat = cat_label(it).upper()
        d.text((tx, yy + len(hl) * 18 + 1), cat, font=font(SANSB, 11), fill=T["red"])
        d.text((tx + font(SANSB, 11).getlength(cat) + 8, yy + len(hl) * 18), date_str(it.get("pub")), font=mf, fill=T["sub"])
    d.line([(0, S_FOOT), (W, S_FOOT)], fill=T["shadow"], width=1)
    d.text((10, (S_FOOT + H) / 2), SITE, font=font(SANS, 12), fill=T["sub"], anchor="lm")
    if ok and rows:
        d.text((W - 24, (S_FOOT + H) / 2), "Tap to read", font=font(SANSB, 13), fill=T["red"], anchor="rm")
        _tri(img, W - 13, (S_FOOT + H) / 2, 5, "right", T["red"])
    return img


HDR, FOOT = M_H, 204
ROW = (FOOT - HDR) // LIST_ROWS                 # 42


def footer(img, T):
    grad(img, (0, FOOT, W, H), *T["foot"])
    ImageDraw.Draw(img).line([(0, FOOT), (W, FOOT)], fill=T["foot_line"])


def list_image(items, ok, k, npages, T, now):
    img = Image.new("RGB", (W, H), T["paper"])
    masthead(img, T, M_H, MARK_H)
    d = ImageDraw.Draw(img)
    rows = items[k * LIST_ROWS:(k + 1) * LIST_ROWS]
    if not ok:
        d.text((W / 2, 110), "Whyalla News is unavailable", font=font(SANSB, 15), fill=T["ink"], anchor="mm")
        d.text((W / 2, 134), "It will try again soon.", font=font(SANSB, 13), fill=T["sub"], anchor="mm")
    hf, mf, cf = font(SERIF, 15), font(SANS, 12), font(SANSB, 11)
    tx, pw, ph = 74, 56, 38
    for r, it in enumerate(rows):
        y = HDR + r * ROW
        if r:
            d.line([(10, y), (W - 10, y)], fill=T["sep"])
        photo_box(img, T, it, 10, y + (ROW - ph) // 2, pw, ph)
        tw = W - tx - 8
        d.text((tx, y + 5), ellipsize(wrap(it["title"], hf, tw), 1, hf, tw)[0], font=hf, fill=T["ink"])
        cat = cat_label(it).upper()
        d.text((tx, y + 25), cat, font=cf, fill=T["red"])
        stamp = date_str(it.get("pub")) + ("  · extract" if it["paywalled"] else "")
        d.text((tx + cf.getlength(cat) + 8, y + 24), stamp, font=mf, fill=T["sub"])
    footer(img, T)
    button(img, (6, FOOT + 5, 52, H - 5), None, T, enabled=k > 0, arrow="up")
    button(img, (58, FOOT + 5, 104, H - 5), None, T, enabled=k < npages - 1, arrow="down")
    if items:
        a, b = k * LIST_ROWS + 1, min(len(items), (k + 1) * LIST_ROWS)
        d.text((114, (FOOT + H) / 2), f"{a}–{b} of {len(items)}", font=font(SANSB, 12), fill=T["label"], anchor="lm")
    button(img, (246, FOOT + 6, 314, H - 6), "DONE", T, fnt=font(SANSB, 12))
    return img


# story layout
SX0, SX1 = 10, 310
SPW, SPH = 96, 72
BODY_F, BODY_L = 12, 15


def layout_story(it, now, max_pages):
    """Pages of draw ops for a story (photo, headline, byline, date, text)."""
    hf, bf, hb = font(SERIF, 15), font(SANS, BODY_F), font(SANSB, BODY_F)
    top, bottom = HDR + 6, FOOT - 4
    ops, pages = [], []
    hx = SX0 + SPW + 8
    hw = SX1 - hx
    ops.append(("photo", SX0, top))
    y = top - 1
    for ln in ellipsize(wrap(it["title"], hf, hw), 3, hf, hw):
        ops.append(("title", hx, y, ln))
        y += 18
    y += 2
    ops.append(("cat", hx, y, cat_label(it).upper() + ("  ·  SUBSCRIBERS" if it["paywalled"] else "")))
    y += 15
    for m in [f"By {it['by']}" if it.get("by") else "", date_str(it.get("pub"))]:
        if m and y + 14 <= top + SPH + 16:
            ops.append(("meta", hx, y, ellipsize(wrap(m, font(SANS, 12), hw), 1, font(SANS, 12), hw)[0]))
            y += 15
    y = max(y, top + SPH) + 8
    flow = []
    for kind, text in it["paras"]:
        f = hb if kind == "h" else bf
        flow += [(kind, ln) for ln in wrap(text, f, SX1 - SX0)]
        flow.append(("gap", ""))
    while flow and flow[-1][0] == "gap":
        flow.pop()
    if it["paywalled"]:
        flow += [("gap", ""), ("more", "Continue at " + SITE)]
    cut = False
    for kind, text in flow:
        if kind == "gap":
            y += 5
            continue
        if y + BODY_L > bottom:
            pages.append(ops)
            if len(pages) >= max_pages:
                cut = True
                break
            ops, y = [], top
        ops.append((kind, SX0, y, text))
        y += BODY_L
    else:
        pages.append(ops)
    if cut:                                          # full text longer than the page budget
        last = pages[-1]
        while last and last[-1][0] in ("p", "h", "more") and last[-1][2] + BODY_L > bottom - BODY_L:
            last.pop()
        last.append(("more", SX0, bottom - BODY_L, "The rest at " + SITE))
    return pages


def story_image(ops, it, T, j, n):
    img = Image.new("RGB", (W, H), T["paper"])
    masthead(img, T, M_H, MARK_H, f"{j + 1}/{n}" if n > 1 else None)
    d = ImageDraw.Draw(img)
    fonts = {"cat": font(SANSB, 11), "title": font(SERIF, 15), "meta": font(SANS, 12), "p": font(SANS, BODY_F),
             "h": font(SANSB, BODY_F), "more": font(SANSB, 12)}
    cols = {"cat": T["red"], "title": T["ink"], "meta": T["sub"], "p": T["sub"], "h": T["ink"], "more": T["red"]}
    for op in ops:
        if op[0] == "photo":
            photo_box(img, T, it, op[1], op[2], SPW, SPH)
        else:
            kind, x, y, text = op
            d.text((x, y), text, font=fonts[kind], fill=cols[kind])
    footer(img, T)
    f12 = font(SANSB, 12)
    button(img, (6, FOOT + 5, 94, H - 5), "  PREV", T, fnt=f12)
    _tri(img, 20, (FOOT + H) / 2, 5, "left", T["btn_ink"])
    button(img, (116, FOOT + 5, 204, H - 5), "LIST", T, fnt=f12)
    button(img, (W - 94, FOOT + 5, W - 6, H - 5), "NEXT  ", T, fnt=f12)
    _tri(img, W - 20, (FOOT + H) / 2, 5, "right", T["btn_ink"])
    return img


# ---------------------------------------------------------------- render
def render_slides(out, first_page, rgb565, opts=None, slides=None, seconds=12, now=None, cache_dir=None,
                  night_files=False, screens_too=True):
    """Slideshow pages (each 'pagetap'-ing its list page) plus the list and story screens.
    slides: [(None, pages)] as config.plan() gives an app without sub-slides.
    Returns Rendered(lines, titles, thumbs). Never raises."""
    now = now or dt.datetime.now(TZ)
    opts, _ = config.validate(CONFIG_SCHEMA, opts or {})
    npages = sum(n for _sid, n in slides) if slides else page_count(None, opts)
    try:
        data = gather(cache_dir or _cache_dir(out), int(opts["stories"]), opts["category"])
    except Exception as e:                            # belt and braces: gather already catches
        print(f"whyallanews: {e}", file=sys.stderr)
        data = {"ok": False, "items": []}
    items, ok = data["items"], data["ok"]
    lines, titles, thumbs = [], [], []
    try:
        lines += _screens(out, rgb565, items, ok, now) if screens_too else []
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"whyallanews: reader failed: {e}", file=sys.stderr)
        lines = _screens(out, rgb565, [], False, now) if screens_too else []
    nshow = max(1, min(npages, math.ceil(len(items) / SLIDE_ROWS))) if ok else 1
    for k in range(nshow):
        p = first_page + k
        name = f"wn_slide{k}"
        for sfx, T in THEMES:
            if sfx and not night_files:
                continue
            try:
                img = slide_image(items, ok, k, nshow, T)
            except Exception as e:
                print(f"whyallanews: slide failed: {e}", file=sys.stderr)
                img = slide_image([], False, k, nshow, T)
            _put(rgb565, img, out, name + sfx)
            if sfx:
                lines.append(f"pagenight {p} {name}_night.raw")
            else:
                lines.append(f"page {p} {name}.raw {seconds} noclock noticker")
                thumbs.append(img)
                titles.append("Whyalla News")
        if screens_too:
            lk = (k * SLIDE_ROWS) // LIST_ROWS if ok else 0
            lines.append(f"pagetap {p} wn_l{lk}")
    # keep page lines before their pagenight/pagetap lines and ahead of the screens
    order = {"page": 0, "pagenight": 1, "pagetap": 2}
    pg = [ln for ln in lines if ln.split()[0] in order]
    pg.sort(key=lambda ln: (int(ln.split()[1]), order[ln.split()[0]]))
    rest = [ln for ln in lines if ln.split()[0] not in order]
    return Rendered(pg + rest, titles, thumbs)


def _screens(out, rgb565, items, ok, now):
    lines = []
    nl = max(1, math.ceil(len(items) / LIST_ROWS))
    layouts = [layout_story(it, now, MAX_STORY_PAGES) for it in items]
    for sfx, T in THEMES:
        for k in range(nl):
            name = f"wn_l{k}{sfx}"
            _put(rgb565, list_image(items, ok, k, nl, T, now), out, name)
            lines.append(f"screen {name} {name}.raw")
            for r in range(LIST_ROWS):
                i = k * LIST_ROWS + r
                if i < len(items):
                    lines.append(f"hot {name} 0 {HDR + r * ROW} {W} {ROW} screen wn_s{i}_p0")
            if k > 0:
                lines.append(f"hot {name} 0 {FOOT} 55 {H - FOOT} screen wn_l{k - 1}")
            if k < nl - 1:
                lines.append(f"hot {name} 55 {FOOT} 55 {H - FOOT} screen wn_l{k + 1}")
            lines.append(f"hot {name} 236 {FOOT} 84 {H - FOOT} slideshow")
        for i, (it, pages) in enumerate(zip(items, layouts)):
            lst = f"wn_l{i // LIST_ROWS}"
            for j, ops in enumerate(pages):
                name = f"wn_s{i}_p{j}{sfx}"
                _put(rgb565, story_image(ops, it, T, j, len(pages)), out, name)
                prev = f"wn_s{i}_p{j - 1}" if j else lst
                nxt = f"wn_s{i}_p{j + 1}" if j < len(pages) - 1 else (f"wn_s{i + 1}_p0" if i + 1 < len(items) else lst)
                lines += [f"screen {name} {name}.raw",
                          f"hot {name} 0 {FOOT} 110 {H - FOOT} screen {prev}",
                          f"hot {name} {W - 110} {FOOT} 110 {H - FOOT} screen {nxt}",
                          f"hot {name} 110 {FOOT} 100 {H - FOOT} screen {lst}"]
    return lines


# ---------------------------------------------------------------- widget API 1
def render(ctx):
    """API 1 (docs/widgets.md): the headline slides that fit, and the list and story screens."""
    global TZ
    TZ = ctx.tz
    if not ctx.slides:                                # none of its pages fit: nothing leads to its screens
        return Rendered1()
    from ..output import rgb565
    cache = os.path.join(ctx.state_dir, "cache") if ctx.state_dir else None
    r = render_slides(ctx.out, ctx.first_page, rgb565, ctx.options, ctx.slides, ctx.seconds, now=ctx.now,
                      cache_dir=cache, night_files=True)
    return Rendered1(list(r.lines), list(r.titles), list(r.thumbs))


# ---------------------------------------------------------------- icon
def make_icon():
    """80x80 web page icon: a folded front page with the initials WN (no publisher's logo)."""
    S, K = 80, 4
    Z = S * K
    im = Image.new("RGB", (Z, Z), (178, 24, 34))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([Z * .14, Z * .12, Z * .86, Z * .9], radius=Z * .05, fill=(252, 250, 244))
    d.text((Z * .5, Z * .3), "WN", font=font(SERIF, int(Z * .24)), fill=(16, 16, 18), anchor="mm")
    d.line([(Z * .2, Z * .46), (Z * .8, Z * .46)], fill=(16, 16, 18), width=K * 2)
    d.line([(Z * .2, Z * .5), (Z * .8, Z * .5)], fill=(16, 16, 18), width=K)
    d.rectangle([Z * .2, Z * .56, Z * .46, Z * .78], fill=(150, 146, 136))
    for k in range(4):
        y = Z * (.58 + k * .06)
        d.line([(Z * .52, y), (Z * .8, y)], fill=(90, 90, 96), width=K * 2)
    im = im.resize((S, S), Image.LANCZOS)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "whyallanews.png")
    im.save(path, optimize=True)
    print("wrote", path)


if __name__ == "__main__":
    if sys.argv[1:] == ["icon"]:
        make_icon()
    elif sys.argv[1:2] == ["wordmark"] and len(sys.argv) in (2, 3):
        make_wordmark(*sys.argv[2:])
    else:
        print(__doc__)
