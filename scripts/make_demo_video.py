"""Render the routekit terminal animation to an MP4 and a GIF.

Frames are the real routekit output (summary panel + animated route map),
generated with the same 30 fps schedule the CLI uses, drawn as a macOS-style
terminal window. Braille dots are drawn as actual dots so they stay crisp.

    pip install pillow
    python scripts/make_demo_video.py examples/us_cities.csv --out ~/Desktop/routekit-demo

The MP4 is encoded with macOS AVFoundation (scripts/encode_mp4.swift), so it
needs macOS with Swift (Xcode Command Line Tools). The GIF only needs Pillow.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.style import Style  # noqa: E402
from rich.terminal_theme import MONOKAI  # noqa: E402
from rich.text import Text  # noqa: E402

from routekit.cli import FPS, animation_frames  # noqa: E402
from routekit.geocode import geocode_stops  # noqa: E402
from routekit.mapview import MapScene  # noqa: E402
from routekit.render import map_panel, summary_panel  # noqa: E402
from routekit.solver import distance_matrix, nearest_neighbor, route_length, two_opt  # noqa: E402
from routekit.stops import load_stops  # noqa: E402

# Terminal look
COLS = 100
FONT_SIZE = 15
CELL_W, CELL_H = 9, 19
PAD_X, PAD_Y = 16, 12
TITLE_H = 30
BG = (30, 30, 30)
FG = (215, 215, 215)
TITLE_BG = (46, 46, 46)
TITLE_FG = (190, 190, 190)
MENLO = "/System/Library/Fonts/Menlo.ttc"


def load_fonts():
    try:
        return ImageFont.truetype(MENLO, FONT_SIZE, index=0), ImageFont.truetype(MENLO, FONT_SIZE, index=1)
    except OSError:
        f = ImageFont.load_default()
        return f, f


USED_COLORS = set()


def style_rgb(style: Style):
    fg = FG
    if style.color is not None and not style.color.is_default:
        tc = style.color.get_truecolor(MONOKAI)
        fg = (tc.red, tc.green, tc.blue)
    if style.dim:
        fg = tuple(int(c * 0.55 + b * 0.45) for c, b in zip(fg, BG))
    USED_COLORS.add(fg)
    return fg


class TerminalRenderer:
    def __init__(self, rows: int, title: str):
        self.rows = rows
        self.width = PAD_X * 2 + COLS * CELL_W
        self.height = TITLE_H + PAD_Y * 2 + rows * CELL_H
        self.width += self.width % 2  # H.264 needs even dimensions
        self.height += self.height % 2
        self.font, self.bold = load_fonts()
        self.console = Console(width=COLS, force_terminal=True, color_system="truecolor",
                               file=open("/dev/null", "w"))
        self.chrome = self._chrome(title)

    def _chrome(self, title: str) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, self.width, TITLE_H], fill=TITLE_BG)
        for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx = 18 + i * 20
            d.ellipse([cx - 6, TITLE_H // 2 - 6, cx + 6, TITLE_H // 2 + 6], fill=color)
        tw = d.textlength(title, font=self.font)
        d.text(((self.width - tw) / 2, TITLE_H // 2 - FONT_SIZE // 2 - 1), title, fill=TITLE_FG, font=self.font)
        return img

    def render(self, renderable) -> Image.Image:
        img = self.chrome.copy()
        d = ImageDraw.Draw(img)
        lines = self.console.render_lines(renderable, self.console.options, pad=False)
        for r, line in enumerate(lines[: self.rows]):
            y = TITLE_H + PAD_Y + r * CELL_H
            c = 0
            for seg in line:
                style = seg.style or Style()
                rgb = style_rgb(style)
                font = self.bold if style.bold else self.font
                for ch in seg.text:
                    x = PAD_X + c * CELL_W
                    self._glyph(d, ch, x, y, rgb, font)
                    c += 1
        return img

    def _glyph(self, d: ImageDraw.ImageDraw, ch: str, x: int, y: int, rgb, font) -> None:
        o = ord(ch)
        if ch == " ":
            return
        if 0x2800 <= o <= 0x28FF:  # braille: draw the dots
            bits = o - 0x2800
            order = ((0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08),
                     (1, 1, 0x10), (1, 2, 0x20), (0, 3, 0x40), (1, 3, 0x80))
            for col, row, bit in order:
                if bits & bit:
                    cx = x + 2.5 + col * 4
                    cy = y + 3 + row * 4.3
                    d.ellipse([cx - 1.15, cy - 1.15, cx + 1.15, cy + 1.15], fill=rgb)
            return
        cx, cy = x + CELL_W / 2, y + CELL_H / 2
        if ch == "●":
            d.ellipse([cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5], fill=rgb)
        elif ch == "■":
            d.rectangle([cx - 4, cy - 4, cx + 4, cy + 4], fill=rgb)
        elif ch == "◆":
            d.polygon([(cx, cy - 6), (cx + 6, cy), (cx, cy + 6), (cx - 6, cy)], fill=rgb)
        else:
            d.text((x, y + 1), ch, fill=rgb, font=font)


def build_frames(csv: Path, command: str):
    stops = load_stops(csv)
    failed = geocode_stops(stops)
    located = [s for s in stops if s.located]
    if failed:
        print(f"note: skipped {len(failed)} address(es) that could not be geocoded", file=sys.stderr)
    d = distance_matrix([(s.lat, s.lon) for s in located])
    nn = nearest_neighbor(d)
    route = two_opt(nn, d)
    ordered = [located[i] for i in route]
    summary = summary_panel(len(located) - 1, route_length(nn, d), route_length(route, d), True, True, 45, 0)
    scene = MapScene(ordered, cols=COLS - 4)

    def prompt(typed: str, cursor: bool = True) -> Text:
        t = Text()
        t.append("~/routekit ", style="bold #4fb0ff").append("❯ ", style="bold #7ee787")
        t.append(typed)
        if cursor:
            t.append("▌", style="#aaaaaa")
        return t

    frames = []
    # 1. Type the command (~1.3 s), then a short beat.
    for i in range(0, len(command) + 1):
        frames.append(prompt(command[:i]))
    frames += [prompt(command)] * 8
    head = prompt(command, cursor=False)
    # 2. The real animation, frame for frame.
    for revealed in animation_frames(scene.total_dots, len(located)):
        frames.append(Group(head, summary, map_panel(scene, revealed)))
    # 3. Hold the finished map for 3 s.
    final = Group(head, summary, map_panel(scene))
    frames += [final] * (FPS * 3)
    rows = len(Console(width=COLS, file=open("/dev/null", "w")).render_lines(final, pad=False))
    return frames, rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="Output folder")
    ap.add_argument("--name", default="routekit-demo")
    ap.add_argument("--gif-scale", type=float, default=0.75, help="Scale factor for the GIF (smaller file)")
    args = ap.parse_args()

    command = f"routekit plan {args.csv.as_posix()}"
    frames, rows = build_frames(args.csv, command)
    renderer = TerminalRenderer(rows + 1, title=f"routekit — {args.csv.name}")
    args.out.mkdir(parents=True, exist_ok=True)

    images = []
    last_key, last_img = None, None
    with tempfile.TemporaryDirectory() as tmp:
        for i, fr in enumerate(frames):
            key = id(fr)
            img = last_img if key == last_key else renderer.render(fr)
            last_key, last_img = key, img
            img.save(Path(tmp) / f"{i:05d}.png")
            images.append(img)
        print(f"rendered {len(images)} frames at {FPS} fps ({len(images) / FPS:.1f} s), "
              f"{renderer.width}x{renderer.height}")

        mp4 = args.out / f"{args.name}.mp4"
        encoder = Path(__file__).with_name("encode_mp4.swift")
        if shutil.which("swift") and encoder.exists():
            subprocess.run(["swift", str(encoder), tmp, str(mp4), str(FPS)], check=True)
            print(f"wrote {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
        else:
            print("skipped MP4: needs macOS with Swift", file=sys.stderr)

    gif = args.out / f"{args.name}.gif"
    size = (int(renderer.width * args.gif_scale), int(renderer.height * args.gif_scale))
    # Build the palette from the final frame plus a swatch of every UI color, so small
    # accents (stops, depot, green savings) each get an exact palette entry.
    final = images[-1].resize(size, Image.LANCZOS)
    swatch_colors = sorted(USED_COLORS | {BG, FG, TITLE_BG, TITLE_FG,
                                          (255, 95, 86), (255, 189, 46), (39, 201, 63)})
    block = 24
    source = Image.new("RGB", (size[0], size[1] + block * (1 + len(swatch_colors) * block // size[0])), BG)
    source.paste(final, (0, 0))
    sd = ImageDraw.Draw(source)
    for i, rgb in enumerate(swatch_colors):
        x, y = (i * block) % size[0], size[1] + (i * block) // size[0] * block
        sd.rectangle([x, y, x + block - 1, y + block - 1], fill=rgb)
    palette = source.quantize(colors=128, method=Image.MEDIANCUT)
    gif_frames = [im.resize(size, Image.LANCZOS).quantize(palette=palette, dither=Image.NONE) for im in images]
    gif_frames[0].save(gif, save_all=True, append_images=gif_frames[1:], duration=int(1000 / FPS),
                       loop=0, optimize=True, disposal=1)
    print(f"wrote {gif} ({gif.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
