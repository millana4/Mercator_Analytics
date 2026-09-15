# ---------- Отрисовка графиков: кривая рынка + обнаруженные события ----------
import io
import logging
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from app.config import policies

logger = logging.getLogger(__name__)

# Цвета как в проработке алгоритмов: снижение - золотой, рост - синий
DIRECTION_COLORS = {"down": "gold", "up": "royalblue"}
DIRECTION_TITLES = {"down": "Снижение цен", "up": "Рост цен"}


def render_chart(curve: pd.DataFrame, events: pd.DataFrame, *, title: str,
                 subtitle: str | None = None) -> bytes:
    """Кривая среднего уровня цены с подсветкой найденных событий.

    Ось Y - геометрическое среднее цены в рублях: exp() от среднего log-уровня.
    Логарифм нужен алгоритмам, но пользователю понятнее рубли.
    Используется Figure напрямую, а не pyplot: у pyplot глобальное состояние,
    в веб-сервере с несколькими запросами это источник гонок.
    """
    fig = Figure(figsize=(13, 6), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    if curve.empty:
        ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center",
                transform=ax.transAxes, fontsize=13, color="gray")
        ax.set_axis_off()
        return _to_png(fig)

    curve = curve.sort_values("parsed_at")
    price_level = np.exp(curve["level_value"].astype(float))
    ax.plot(curve["parsed_at"], price_level, color="black", linewidth=1.6,
            label="средний уровень цены", zorder=3)

    # ---------- Подсветка событий ----------
    for _, e in events.iterrows():
        color = DIRECTION_COLORS.get(e["direction"], "gray")
        ax.axvspan(e["start_at"], e["end_at"], color=color, alpha=0.20, zorder=1)
        ax.axvline(e["start_at"], color=color, linestyle="--", linewidth=1.2, zorder=2)

    # ---------- Легенда собирается вручную: axvspan в неё не попадает ----------
    handles = [Line2D([0], [0], color="black", linewidth=1.6, label="средний уровень цены")]
    for direction in ("down", "up"):
        if (events["direction"] == direction).any() if not events.empty else False:
            handles.append(Line2D([0], [0], color=DIRECTION_COLORS[direction], linewidth=8,
                                  alpha=0.4, label=DIRECTION_TITLES[direction]))
    ax.legend(handles=handles, loc="best", fontsize=9)

    full_title = title if subtitle is None else f"{title}\n{subtitle}"
    ax.set_title(full_title, fontsize=12)
    ax.set_xlabel("Дата")
    ax.set_ylabel("Средний уровень цены, ₽")
    ax.grid(alpha=0.25, linewidth=0.6)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _to_png(fig)


def _to_png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


def _safe_name(text: str) -> str:
    """Имя файла из названия снепшота: пробелы и запятые ломают пути."""
    cleaned = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    return cleaned.strip("_")[:80]


def save_debug_chart(png: bytes, *, category: str, level: str, level_key: str,
                     algorithm: str, grid_point_at: datetime) -> Path | None:
    """Кладёт PNG в отладочный каталог. Файл на точку сетки один - перезаписывается."""
    if not policies.DEBUG_CHARTS:
        return None
    stamp = grid_point_at.strftime("%Y%m%d_%H%M")
    directory = Path(policies.DEBUG_DIR) / stamp / category
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{level}_{_safe_name(level_key)}_{algorithm}.png"
    path.write_bytes(png)
    return path