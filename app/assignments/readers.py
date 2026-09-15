# ---------- Чтение заданий: два формата CSV -> одна каноническая структура ----------
import io
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from app.assignments.models import Assignment, StratumTask, GENDER_MAP


def round_half_up(x: float) -> int:
    """Математическое округление (0.5 вверх), как в импорте ядра. Не банковское round()."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "TRUE"


def _to_gender(value: str) -> str:
    key = str(value).strip().lower()
    if key not in GENDER_MAP:
        raise ValueError(f"Неизвестный гендер: {value!r}")
    return GENDER_MAP[key]


def _to_layer(value) -> int:
    """'0 нательный' -> 0, '3 верхний' -> 3, 3.0 -> 3."""
    text = str(value).strip()
    digits = text.split()[0] if " " in text else text
    return int(float(digits))


def _read_csv(raw: bytes | str) -> pd.DataFrame:
    source = io.BytesIO(raw) if isinstance(raw, bytes) else raw
    df = pd.read_csv(source)
    df.columns = [c.strip() for c in df.columns]   # 'Предмет ' -> 'Предмет'
    return df


# ---------- 1. Плоский формат (одежда) ----------
def _read_flat(df: pd.DataFrame, source_name: str, category: str) -> Assignment:
    rows = df[df["Гендер"].notna()]           # последняя строка файла - итоговая
    strata: list[StratumTask] = []
    for _, r in rows.iterrows():
        item = str(r["Предмет"]).strip()
        strata.append(StratumTask(
            gender=_to_gender(r["Гендер"]),
            layer=_to_layer(r["Слой"]),
            subcategory=item,                  # подкатегория-обёртка
            item=item,
            query=str(r["Поисковый запрос"]).strip(),
            quota_category=round_half_up(r["Кол-во карточек в летнем сете"]),
            plan_parsing=int(r["Кол-во итоговое с округлением"]),
            track_subcategory=False,           # у одежды подкатегорий нет
            is_seasonal=_to_bool(r["Сезонный сплит"]),
            base_share=None if pd.isna(r["Доля базы"]) else float(r["Доля базы"]),
        ))
    return Assignment(category=category, source_name=source_name, strata=strata)


# ---------- 2. Иерархический формат (детская одежда) ----------
def _read_hierarchical(df: pd.DataFrame, source_name: str, category: str) -> Assignment:
    strata: list[StratumTask] = []
    current: dict | None = None

    for _, r in df.iterrows():
        is_subcat_row = pd.notna(r["Категория"])
        is_item_row = pd.notna(r["Предмет"])

        if is_subcat_row:
            current = {
                "gender": _to_gender(r["Гендер"]),
                "layer": _to_layer(r["Слой"]),
                "title": str(r["Категория"]).strip(),
                "quota": float(r["Необх. кол-ко карточек Озон для анализа кат."]),
                "plan": float(r["Итог подкатегории с округлением Озон"]),
                "track": _to_bool(r["Отслеживать подкатегорию"]),
            }
            continue

        if not is_item_row:
            continue                            # итоговая строка файла
        if current is None:
            raise ValueError(f"Строка предмета {r['Предмет']!r} идёт до строки подкатегории")

        share = 1.0 if pd.isna(r["Доля запроса"]) else float(r["Доля запроса"])
        plan = round_half_up(current["plan"] * share)
        if plan == 0:
            continue                            # ядро такую страту не создаёт - пропускаем

        strata.append(StratumTask(
            gender=current["gender"],
            layer=current["layer"],
            subcategory=current["title"],
            item=str(r["Предмет"]).strip(),
            query=str(r["Поисковый запрос"]).strip(),
            quota_category=round_half_up(current["quota"] * share),
            plan_parsing=plan,
            track_subcategory=current["track"],
            is_seasonal=_to_bool(r["Сезонный сплит"]),
            base_share=None if pd.isna(r["Доля базы"]) else float(r["Доля базы"]),
        ))
    return Assignment(category=category, source_name=source_name, strata=strata)


# ---------- 3. Точка входа: формат определяется по заголовку ----------
def read_assignment(raw: bytes | str, source_name: str, category: str) -> Assignment:
    df = _read_csv(raw)
    if "Категория" in df.columns:
        return _read_hierarchical(df, source_name, category)
    if "Гендер" in df.columns:
        return _read_flat(df, source_name, category)
    raise ValueError(f"Неизвестный формат задания: колонки {list(df.columns)}")