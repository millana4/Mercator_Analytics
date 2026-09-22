# ---------- Политики расчёта: сетка регуляризации и расписание ----------
from datetime import datetime, time, timedelta

from app.config.settings import get_settings

# Временные точки суток, к которым приводятся замеры (час, минута).
# Определяются возможностями парсеров: сейчас полный обход укладывается в 2 раза в сутки.
GRID_TIMES: tuple[tuple[int, int], ...] = ((0, 37), (12, 37))

# Задержка между точкой сетки и запуском расчёта аналитики, минут.
# Нужна, чтобы замеры последнего обхода успели дойти до ClickHouse.
ANALYTICS_OFFSET_MINUTES: int = 8

# ---------- Производные величины (не править руками) ----------
GRID_STEPS_PER_DAY: int = len(GRID_TIMES)
GRID_STEP_HOURS: float = 24 / GRID_STEPS_PER_DAY

# ---------- Гейты допуска к анализу ----------
# Минимум карточек на уровне. Меньше - аналитика не строится.
MIN_CARDS_CATEGORY: int = 100
MIN_CARDS_SUBCATEGORY: int = 30
MIN_CARDS_ITEM: int = 10

# Минимум непустых точек в ряду карточки, чтобы взять её в анализ.
MIN_SERIES_POINTS: int = 5

# Допустимый дефицит выборки от суммарной квоты.
# Меньше порога - предупреждение в лог, доли считаем соблюдёнными.
# Больше - все страты урезаются пропорционально, чтобы сохранить репрезентативность выборки.
MAX_DEFICIT_SHARE: float = 0.05

# ---------- Исключения и допуски выборки ----------
# Предметы, не участвующие в аналитике. В задании остаются: парсинг их собирает,
# но для оценки динамики рынка они непоказательны.
EXCLUDED_ITEMS: tuple[str, ...] = ("Термобельё",)

# ---------- Отладочные графики ----------
# Сохранять ли PNG на диск при каждом расчёте. На сервере выключать.
DEBUG_CHARTS = get_settings().debug_charts
DEBUG_DIR: str = "/srv/debug"


def grid_times() -> tuple[time, ...]:
    """Точки сетки регуляризации, отсортированные по возрастанию."""
    return tuple(sorted(time(hour=h, minute=m) for h, m in GRID_TIMES))


def analytics_run_times() -> tuple[time, ...]:
    """Моменты запуска расчёта: точка сетки + ANALYTICS_OFFSET_MINUTES."""
    base = datetime(2000, 1, 1)
    shifted = [
        (base.replace(hour=t.hour, minute=t.minute)
         + timedelta(minutes=ANALYTICS_OFFSET_MINUTES)).time()
        for t in grid_times()
    ]
    return tuple(sorted(shifted))


def cron_expression() -> tuple[str, str]:
    """Расписание для APScheduler в виде (часы, минуты) через запятую."""
    run = analytics_run_times()
    return ",".join(str(t.hour) for t in run), ",".join(str(t.minute) for t in run)