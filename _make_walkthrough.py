"""Assemble the Project Shredder walkthrough frames into an annotated GIF."""
import os
from PIL import Image, ImageDraw, ImageFont

FR = os.path.join(os.path.dirname(__file__), ".playwright-mcp", "frames")
OUT = os.path.join(os.path.dirname(__file__), "assets")
os.makedirs(OUT, exist_ok=True)

BG = (10, 10, 12)
ACCENT = (232, 255, 83)   # acid lime
MAG = (255, 46, 136)      # magenta
FG = (236, 236, 239)
DIM = (150, 150, 160)
W, H = 1280, 900


def font(sz, bold=True):
    for n in (("ariblk.ttf",) if bold else ("arial.ttf",)) + ("arialbd.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def load(name):
    p = os.path.join(FR, name)
    if not os.path.exists(p):
        return None
    im = Image.open(p).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    return im


def caption(im, title, sub, accent=ACCENT):
    """Bottom caption bar."""
    im = im.copy()
    d = ImageDraw.Draw(im, "RGBA")
    bar_h = 92
    d.rectangle((0, H - bar_h, W, H), fill=(10, 10, 12, 235))
    d.rectangle((0, H - bar_h, 6, H), fill=accent)
    d.text((22, H - bar_h + 14), title, font=font(26), fill=accent)
    d.text((22, H - bar_h + 52), sub, font=font(18, bold=False), fill=FG)
    return im


def title_card():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # scanline texture
    for y in range(0, H, 3):
        d.line((0, y, W, y), fill=(18, 18, 14))
    # icon
    ic = os.path.join(OUT, "shredder_icon.png")
    if os.path.exists(ic):
        i = Image.open(ic).convert("RGBA").resize((150, 150), Image.LANCZOS)
        im.paste(i, (W // 2 - 75, 250), i)
    d = ImageDraw.Draw(im)
    t = "PROJECT SHREDDER"
    tb = d.textbbox((0, 0), t, font=font(58))
    d.text(((W - (tb[2] - tb[0])) // 2, 430), t, font=font(58), fill=ACCENT)
    s = "what the draft room looks like when it's live"
    sb = d.textbbox((0, 0), s, font=font(24, bold=False))
    d.text(((W - (sb[2] - sb[0])) // 2, 505), s, font=font(24, bold=False), fill=DIM)
    s2 = "// pre-draft  ·  on the clock  ·  reading the room"
    s2b = d.textbbox((0, 0), s2, font=font(20, bold=False))
    d.text(((W - (s2b[2] - s2b[0])) // 2, 545), s2, font=font(20, bold=False), fill=MAG)
    return im


# frame -> (title, subtitle, accent, hold_seconds)
plan = [
    (None, "TITLE", None, MAG, 2.6),
    ("f06.png", "BEFORE YOUR PICK — Opponent Watch",
     "Auto-shows while the room drafts: who's on the clock, who they'll take (Prophecy), and SNIPE alerts — grab-now-to-deny.", MAG, 4.5),
    ("f07.png", "Opponent Watch — snipe list + predicted board",
     "Every rival's most-likely target and the pick where you could take him first. Your top-3 on-deck stays visible.", MAG, 3.8),
    ("f02.png", "YOU'RE ON THE CLOCK — My Pick",
     "Flips automatically at your turn. TAKE NOW hero pick, Wheel Play (grab-now-vs-wheel-back), villain narration.", ACCENT, 4.5),
    ("f03.png", "My Pick — Draft with Soul + Dark Horse",
     "The undervalued breakout the projections don't hype (Tee Higgins archetype), plus the gated last-pick Dark Horse.", ACCENT, 4.2),
    ("f04.png", "My Pick — the ranked board",
     "Best-available by the Edge Engine: VORP, tier, ADP value, survival %, injury chips. Draft or mark 'gone' per player.", ACCENT, 4.0),
    ("f05.png", "My Pick — full recommendation list",
     "Everything re-ranks live as picks come in. This is your command center every time the snake returns to you.", ACCENT, 3.8),
]

frames = []
FPS = 10
for item in plan:
    name, title, sub, accent, hold = item
    if title == "TITLE":
        im = title_card()
    else:
        base = load(name)
        if base is None:
            continue
        im = caption(base, title, sub, accent)
    for _ in range(int(hold * FPS)):
        frames.append(im)

if not frames:
    raise SystemExit("no frames assembled")

gif_path = os.path.join(OUT, "shredder_walkthrough.gif")
# downscale for a reasonable GIF size
scaled = [f.resize((854, 600), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=128)
          for f in frames]
scaled[0].save(gif_path, save_all=True, append_images=scaled[1:],
               duration=int(1000 / FPS), loop=0, optimize=True)
print("GIF", gif_path, os.path.getsize(gif_path) // 1024, "KB")
