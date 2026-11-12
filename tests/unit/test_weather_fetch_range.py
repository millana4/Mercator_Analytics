# ---------- Погода: какие сутки докачивать у Open-Meteo ----------
from datetime import date

from app.analytics.weather import fetch_range

REFRESH = 3
CACHE_MIN, CACHE_MAX = date(2026, 9, 1), date(2026, 11, 11)


def test_empty_cache_fetches_whole_window():
    assert fetch_range(None, None, date(2026, 11, 5), date(2026, 11, 12), REFRESH) == (date(2026, 11, 5), date(2026, 11, 12))


def test_cache_not_covering_start_fetches_whole_window():
    assert fetch_range(date(2026, 11, 1), CACHE_MAX, date(2026, 10, 20), date(2026, 11, 12), REFRESH) == (date(2026, 10, 20), date(2026, 11, 12))


def test_covered_window_refreshes_last_days_of_cache():
    assert fetch_range(CACHE_MIN, CACHE_MAX, date(2026, 11, 5), date(2026, 11, 12), REFRESH) == (date(2026, 11, 8), date(2026, 11, 12))


def test_window_starting_inside_refresh_days_is_not_widened():
    assert fetch_range(CACHE_MIN, CACHE_MAX, date(2026, 11, 10), date(2026, 11, 12), REFRESH) == (date(2026, 11, 10), date(2026, 11, 12))


def test_window_ending_exactly_at_refresh_start_is_fetched():
    assert fetch_range(CACHE_MIN, CACHE_MAX, date(2026, 11, 1), date(2026, 11, 8), REFRESH) == (date(2026, 11, 8), date(2026, 11, 8))


def test_old_window_needs_no_fetch():
    # случай из лога: ряд сета заканчивается 13.10, а кеш общий на гео и доходит до 11.11 -
    # раньше получался перевёрнутый запрос start_date=2026-11-08&end_date=2026-10-13 и ошибка 400
    assert fetch_range(CACHE_MIN, CACHE_MAX, date(2026, 10, 12), date(2026, 10, 13), REFRESH) is None