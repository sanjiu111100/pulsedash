"""Chart primitives rendered with Pillow only.

No plotting library is used: every axis, grid line and label here is drawn
explicitly, which keeps the dependency footprint tiny and makes the output
fully deterministic (same data in, same pixels out).
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PIL import ImageDraw, ImageFont

# --------------------------------------------------------------------------- theme
BG = (15, 23, 42)
PANEL = (30, 41, 59)
PANEL_EDGE = (51, 65, 85)
GRID = (45, 58, 80)
TEXT = (226, 232, 240)
MUTED = (148, 163, 184)
ACCENT = (56, 189, 248)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
VIOLET = (167, 139, 250)

# CJK-capable faces first: a Latin-only face would render Chinese as tofu boxes.
FONT_REGULAR = [
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("C:/Windows/Fonts/segoeui.ttf", 0),
]
FONT_BOLD = [
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
]

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a CJK-capable TrueType font, cached per (size, weight)."""
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    order = FONT_BOLD if bold else FONT_REGULAR
    for path, index in order:
        if Path(path).exists():
            try:
                loaded = ImageFont.truetype(path, size, index=index)
                _font_cache[key] = loaded
                return loaded
            except OSError:
                continue
    fallback = ImageFont.load_default()
    _font_cache[key] = fallback
    return fallback


def text(draw: ImageDraw.ImageDraw, xy, value: str, size: int = 16,
         color=TEXT, anchor: str = "la", bold: bool = False) -> None:
    draw.text(xy, value, font=font(size, bold), fill=color, anchor=anchor)


# --------------------------------------------------------------------------- shapes
def panel(draw: ImageDraw.ImageDraw, box, radius: int = 14,
          fill=PANEL, outline=PANEL_EDGE) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


def kpi_card(draw: ImageDraw.ImageDraw, box, label: str, value: str,
             delta: str = "", accent=ACCENT) -> None:
    panel(draw, box)
    x0, y0 = box[0], box[1]
    draw.rounded_rectangle((x0, y0 + 12, x0 + 4, box[3] - 12), radius=2, fill=accent)
    text(draw, (x0 + 22, y0 + 18), label, 15, MUTED)
    text(draw, (x0 + 22, y0 + 46), value, 32, TEXT, bold=True)
    if delta:
        text(draw, (x0 + 22, box[3] - 34), delta, 14, accent)


def _nice_step(span: float, target_lines: int = 4) -> float:
    """Pick a readable grid step for the given value span."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target_lines)
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step >= raw:
            return step
    return 10 * magnitude


def _fmt(value: float, decimals: int = 1) -> str:
    if abs(value) >= 1000:
        return "{:,.0f}".format(value)
    if decimals <= 0:
        return "{:.0f}".format(value)
    return ("{:.%df}" % decimals).format(value)


def timeseries(draw: ImageDraw.ImageDraw, box, series: list[tuple[int, float]],
               color=ACCENT, decimals: int = 1, x_ticks: int = 5,
               y_ticks: int = 4, suffix: str = "", show_x: bool = True,
               x_format: str = "%m-%d %H:%M") -> None:
    """Line chart of (epoch, value) pairs inside box = (x0, y0, x1, y1)."""
    if len(series) < 2:
        return
    x0, y0, x1, y1 = box
    left, right = x0 + 70, x1 - 16
    top, bottom = y0 + 14, y1 - (30 if show_x else 12)
    if right - left < 40 or bottom - top < 40:
        return

    values = [v for _, v in series]
    times = [t for t, _ in series]
    low, high = min(values), max(values)
    if high == low:
        high = low + 1.0
    padding = (high - low) * 0.12
    low, high = low - padding, high + padding
    step = _nice_step(high - low, y_ticks)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if high <= low:
        high = low + step

    def map_x(ts: int) -> float:
        if times[-1] == times[0]:
            return left
        return left + (ts - times[0]) / (times[-1] - times[0]) * (right - left)

    def map_y(value: float) -> float:
        return bottom - (value - low) / (high - low) * (bottom - top)

    ticks = int(round((high - low) / step))
    for index in range(ticks + 1):
        value = low + index * step
        y = map_y(value)
        draw.line((left, y, right, y), fill=GRID, width=1)
        digits = 0 if step >= 1 else decimals
        text(draw, (left - 12, y), _fmt(value, digits) + suffix, 12, MUTED, anchor="rm")

    if show_x:
        count = len(times)
        picks = sorted({0, count - 1} | {int(round(i * (count - 1) / max(1, x_ticks - 1)))
                                        for i in range(x_ticks)})
        for pick in picks:
            x = map_x(times[pick])
            label = datetime.fromtimestamp(times[pick]).strftime(x_format)
            anchor = "lm" if pick == 0 else ("rm" if pick == count - 1 else "ma")
            text(draw, (x, bottom + 10), label, 12, MUTED, anchor=anchor)

    points = [(map_x(t), map_y(v)) for t, v in series]
    draw.line(points, fill=color, width=2, joint="curve")

    last_x, last_y = points[-1]
    draw.ellipse((last_x - 3.5, last_y - 3.5, last_x + 3.5, last_y + 3.5), fill=color)
    draw.ellipse((last_x - 7, last_y - 7, last_x + 7, last_y + 7), outline=color, width=1)


def barchart(draw: ImageDraw.ImageDraw, box, series: list[tuple[int, float]],
             color=VIOLET, y_ticks: int = 4) -> None:
    """Vertical bars for a series of (epoch, value) pairs."""
    if not series:
        return
    x0, y0, x1, y1 = box
    left, right = x0 + 56, x1 - 14
    top, bottom = y0 + 14, y1 - 12
    if right - left < 30 or bottom - top < 30:
        return
    values = [v for _, v in series]
    high = max(values + [0.0])
    if high <= 0:
        high = 1.0
    step = _nice_step(high, y_ticks)
    high = math.ceil(high / step) * step
    if high <= 0:
        high = step

    for index in range(int(round(high / step)) + 1):
        value = index * step
        y = bottom - value / high * (bottom - top)
        draw.line((left, y, right, y), fill=GRID, width=1)
        text(draw, (left - 10, y), _fmt(value, 0), 12, MUTED, anchor="rm")

    slot = (right - left) / len(series)
    bar_width = max(2.0, min(14.0, slot * 0.62))
    for index, (_, value) in enumerate(series):
        centre = left + slot * (index + 0.5)
        height = max(0.0, value) / high * (bottom - top)
        if height < 1:
            continue
        draw.rounded_rectangle(
            (centre - bar_width / 2, bottom - height, centre + bar_width / 2, bottom),
            radius=min(3.0, bar_width / 2), fill=color)


def hbar_rows(draw: ImageDraw.ImageDraw, box, rows: list[tuple[str, float, str]],
              color=ACCENT, label_width: int = 210) -> None:
    """label / bar / value rows, used for ranking blocks."""
    x0, y0, x1, y1 = box
    if not rows:
        return
    row_height = (y1 - y0) / len(rows)
    max_value = max((value for _, value, _ in rows), default=1.0) or 1.0
    bar_left = x0 + label_width
    bar_right = x1 - 100
    for index, (label, value, suffix) in enumerate(rows):
        centre_y = y0 + row_height * (index + 0.5)
        text(draw, (x0, centre_y), label, 13, TEXT, anchor="lm")
        ratio = max(0.0, min(1.0, value / max_value))
        draw.rounded_rectangle((bar_left, centre_y - 7, bar_right, centre_y + 7),
                               radius=7, fill=GRID)
        width = (bar_right - bar_left) * ratio
        if width > 3:
            draw.rounded_rectangle((bar_left, centre_y - 7, bar_left + width, centre_y + 7),
                                   radius=7, fill=color)
        text(draw, (bar_right + 12, centre_y), suffix, 13, MUTED, anchor="lm")


def badge(draw: ImageDraw.ImageDraw, xy, label: str, ok: bool = True) -> int:
    """Pill-shaped status badge. Returns its rendered width."""
    color = GREEN if ok else RED
    x, y = xy
    width = draw.textlength(label, font=font(12)) + 24
    draw.rounded_rectangle((x, y, x + width, y + 22), radius=11,
                           fill=(color[0] // 6, color[1] // 6, color[2] // 6),
                           outline=color, width=1)
    draw.ellipse((x + 8, y + 8, x + 14, y + 14), fill=color)
    text(draw, (x + 20, y + 4), label, 12, color)
    return int(width)
