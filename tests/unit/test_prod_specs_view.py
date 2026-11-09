# ---------- Рекомендации по закупкам: вид анализа страты для дашборда ----------
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from app.prod_specs.view import (card_item, cluster_view, detail_view, features, list_item,
                                 subcategory_of)


def freq(*pairs) -> list[dict]:
    return [{"value": v, "count": c, "share": 0.5} for v, c in pairs]


def group(title="Пижама", other=False, skus=("1", "2", "3")) -> dict:
    return {"title": title, "skus": list(skus), "n_cards": len(skus), "summary": "Описание",
            "features_intro": "внутреннее", "description": ["Фасон: свободный"],
            "frequencies": {"silhouette": freq(("свободный", len(skus)))},
            "cards": [{"sku": s, "name": f"Карточка {s}", "url": f"https://ozon/{s}"} for s in skus],
            "other": other}


def row(groups) -> SimpleNamespace:
    result = {"prompt_version": "v4", "grouping_note": "внутреннее", "intro": "внутреннее",
              "frequencies": {"silhouette": freq(("свободный", 3))}, "groups": groups}
    return SimpleNamespace(stratum_id=uuid4(), stratum_label="девочки, Пижама",
                           category="kids_clothing", geo="Санкт-Петербург", marketplace="ozon",
                           analyzed_at=datetime(2026, 11, 9, 10, 5),
                           n_cards=sum(g["n_cards"] for g in groups), result=result)


def test_features_follow_field_order_and_unknown_go_last():
    frequencies = {"extra": freq(("бант", 1)), "zeta": freq(("а", 1)), "silhouette": freq(("а", 1)),
                   "garment_type": freq(("а", 1)), "alpha": freq(("а", 1)), "decor": freq(("а", 1))}

    assert [f["field"] for f in features(frequencies)] == [
        "garment_type", "silhouette", "decor", "extra", "alpha", "zeta"]


def test_features_values_keep_order_and_drop_share():
    result = features({"silhouette": freq(("а", 2), ("б", 1))})

    assert result == [{"field": "silhouette",
                       "values": [{"value": "а", "count": 2}, {"value": "б", "count": 1}]}]


def test_features_empty():
    assert features(None) == []
    assert features({}) == []


def test_card_item_sku_is_number_when_digits():
    assert card_item({"sku": "123", "name": "н", "url": "u"}) == {"sku": 123, "name": "н", "url": "u"}
    assert card_item({"sku": "AB12", "name": "н", "url": "u"})["sku"] == "AB12"


def test_list_item_counts_clusters_without_other():
    item = list_item(row([group(), group("Прочее", other=True, skus=("4",))]))

    assert item["label"] == "девочки, Пижама"
    assert item["subcategory"] == "девочки, Пижама"
    assert item["clusters"] == 1
    assert item["n_cards"] == 4
    assert isinstance(item["stratum_id"], str)
    assert item["analyzed_at"] == datetime(2026, 11, 9, 10, 5)


def test_subcategory_of_cuts_item_after_colon():
    assert subcategory_of("Блузы и рубашки, девочки: Рубашка") == "Блузы и рубашки, девочки"
    assert subcategory_of("Худи, девочки") == "Худи, девочки"
    assert subcategory_of("") == ""


def test_detail_view_structure_and_hidden_fields():
    detail = detail_view(row([group()]))

    assert set(detail) == {"stratum_id", "label", "category", "geo", "marketplace",
                           "analyzed_at", "n_cards", "clusters", "other_cards"}
    cluster = detail["clusters"][0]
    assert set(cluster) == {"title", "n_cards", "summary", "typical", "features", "cards"}
    assert cluster["typical"] == ["Фасон: свободный"]
    assert cluster["features"] == [{"field": "silhouette", "values": [{"value": "свободный", "count": 3}]}]
    assert cluster["cards"][0] == {"sku": 1, "name": "Карточка 1", "url": "https://ozon/1"}


def test_detail_view_other_cards_listed_separately():
    detail = detail_view(row([group(), group("Прочее", other=True, skus=("4",))]))

    assert len(detail["clusters"]) == 1
    assert detail["other_cards"] == [{"sku": 4, "name": "Карточка 4", "url": "https://ozon/4"}]


def test_detail_view_without_other_and_minimal_group():
    assert detail_view(row([group()]))["other_cards"] == []
    assert cluster_view({"title": "Т"}) == {"title": "Т", "n_cards": 0, "summary": None,
                                            "typical": [], "features": [], "cards": []}