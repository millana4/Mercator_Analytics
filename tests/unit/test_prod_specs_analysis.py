# ---------- Рекомендации по закупкам, шаг 2: запрос, проверка ответа, частоты, тексты ----------
import json

import pandas as pd
import pytest

from app.prod_specs.analysis import (AnalysisError, PROMPT_VERSION, SCALAR_FIELDS, analyze_selection,
                                     attrs_by_card_id, build_result, feature_frequencies,
                                     system_prompt, user_prompt)
from app.prod_specs.stratum_selection import Selection, selection_hash


def make_selection(n: int, stratum: str = "s-1") -> Selection:
    skus = [str(100 + i) for i in range(n)]
    cards = pd.DataFrame({"stratum_id": stratum, "card_id": [f"card-{s}" for s in skus],
                          "sku": skus, "name": [f"Худи {s}" for s in skus],
                          "url": [f"https://ozon/{s}" for s in skus]})
    return Selection(stratum, cards, skus, selection_hash(skus))


def attrs(garment="худи", decor=(), colors=(), extra=(), **scalars) -> dict:
    a = {f: None for f in SCALAR_FIELDS}
    a.update({"closure": [], "hardware": [], "decor": list(decor), "colors": list(colors),
              "extra": [{"name": n, "value": v} for n, v in extra]})
    a["garment_type"] = garment
    a.update(scalars)
    return a


def all_attrs(selection: Selection, **overrides: dict) -> dict[str, dict]:
    return {f"card-{s}": overrides.get(s, attrs()) for s in selection.skus}


def group(title, skus, description=("Фасон: прямой",), summary="Обычно прямой.",
          features_intro="Карточек в группе: {n}.") -> dict:
    return {"title": title, "skus": skus, "summary": summary,
            "features_intro": features_intro, "description": list(description)}


def answer(*groups, intro="Всего товаров: {n}.") -> str:
    data = {"groups": list(groups)}
    if intro is not None:
        data = {"intro": intro, **data}
    return json.dumps(data, ensure_ascii=False)


# ---------- Частоты ----------

def test_frequencies_scalars_lists_and_extra():
    cards = [attrs(decor=["рюши", "банты"], silhouette="оверсайз"),
             attrs(decor=["рюши"], silhouette="оверсайз"),
             attrs(silhouette="прямой", extra=[("бант на спине", "есть")])]

    freq = feature_frequencies(cards)

    assert freq["garment_type"] == [{"value": "худи", "count": 3, "share": 1.0}]
    assert freq["silhouette"] == [{"value": "оверсайз", "count": 2, "share": 0.667},
                                  {"value": "прямой", "count": 1, "share": 0.333}]
    assert freq["decor"] == [{"value": "рюши", "count": 2, "share": 0.667},
                             {"value": "банты", "count": 1, "share": 0.333}]
    assert freq["extra"] == [{"value": "бант на спине", "count": 1, "share": 0.333}]
    assert "closure" not in freq          # значений нет - признак не попадает


def test_frequencies_count_card_once_per_value():
    freq = feature_frequencies([attrs(decor=["рюши", "Рюши "])])

    assert freq["decor"] == [{"value": "рюши", "count": 1, "share": 1.0}]


def test_frequencies_empty():
    assert feature_frequencies([]) == {}


# ---------- Запрос ----------

def test_user_prompt_lists_cards_with_features():
    selection = make_selection(2)
    by_card = all_attrs(selection, **{"100": attrs(silhouette="оверсайз", decor=["рюши", "банты"],
                                                   extra=[("бант на спине", "есть")])})

    text = user_prompt(selection, by_card)

    assert text.startswith("Товаров: 2\n")
    assert "Артикул 100 — Худи 100" in text
    assert "фасон: оверсайз" in text
    assert "декор: рюши, банты" in text
    assert "прочее: бант на спине — есть" in text
    assert "Артикул 101 — Худи 101\n  вид изделия: худи\n" in text
    assert "рукав" not in text            # пустые признаки не передаются


def test_user_prompt_contains_frequencies():
    selection = make_selection(3)
    by_card = all_attrs(selection, **{"100": attrs(silhouette="оверсайз"),
                                      "101": attrs(silhouette="оверсайз"),
                                      "102": attrs(silhouette="прямой")})

    text = user_prompt(selection, by_card)

    assert "Частота признаков" in text
    assert "фасон: оверсайз — 2 из 3, прямой — 1 из 3" in text
    assert "вид изделия: худи — 3 из 3" in text


def test_user_prompt_without_features_has_no_frequency_block():
    selection = make_selection(2)
    by_card = {f"card-{s}": attrs(garment=None) for s in selection.skus}

    assert "Частота признаков" not in user_prompt(selection, by_card)


# ---------- Промпт ----------

def test_system_prompt_has_limits_and_number_placeholder():
    prompt = system_prompt()

    assert "не меньше 3 товаров" in prompt
    assert "групп не больше 3" in prompt
    assert "{min_group}" not in prompt and "{max_groups}" not in prompt
    assert "{n}" in prompt


def test_system_prompt_significance_is_left_to_the_model():
    prompt = system_prompt()

    assert "исходя из общих знаний" in prompt
    assert "5 классических рубашек и 5 домашних" in prompt
    assert "по два кармана" in prompt
    assert "оттенок" not in prompt and "небольшая разница" not in prompt


def test_system_prompt_requires_json_with_intro_and_groups():
    prompt = system_prompt()

    assert PROMPT_VERSION == "v4"
    assert '"grouping_note"' in prompt
    assert '"intro"' in prompt
    assert '"groups": [{"title"' in prompt      # фигурные скобки примера не потеряны
    assert '"features_intro"' in prompt


def test_system_prompt_requires_frequency_threshold_for_values():
    prompt = system_prompt()

    assert "примерно у четверти" in prompt
    assert "не упоминай его совсем" in prompt
    assert "реже короткий" in prompt


def test_system_prompt_prefers_one_group_and_exact_values():
    prompt = system_prompt()

    assert "По умолчанию группа одна" in prompt
    assert "Не дроби" in prompt
    assert "слово в слово" in prompt


# ---------- Проверка ответа ----------

def test_groups_and_leftover():
    selection = make_selection(8)
    content = answer(group("Прямые", ["100", "101", "102", "103"],
                           ["Фасон: прямой", "- Декор: рюши"]),
                     group("Оверсайз", ["104", "105", "106"], ["Фасон: оверсайз"]))

    result = build_result(content, selection, all_attrs(selection), "qwen3.6-35b-a3b")

    assert result["prompt_version"] == PROMPT_VERSION
    assert result["model"] == "qwen3.6-35b-a3b"
    assert result["n_cards"] == 8
    assert result["intro"] == "Всего товаров: 8."
    assert [g["title"] for g in result["groups"]] == ["Прямые", "Оверсайз", "Прочее"]
    first, second, other = result["groups"]
    assert first["description"] == ["Фасон: прямой", "Декор: рюши"]
    assert first["summary"] == "Обычно прямой."
    assert first["features_intro"] == "Карточек в группе: 4."
    assert second["features_intro"] == "Карточек в группе: 3."
    assert first["skus"] == ["100", "101", "102", "103"] and first["n_cards"] == 4
    assert first["cards"][0] == {"sku": "100", "name": "Худи 100", "url": "https://ozon/100"}
    assert second["skus"] == ["104", "105", "106"] and not second["other"]
    assert other["other"] is True and other["skus"] == ["107"] and other["description"] == []
    assert other["features_intro"] is None


def test_description_cleaning_and_text_form():
    selection = make_selection(3)
    cleaned = answer(group("Худи", ["100", "101", "102"],
                           ["- Фасон: прямой", "  • Декор:  рюши ", "", "1. Ткань: футер"]))
    as_text = json.dumps({"groups": [{"title": "Худи", "skus": ["100", "101", "102"],
                                      "description": "Фасон: прямой\nДекор: рюши"}]}, ensure_ascii=False)

    assert build_result(cleaned, selection, all_attrs(selection))["groups"][0]["description"] == [
        "Фасон: прямой", "Декор: рюши", "Ткань: футер"]
    assert build_result(as_text, selection, all_attrs(selection))["groups"][0]["description"] == [
        "Фасон: прямой", "Декор: рюши"]


def test_group_without_description_goes_to_other():
    selection = make_selection(6)
    content = answer(group("Пустая", ["100", "101", "102"], []),
                     group("Хорошая", ["103", "104", "105"]))

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert [g["title"] for g in groups] == ["Хорошая", "Прочее"]
    assert groups[1]["skus"] == ["100", "101", "102"]


def test_group_below_minimum_dissolved_into_other():
    selection = make_selection(8)
    content = answer(group("Большая", ["100", "101", "102", "103", "104"]),
                     group("Малая", ["105", "106"]))

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert [g["title"] for g in groups] == ["Большая", "Прочее"]
    assert groups[1]["skus"] == ["105", "106", "107"]      # порядок отбора


def test_unknown_and_duplicate_skus():
    selection = make_selection(8)
    content = answer(group("Первая", ["100", "101", "102", "999"]),
                     group("Вторая", ["102", "103", "104", "105"]))

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert groups[0]["skus"] == ["100", "101", "102"]       # 999 нет в выборке
    assert groups[1]["skus"] == ["103", "104", "105"]       # 102 уже в первой группе
    assert groups[2]["skus"] == ["106", "107"]


def test_more_than_max_groups_keeps_largest():
    selection = make_selection(16)
    content = answer(group("А", [str(i) for i in range(100, 105)]),      # 5
                     group("Б", [str(i) for i in range(105, 109)]),      # 4
                     group("В", [str(i) for i in range(109, 113)]),      # 4
                     group("Г", [str(i) for i in range(113, 116)]))      # 3

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert [g["title"] for g in groups] == ["А", "Б", "В", "Прочее"]
    assert groups[3]["skus"] == ["113", "114", "115"]


def test_no_usable_group_fails():
    selection = make_selection(6)

    with pytest.raises(AnalysisError, match="нет пригодных групп"):
        build_result(answer(group("Малая", ["100", "101"])), selection, all_attrs(selection))
    with pytest.raises(AnalysisError, match="нет пригодных групп"):
        build_result(answer(), selection, all_attrs(selection))


def test_unusable_answers_fail():
    selection = make_selection(4)
    by_card = all_attrs(selection)

    with pytest.raises(AnalysisError, match="нет JSON"):
        build_result("Не знаю.", selection, by_card)
    with pytest.raises(AnalysisError, match="JSON не разобран"):
        build_result("{ не json }", selection, by_card)
    with pytest.raises(AnalysisError, match="нет списка groups"):
        build_result('{"groups": "нет"}', selection, by_card)
    with pytest.raises(AnalysisError, match="нет списка groups"):
        build_result("{}", selection, by_card)


def test_default_title_and_missing_model():
    selection = make_selection(3)
    content = json.dumps({"groups": [{"skus": ["100", "101", "102"],
                                      "description": ["Фасон: прямой"]}]}, ensure_ascii=False)

    result = build_result(content, selection, all_attrs(selection))

    assert result["groups"][0]["title"] == "Группа 1"
    assert result["model"] is None


def test_group_and_stratum_frequencies():
    selection = make_selection(8)
    by_card = all_attrs(selection, **{"100": attrs(decor=["рюши"]), "101": attrs(decor=["рюши"])})
    content = answer(group("Худи", ["100", "101", "102", "103"]))

    result = build_result(content, selection, by_card)

    assert result["frequencies"]["decor"] == [{"value": "рюши", "count": 2, "share": 0.25}]
    assert result["groups"][0]["frequencies"]["decor"] == [{"value": "рюши", "count": 2, "share": 0.5}]


# ---------- Связные тексты ----------

def test_summary_kept_and_cleaned():
    selection = make_selection(3)
    content = answer(group("Худи", ["100", "101", "102"],
                           summary="  «Обычно оверсайз,\n  с пуговицами.» "))

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert groups[0]["summary"] == "Обычно оверсайз, с пуговицами."


def test_blank_or_missing_summary_group_stays():
    selection = make_selection(6)
    content = json.dumps({"groups": [
        {"title": "А", "skus": ["100", "101", "102"], "description": ["Фасон: прямой"]},
        {"title": "Б", "skus": ["103", "104", "105"], "summary": "   ",
         "description": ["Фасон: прямой"]}]}, ensure_ascii=False)

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert [g["title"] for g in groups] == ["А", "Б"]
    assert [g["summary"] for g in groups] == [None, None]


def test_long_summary_is_truncated():
    selection = make_selection(3)
    content = answer(group("Худи", ["100", "101", "102"], summary="а" * 1300))

    summary = build_result(content, selection, all_attrs(selection))["groups"][0]["summary"]

    assert summary == "а" * 1200 + "…"


def test_other_group_has_no_summary_or_intro_text():
    selection = make_selection(4)
    content = answer(group("Худи", ["100", "101", "102"]))

    groups = build_result(content, selection, all_attrs(selection))["groups"]

    assert groups[1]["other"] is True
    assert groups[1]["summary"] is None and groups[1]["features_intro"] is None


def test_intro_and_features_intro_get_exact_numbers():
    selection = make_selection(8)
    content = answer(group("Прямые", ["100", "101", "102", "103"],
                           features_intro="Карточек в группе: {n}."),
                     group("Оверсайз", ["104", "105", "106"],
                           features_intro="В группе {n} шт."),
                     intro="Всего товаров: {n}.")

    result = build_result(content, selection, all_attrs(selection))

    assert result["intro"] == "Всего товаров: 8."
    assert [g["features_intro"] for g in result["groups"][:2]] == [
        "Карточек в группе: 4.", "В группе 3 шт."]


def test_placeholder_variants():
    selection = make_selection(5)
    content = answer(group("Худи", ["100", "101", "102"], features_intro="Группа { N }, всего {n}."),
                     intro="{N} товаров, {  n  }.")

    result = build_result(content, selection, all_attrs(selection))

    assert result["intro"] == "5 товаров, 5."
    assert result["groups"][0]["features_intro"] == "Группа 3, всего 3."


def test_texts_without_placeholder_are_kept():
    selection = make_selection(3)
    content = answer(group("Худи", ["100", "101", "102"], features_intro="Признаки группы:"),
                     intro="Описание страты:")

    result = build_result(content, selection, all_attrs(selection))

    assert result["intro"] == "Описание страты:"
    assert result["groups"][0]["features_intro"] == "Признаки группы:"


def test_grouping_note_is_kept_and_cleaned():
    selection = make_selection(3)
    data = {"grouping_note": "  Товары однотипны:\n  одна группа. ", "groups": [
        {"title": "Худи", "skus": ["100", "101", "102"], "description": ["Фасон: прямой"]}]}

    result = build_result(json.dumps(data, ensure_ascii=False), selection, all_attrs(selection))

    assert result["grouping_note"] == "Товары однотипны: одна группа."


def test_missing_grouping_note_is_none():
    selection = make_selection(3)

    result = build_result(answer(group("Худи", ["100", "101", "102"])), selection, all_attrs(selection))

    assert result["grouping_note"] is None


def test_missing_intro_and_features_intro_are_none():
    selection = make_selection(3)
    content = json.dumps({"groups": [{"title": "Худи", "skus": ["100", "101", "102"],
                                      "description": ["Фасон: прямой"]}]}, ensure_ascii=False)

    result = build_result(content, selection, all_attrs(selection))

    assert result["intro"] is None
    assert result["groups"][0]["features_intro"] is None
    assert result["groups"][0]["summary"] is None


# ---------- Шаг 2 целиком ----------

def fake_llm(content: str, model: str = "qwen3.6-35b-a3b"):
    calls = []

    def call(system, user, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        return {"content": content, "model": model}
    return call, calls


def test_analyze_selection_calls_model_and_builds_result():
    selection = make_selection(4)
    call, calls = fake_llm(answer(group("Худи", ["100", "101", "102"])))

    result = analyze_selection(selection, all_attrs(selection), call)

    assert len(calls) == 1
    assert calls[0]["system"] == system_prompt()
    assert "Артикул 100" in calls[0]["user"]
    assert calls[0]["ref_type"] == "stratum" and calls[0]["ref_id"] == "s-1"
    assert calls[0]["max_tokens"] == 3000
    assert result["model"] == "qwen3.6-35b-a3b"
    assert [g["title"] for g in result["groups"]] == ["Худи", "Прочее"]


def test_analyze_selection_requires_attributes_for_all_cards():
    selection = make_selection(4)
    by_card = all_attrs(selection)
    del by_card["card-103"]
    call, calls = fake_llm(answer(group("Худи", ["100", "101", "102"])))

    with pytest.raises(AnalysisError, match="нет признаков"):
        analyze_selection(selection, by_card, call)
    assert calls == []


def test_attrs_by_card_id():
    frame = pd.DataFrame({"card_id": ["c1", "c2"], "prompt_version": ["v1", "v1"],
                          "attrs": [{"decor": ["рюши"]}, None]})

    assert attrs_by_card_id(frame) == {"c1": {"decor": ["рюши"]}}