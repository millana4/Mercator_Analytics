# ---------- Каноническое представление задания на сбор и анализ ----------
from dataclasses import dataclass

# Соответствие обозначений гендера в разных файлах -> код в attrs базы ядра
GENDER_MAP: dict[str, str] = {
    "ж": "f", "м": "m",
    "female": "f", "male": "m", "baby": "baby",
    "унисекс": "u", "unisex": "u",
}


@dataclass(frozen=True)
class StratumTask:
    """Одна страта задания: что искать, сколько парсить и сколько брать в анализ."""
    gender: str  # f | m | baby | u
    layer: int                # 0..3
    subcategory: str          # у одежды совпадает с item (подкатегория-обёртка)
    item: str
    query: str
    quota_category: int       # сколько карточек нужно для анализа КАТЕГОРИИ
    plan_parsing: int         # сколько карточек парсится (лежит в stratum.count)
    track_subcategory: bool   # строим ли отдельную аналитику по подкатегории
    is_seasonal: bool
    base_share: float | None

    @property
    def is_underprovisioned(self) -> bool:
        """Квота анализа больше плана парсинга - страта заведомо недоберёт."""
        return self.quota_category > self.plan_parsing

    def subcategory_key(self) -> tuple[str, int, str]:
        """Ключ подкатегории: (гендер, слой, название). Одно название может
        существовать в разных гендерах - 'Верхняя одежда' у Female и у Male."""
        return (self.gender, self.layer, self.subcategory)


@dataclass
class Assignment:
    """Задание целиком: категория + список страт."""
    category: str             # clothing | kids_clothing
    source_name: str          # имя исходного файла
    strata: list[StratumTask]

    @property
    def total_quota(self) -> int:
        return sum(s.quota_category for s in self.strata)

    @property
    def total_plan(self) -> int:
        return sum(s.plan_parsing for s in self.strata)

    def tracked_subcategories(self) -> list[tuple[str, int, str]]:
        return sorted({s.subcategory_key() for s in self.strata if s.track_subcategory})