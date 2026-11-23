"""Thread-safe matplotlib chart rendering for /graph and /juicegraph."""

from __future__ import annotations

import io
from collections import defaultdict
from typing import Callable, Sequence

import matplotlib.dates as mdates
from matplotlib import cm
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from config import EMBED_COLOR
from db.first_game import FirstEntry
from services.first_game import JuicePoint

_BG_COLOR = "#2b2d31"
_GRID_COLOR = "#404249"
_TEXT_COLOR = "#dcddde"


def _styled_figure(ylabel: str, title: str) -> tuple[Figure, Axes]:
    fig = Figure(figsize=(12, 7), facecolor=_BG_COLOR)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111, facecolor=_BG_COLOR)
    for spine in ax.spines.values():
        spine.set_color(_GRID_COLOR)
    ax.tick_params(colors=_TEXT_COLOR)
    ax.xaxis.label.set_color(_TEXT_COLOR)
    ax.yaxis.label.set_color(_TEXT_COLOR)
    ax.title.set_color(_TEXT_COLOR)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, linestyle="--", color=_GRID_COLOR, alpha=0.45)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    return fig, ax


def _export_png(fig: Figure) -> bytes:
    fig.autofmt_xdate(rotation=30, ha="right")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150, facecolor=_BG_COLOR)
    return buf.getvalue()


def render_firsts_chart(
    entries: Sequence[FirstEntry],
    resolve_name: Callable[[str], str],
) -> bytes:
    """Render cumulative /1st wins over time as PNG bytes (thread-safe)."""
    by_user: dict[str, list[FirstEntry]] = defaultdict(list)
    for entry in sorted(entries, key=lambda e: e.timesent):
        by_user[entry.user_id].append(entry)

    fig, ax = _styled_figure("Cumulative Firsts", "Firsts to Date")
    palette = cm.tab10.colors  # type: ignore[attr-defined]

    ranked_users = sorted(by_user.keys(), key=lambda uid: -len(by_user[uid]))
    for idx, user_id in enumerate(ranked_users):
        user_entries = by_user[user_id]
        xs = [e.timesent for e in user_entries]
        ys = list(range(1, len(user_entries) + 1))
        ax.plot(
            xs,
            ys,
            label=resolve_name(user_id),
            color=palette[idx % len(palette)],
            linewidth=2.5,
        )

    legend = ax.legend(loc="upper left", frameon=False)
    for text in legend.get_texts():
        text.set_color(_TEXT_COLOR)

    return _export_png(fig)


def render_juice_chart(points: Sequence[JuicePoint]) -> bytes:
    """Render daily juice over time as PNG bytes (thread-safe)."""
    fig, ax = _styled_figure("Juice", "Daily Juice")
    xs = [p.timesent_eastern for p in points]
    ys = [p.juice for p in points]
    ax.plot(xs, ys, color=f"#{EMBED_COLOR:06x}", linewidth=2.5)
    return _export_png(fig)
