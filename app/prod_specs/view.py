# ---------- Рекомендации по закупкам: вид анализа страты для дашборда ----------
# Из записи prod_specs_analysis собирается то, что показывается закупщику: кластеры
# (название, собирательное описание, типичные признаки, признаки с частотами, карточки)
# и карточки группы «Прочее». Пояснение модели о группировке и вводные фразы не отдаются:
# тексты вокруг списков живут во фронтенде.
from app.prod_specs.analysis import FIELD_ORDER


def card_item(card: dict) -> dict:
    """Карточка кластера: sku числом (как у участников события), название, ссылка."""
    sku = str(card.get("sku", ""))
    return {"sku": int(sku) if sku.isdigit() else sku,
            "name": card.get("name"), "url": card.get("url")}


def features(frequencies: dict | None) -> list[dict]:
    """Признаки с частотами списком в порядке показа: известные по схеме, затем остальные.
    JSONB не хранит порядок ключей, поэтому порядок задаётся здесь."""
    freq = frequencies or {}
    fields = [f for f in FIELD_ORDER if f in freq] + sorted(f for f in freq if f not in FIELD_ORDER)
    return [{"field": f, "values": [{"value": i["value"], "count": i["count"]} for i in freq[f]]}
            for f in fields]


def cluster_view(group: dict) -> dict:
    cards = group.get("cards") or []
    return {"title": group.get("title"),
            "n_cards": group.get("n_cards", len(cards)),
            "summary": group.get("summary"),
            "typical": list(group.get("description") or []),
            "features": features(group.get("frequencies")),
            "cards": [card_item(c) for c in cards]}


def _groups(row) -> list[dict]:
    return (row.result or {}).get("groups") or []


def subcategory_of(stratum_label: str) -> str:
    """Название подкатегории из подписи страты: часть до «: » (формат подписи -
    «Блузы и рубашки, девочки: Рубашка»); подпись без предмета - целиком."""
    return (stratum_label or "").split(": ", 1)[0]


def list_item(row) -> dict:
    """Строка списка страт для выпадающего меню. subcategory - название подкатегории
    точно как в блоке «Подкатегория»: по нему фронтенд объединяет страты в один пункт."""
    return {"stratum_id": str(row.stratum_id), "label": row.stratum_label,
            "subcategory": subcategory_of(row.stratum_label),
            "n_cards": row.n_cards,
            "clusters": sum(1 for g in _groups(row) if not g.get("other")),
            "analyzed_at": row.analyzed_at}


def detail_view(row) -> dict:
    """Анализ страты: кластеры и карточки «Прочее» отдельным списком."""
    groups = _groups(row)
    return {"stratum_id": str(row.stratum_id), "label": row.stratum_label,
            "category": row.category, "geo": row.geo, "marketplace": row.marketplace,
            "analyzed_at": row.analyzed_at, "n_cards": row.n_cards,
            "clusters": [cluster_view(g) for g in groups if not g.get("other")],
            "other_cards": [card_item(c) for g in groups if g.get("other")
                            for c in g.get("cards") or []]}