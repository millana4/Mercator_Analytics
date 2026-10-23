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

# Допуск регуляризации: в точку сетки берётся последний замер не старше стольких
# часов. Нет такого замера или он без цены («нет в наличии») — точка-пробел.
# 24 ч = минимальная частота обхода (раз в сутки).
MAX_MEASUREMENT_AGE_HOURS: int = 24

# ---------- Порог события ----------
# Допустимая ложная тревога в точке. Порог события k (число карточек с флагом)
# считается в каждой точке от числа карточек в срезе n и фона алгоритма p0:
# наименьшее k, при котором P(X >= k | n, p0) <= FALSE_ALARM_MAX, X ~ Bin(n, p0).
FALSE_ALARM_MAX: float = 0.01

# ---------- Анализ поисковой выдачи ----------
# Сравниваются только суточные снимки SEARCH_DAILY_TIME ровно через SEARCH_GAP_HOURS:
# за 12 ч выдача меняется меньше, чем за сутки, пары через 12 ч (утренние снимки
# до 14.10) смешивать с суточными нельзя.
SEARCH_DAILY_TIME: tuple[int, int] = (18, 37)
SEARCH_GAP_HOURS: int = 24
# Минимальная доля общих страт пары снимков: меньше - точки нет.
SEARCH_MIN_STRATA_SHARE: float = 0.75
# Индикатор «ниже обычного»: обычная повторяемость - медиана последних SEARCH_REP_WINDOW
# нормальных дней, разброс - MAD x 1,4826 (не меньше SEARCH_REP_SCALE_FLOOR п.п.),
# порог - SEARCH_REP_K разбросов. Первые SEARCH_REP_MIN_DAYS дней - разгон.
SEARCH_REP_WINDOW: int = 7
SEARCH_REP_MIN_DAYS: int = 4
SEARCH_REP_K: float = 2.0
SEARCH_REP_SCALE_FLOOR: float = 2.0
# Блок «Анализ поисковой выдачи» на дашборде - при стольких точках и больше
# (одна точка = два суточных снимка через 24 ч с достаточным числом общих страт).
SEARCH_MIN_POINTS: int = 1

# ---------- Погода к снимкам выдачи ----------
# Координаты гео сета для Open-Meteo; погода снимка - за WEATHER_WINDOW_HOURS до него.
GEO_COORDS: dict[str, tuple[float, float]] = {"Санкт-Петербург": (59.9386, 30.3141)}
WEATHER_WINDOW_HOURS: int = 24
# Почасовые данные по сегодняшний день; с сервера открыт (api.open-meteo.com закрыт).
WEATHER_API_URL: str = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Сколько последних суток кеша перезапрашивать: свежие данные Open-Meteo уточняет.
WEATHER_REFRESH_DAYS: int = 3
# Значок погоды суток: небо по средней облачности, %; осадки - от суммы за сутки, мм.
WEATHER_SKY_CLEAR_MAX: float = 25.0     # меньше - ясно (солнце)
WEATHER_SKY_PARTLY_MAX: float = 70.0    # до этого значения - переменная облачность
WEATHER_PRECIP_MIN_MM: float = 0.5      # меньше - без осадков на значке

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

# ---------- Товарные ниши ----------
# Видимость доступных ниш: "all" — все ниши подписанных связок (категория +
# маркетплейс + гео), "own" — только ниши пользователя (user_niche).
NICHE_VISIBILITY: str = "all"

# Бесплатных активных ниш на пользователя.
FREE_NICHES_INTERNAL: int = 3
FREE_NICHES_EXTERNAL: int = 1

# Домены маркетплейсов — проверка ссылки на референс.
# Ссылка подходит, если её домен совпадает с одним из доменов или является его поддоменом.
MARKETPLACE_DOMAINS: dict[str, tuple[str, ...]] = {
    "ozon": ("ozon.ru",),
}

# Минимум карточек ниши для расчёта. Расчёт размера выборки (ложная тревога <= 1%,
# мощность >= 80% при доле участников 30%): минимум 25 при фоне 5%; при фоне EWMA 6%
# 25 карточек дают мощность около 81%, поэтому 26 - с запасом.
MIN_CARDS_NICHE: int = 26

# Как часто досылать в ядро ниши, которые не удалось отправить сразу
# (задания pending и неотправленные удаления), секунд.
NICHE_RESEND_INTERVAL_SECONDS: int = 60

# сколько аналогов ниши отдаёт /niches/{id}/analogs
NICHE_ANALOGS_LIMIT = 30

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