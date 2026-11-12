# ---------- Отбор SKU под уровень анализа ----------
import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import logging

import pandas as pd

from app.assignments.models import Assignment, StratumTask
from app.config import policies
from app.etl.regularize import last_grid_points


logger = logging.getLogger(__name__)


def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------- 1. Классификация коллекции по сезону ----------
BASE_MARKERS = ("базовая коллекц", "школ")
SPRING_SUMMER = ("весна", "лето")
AUTUMN_WINTER = ("осень", "зима")

# На сколько лет назад коллекция того же сезона ещё считается актуальной.
# 1 = в сентябре 2026 (цель: осень-зима 2027) принимается и «Осень-зима 2026».
MAX_COLLECTION_YEAR_LAG = 1


def is_base_collection(collection) -> bool:
    """Базовая = либо явный маркер, либо коллекция без признака сезона
    (в том числе пустая). Карточку без разметки не отбрасываем."""
    text = "" if pd.isna(collection) else str(collection).lower()
    if any(marker in text for marker in BASE_MARKERS):
        return True
    return collection_season(collection)[0] is None


def collection_season(collection) -> tuple[str | None, int | None]:
    if pd.isna(collection):
        return None, None
    text = str(collection).lower()
    year_match = re.search(r"(20\d{2})", text)
    year = int(year_match.group(1)) if year_match else None
    has_ss = any(w in text for w in SPRING_SUMMER)
    has_aw = any(w in text for w in AUTUMN_WINTER)
    if has_ss and not has_aw:
        return "spring_summer", year
    if has_aw and not has_ss:
        return "autumn_winter", year
    return None, year


def target_season(as_of: date) -> tuple[str, int]:
    if 3 <= as_of.month <= 8:
        return "spring_summer", as_of.year
    if as_of.month in (1, 2):
        return "autumn_winter", as_of.year
    return "autumn_winter", as_of.year + 1   # сен-дек: в продаже коллекция следующего года


def is_current_season(collection, as_of: date) -> bool:
    season, year = collection_season(collection)
    if season is None:
        return False
    target_s, target_y = target_season(as_of)
    if season != target_s:
        return False
    return year is None or year >= target_y - MAX_COLLECTION_YEAR_LAG


def split_seasonal_base(count_needed: int, is_seasonal: bool, base_share) -> tuple[int, int]:
    """Пограничный случай (ровно .5) - в пользу СЕЗОННОЙ карточки."""
    if not is_seasonal or base_share is None or pd.isna(base_share):
        return 0, count_needed
    base_raw = count_needed * float(base_share)
    base_floor = math.floor(base_raw + 1e-9)
    frac = base_raw - base_floor
    base_needed = base_floor if abs(frac - 0.5) < 1e-9 else round_half_up(base_raw)
    return count_needed - base_needed, base_needed


# ---------- 2. Результат отбора ----------
@dataclass
class Shortfall:
    level_key: str
    gender: str
    needed: int
    taken: int
    reason: str

    @property
    def deficit(self) -> int:
        return self.needed - self.taken


@dataclass
class Selection:
    card_ids: list[str]
    shortfalls: list[Shortfall] = field(default_factory=list)
    scale_factor: float = 1.0          # 1.0 = урезания не было
    total_quota: int = 0
    sufficient: bool = True            # False - данных не хватает, расчёт не делаем
    insufficient_reason: str | None = None

    @property
    def deficit(self) -> int:
        return sum(s.deficit for s in self.shortfalls)

    @property
    def deficit_share(self) -> float:
        return self.deficit / self.total_quota if self.total_quota else 0.0

    def warning_text(self, level_label: str) -> str | None:
        if not self.sufficient:
            return f"Недостаточно данных для аналитики {level_label}: {self.insufficient_reason}"
        if not self.shortfalls:
            return None
        head = [f"Внимание! Аналитика {level_label} построена с нехваткой."]
        if self.scale_factor < 1.0:
            head.append(f"Дефицит {self.deficit_share:.1%} превысил допустимый - страты урезаны "
                        f"пропорционально, коэффициент {self.scale_factor:.3f}. "
                        f"Доли задания сохранены, объём выборки уменьшен.")
        else:
            head.append(f"Дефицит {self.deficit_share:.1%} в пределах допустимого, "
                        f"выборка взята как есть.")
        head.append("Страта | Гендер | Нужно | Взято | Причина")
        for s in sorted(self.shortfalls, key=lambda x: -x.deficit):
            head.append(f"{s.level_key} | {s.gender} | {s.needed} | {s.taken} | {s.reason}")
        return "\n".join(head)


def _insufficient_reason(taken: int, min_cards: int, min_points: int) -> str:
    return (f"пригодных карточек {taken} при минимуме {min_cards} "
            f"(свежий замер с ценой и {min_points} точек без пробелов)")


# ---------- 3. Пригодность карточки ----------
def eligible_cards(cards: pd.DataFrame, panel: pd.DataFrame,
                   min_points: int, as_of=None) -> pd.DataFrame:
    """Активные карточки, годные для алгоритма с рядом от min_points точек.

    Порядок проверки:
      1. последний замер: в точке расчёта as_of точка годная - замер не старше
         MAX_MEASUREMENT_AGE_HOURS и с ценой (не «нет в наличии»);
      2. длина ряда: последние min_points точек сетки до as_of - без пробелов.
         Окно задаёт алгоритм (у EWMA и MACD оно разное).
    Пробелы старше окна в панели заполнены предыдущей ценой и годности не мешают.
    as_of не задан - последняя точка панели. Сортировка - по длине ряда.
    """
    active = cards[cards["status"] == "active"]
    as_of = pd.Timestamp(as_of) if as_of is not None else panel["parsed_at"].max()
    window = (last_grid_points(as_of, min_points) if pd.notna(as_of)
              else pd.DatetimeIndex([]))

    valid = panel[panel["is_valid"].astype(bool)]
    fresh = set(valid.loc[valid["parsed_at"] == as_of, "card_id"])
    in_window = valid[valid["parsed_at"].isin(window)].groupby("card_id")["parsed_at"].nunique()
    full_window = set(in_window[in_window == len(window)].index) & fresh

    history = panel[(panel["parsed_at"] <= as_of) & panel["card_price"].notna()]
    stats = history.groupby("card_id")["parsed_at"].agg(
        n_points="count", first_at="min", last_at="max")
    stats["span_days"] = (stats["last_at"] - stats["first_at"]).dt.total_seconds() / 86400

    out = active.merge(stats, left_on="card_id", right_index=True, how="inner")
    out = out[out["card_id"].isin(full_window)]
    logger.debug("Пригодность (окно %d точек до %s): active=%d, свежий замер с ценой=%d, "
                 "окно без пробелов=%d", min_points, as_of, len(active),
                 len(fresh & set(active["card_id"])), len(out))
    return out.sort_values(["span_days", "n_points"], ascending=False)


def _deficit_share(plan: list[dict], scale: float) -> float:
    """Дефицит относительно квот, урезанных коэффициентом scale, по стратам из базы.
    Страт, которых нет в базе, урезание не исправит - они идут только в предупреждение."""
    targets = [round_half_up(p["quota"] * scale) for p in plan]
    total = sum(targets)
    if total == 0:
        return 0.0
    return sum(max(0, t - p["available"]) for t, p in zip(targets, plan)) / total


# ---------- 4. Уровень КАТЕГОРИИ: по квотам задания ----------
def select_for_category(assignment: Assignment, strata: pd.DataFrame,
                        cards: pd.DataFrame, panel: pd.DataFrame, *,
                        min_points: int, min_cards: int, as_of=None) -> Selection:
    """Порядок: считаем доступное -> решаем про урезание -> проверяем гейт -> отбираем.

    min_points задаёт алгоритм (у EWMA и MACD он разный), поэтому негодные ряды
    отсекаются ДО отбора и доли страт из задания не ломаются позже.
    """
    pool_all = eligible_cards(cards, panel, min_points, as_of)
    strata_key = strata.set_index(["gender", "layer", "subcategory", "item"])["stratum_id"]

    plan, missing = [], []
    for task in assignment.strata:
        if task.quota_category == 0 or task.item in policies.EXCLUDED_ITEMS:
            continue
        stratum_id = strata_key.get((task.gender, task.layer, task.subcategory, task.item))
        if stratum_id is None:
            missing.append(task)
            continue
        pool = pool_all[pool_all["stratum_id"] == stratum_id]
        plan.append({"task": task, "pool": pool,
                     "quota": task.quota_category, "available": len(pool)})

    total_quota = sum(p["quota"] for p in plan) + sum(t.quota_category for t in missing)
    if total_quota == 0:
        return Selection([], sufficient=False,
                         insufficient_reason="в задании нет страт с ненулевой квотой")

    # ---------- 4.1. Урезание: все страты сразу, шагами SCALE_STEP от исходных квот, ----------
    # пока дефицит относительно урезанных квот больше допустимого
    scale = 1.0
    while (_deficit_share(plan, scale) > policies.MAX_DEFICIT_SHARE
           and scale - policies.SCALE_STEP > 1e-9):
        scale = round(scale - policies.SCALE_STEP, 4)

    # ---------- 4.2. Отбор ----------
    chosen: list[str] = []
    shortfalls: list[Shortfall] = []
    for p in plan:
        task, quota = p["task"], p["quota"]
        scaled = round_half_up(quota * scale)
        target = min(scaled, p["available"])
        taken = p["pool"].head(target)["card_id"].tolist()
        chosen.extend(taken)
        if len(taken) < quota:
            reason = ("пропорциональное урезание" if scale < 1.0 and p["available"] >= scaled
                      else "дефект задания" if task.is_underprovisioned
                      else "недостаточно карточек")
            shortfalls.append(Shortfall(task.item, task.gender, quota, len(taken), reason))
    for task in missing:
        shortfalls.append(Shortfall(task.item, task.gender, task.quota_category, 0,
                                    "страты нет в базе"))

    # ---------- 4.3. Гейт ----------
    if len(chosen) < min_cards:
        return Selection(card_ids=[], shortfalls=shortfalls, scale_factor=scale,
                         total_quota=total_quota, sufficient=False,
                         insufficient_reason=_insufficient_reason(len(chosen), min_cards,
                                                                  min_points))

    return Selection(card_ids=chosen, shortfalls=shortfalls,
                     scale_factor=scale, total_quota=total_quota)


# ---------- 5. Уровень ПОДКАТЕГОРИИ: топ-N по длине ряда ----------
def select_for_subcategory(subcategory_key: tuple[str, int, str], strata: pd.DataFrame,
                           cards: pd.DataFrame, panel: pd.DataFrame, *,
                           min_points: int, min_cards: int, as_of=None) -> Selection:
    """subcategory_key = (гендер, слой, название). Квот по стратам здесь нет:
    подкатегория однородна, в расчёт идут все годные карточки (верхней границы нет:
    чем больше срез, тем выше мощность). Годных меньше min_cards - расчёта нет."""
    gender, layer, title = subcategory_key
    ids = strata[(strata["gender"] == gender) & (strata["layer"] == layer)
                 & (strata["subcategory"] == title)]["stratum_id"]

    pool = eligible_cards(cards, panel, min_points, as_of)
    pool = pool[pool["stratum_id"].isin(set(ids))]
    taken = pool["card_id"].tolist()

    if len(taken) < min_cards:
        return Selection(card_ids=[], total_quota=min_cards, sufficient=False,
                         insufficient_reason=_insufficient_reason(len(taken), min_cards,
                                                                  min_points))
    return Selection(card_ids=taken, total_quota=min_cards)


# ---------- 6. Уровень НИШИ: ближайшие к референсу ----------
def select_for_niche(cards: pd.DataFrame, panel: pd.DataFrame, *, target_count: int,
                     min_points: int, min_cards: int, as_of=None) -> Selection:
    """cards - состав ниши (схема NICHE_CARDS). В расчёт идут все годные карточки ниши
    (верхней границы нет). Ядро собирает plan_count с запасом на ротацию.
    target_count - плановый размер ниши, нужен только для total_quota."""
    pool = eligible_cards(cards, panel, min_points, as_of)
    taken = pool["card_id"].tolist()

    if len(taken) < min_cards:
        return Selection(card_ids=[], total_quota=target_count, sufficient=False,
                         insufficient_reason=_insufficient_reason(len(taken), min_cards,
                                                                  min_points))
    return Selection(card_ids=taken, total_quota=target_count)