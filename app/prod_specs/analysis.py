# ---------- Рекомендации по закупкам, шаг 2: группы трендовых предметов по страте ----------
# Модель получает признаки отобранных карточек (шаг 1) и частоты признаков, сама определяет
# значимые различия и делит товары на 1-3 группы (по умолчанию группа одна). Для страты и
# каждой группы она пишет тексты: вводную фразу (intro), собирательное описание связным
# текстом (summary), фразу перед списком (features_intro) и список признаков (description).
# Число карточек в фразах модель пишет как {n}, подставляет код. Частоты признаков считает
# код и кладёт в результат. Ответ проверяется: sku из выборки, карточка в одной группе,
# группа не меньше PROD_SPECS_MIN_GROUP_CARDS.
import json
import re
from collections import Counter
from collections.abc import Callable

import pandas as pd

from app.config import policies
from app.prod_specs.stratum_selection import Selection

# Версия промпта шага 2: только записывается в результат, на решения не влияет.
# v2: связные тексты (intro, summary, features_intro) и частоты признаков в запросе.
# v3: группа по умолчанию одна, определение группы, поле grouping_note, значения слово в слово.
# v4: в текст и список попадают только значения, частые в группе (от четверти товаров);
#     в таблице частот указана доля «N из M».
PROMPT_VERSION = "v4"

SCALAR_FIELDS = ("garment_type", "silhouette", "length", "sleeve", "neckline",
                 "pockets", "fabric", "print", "style")
LIST_FIELDS = ("closure", "hardware", "decor", "colors")
# Порядок показа признаков; extra - признаки, которых нет в схеме.
FIELD_ORDER = ("garment_type", "silhouette", "length", "sleeve", "neckline", "closure",
               "hardware", "decor", "pockets", "fabric", "print", "colors", "style", "extra")
FIELD_LABELS = {
    "garment_type": "вид изделия", "silhouette": "фасон", "length": "длина", "sleeve": "рукав",
    "neckline": "воротник и вырез", "closure": "застёжка", "hardware": "фурнитура",
    "decor": "декор", "pockets": "карманы", "fabric": "ткань", "print": "принт",
    "colors": "цвета", "style": "стиль", "extra": "прочее",
}

OTHER_TITLE = "Прочее"
MAX_NAME_CHARS = 120
MAX_TITLE_CHARS = 60
MAX_LINE_CHARS = 200
MAX_DESCRIPTION_LINES = 12
MAX_SUMMARY_CHARS = 1200
MAX_PHRASE_CHARS = 300
MAX_NOTE_CHARS = 800
FREQ_TOP = 6        # значений признака в таблице частот запроса

_N_PLACEHOLDER = re.compile(r"\{\s*n\s*\}", re.IGNORECASE)

SYSTEM_TEMPLATE = """\
Ты помогаешь закупщикам одежды на маркетплейсе. Дан список товаров-маячков одной товарной \
категории: они стабильно держатся в топе поисковой выдачи, у них высокий рейтинг и много \
отзывов. Для каждого товара указаны признаки, определённые по фото и описанию. Перед списком \
товаров дана частота признаков среди всех товаров: у скольких товаров из общего числа есть \
каждое значение.

Задача: выяснить, что объединяет эти товары, и описать, какой предмет сейчас пользуется \
спросом, чтобы закупщики сформировали требования к товару.

Порядок работы:
1. Определи, какие признаки значимы для закупки, исходя из общих знаний об одежде и о том, \
что важно покупателю и закупщику. Что значимо, решаешь ты.
2. Группа - это тип изделия или назначение, которое закупщик закупает отдельной позицией со \
своими требованиями. По умолчанию группа одна. Делить товары на группы нужно, только если они \
заметно различаются по типу изделия или назначению. Например, 5 классических рубашек и 5 \
домашних - две группы: это разные изделия для разных целей. Если все товары - один тип \
изделия для одного назначения, группа одна, а различия внутри неё опиши в тексте как \
варианты, например «оверсайз или прямой». Например, если у 5 товаров по два кармана, а у 5 по \
одному, это одна группа.
3. Не дроби. Если отличаются лишь несколько товаров, это вариант внутри группы, а не \
отдельная группа. Каждая группа должна быть достаточно крупной, чтобы по ней можно было \
сформулировать самостоятельное требование к закупке.
4. Прежде чем делить товары на группы, напиши в grouping_note 2-3 предложения: что общего у \
всех товаров, чем они различаются и почему ты делишь их на группы или оставляешь одну.
5. Частота значений. Описывай то, что типично для группы. Упоминай значение признака в тексте \
и в списке, только если оно есть примерно у четверти товаров группы и больше. Если значение \
есть у одного-двух товаров, это редкое исключение: не упоминай его совсем, даже как «иногда» \
или «может быть». Если у большинства товаров группы значение одно, пиши его как типичное без \
вариантов. Вариант указывай, только если он значим, то есть есть примерно у четверти, трети \
или половины товаров, и называй, какой вариант чаще. Частоту считай по товарам группы, \
пользуйся таблицей частот и списком товаров.
6. Для каждой группы напиши тексты.

Ограничения: в группе не меньше {min_group} товаров, групп не больше {max_groups}. Каждый \
товар попадает не больше чем в одну группу. Товар, который не подходит ни к одной группе, \
не включай.

Тексты:
- grouping_note: 2-3 предложения о том, как ты решил группировать товары (см. порядок работы).
- intro: одна фраза в начале для всей страты. Пример: «ИИ проанализировал товары-маячки \
(всего: {n}) и составил собирательное описание:»
- title: короткое название группы, 2-5 слов.
- summary: собирательное описание типичного товара группы связным текстом из 3-5 предложений, \
как для закупщика. Пример: «Хлопковая рубашка для девочки обычно оверсайз, с отложным \
воротником и на пуговицах, белая и однотонная. Чаще всего школьного стиля.» Включай самые \
частые значения признаков группы, пиши «обычно», «чаще всего», «как правило». Редкие значения \
(см. правило о частоте) в текст не включай. Значимый вариант указывай с пометкой, какой чаще: \
«рукав чаще длинный, реже короткий». Числа и проценты не указывай. Ничего не придумывай \
сверх признаков из данных.
- features_intro: фраза перед списком признаков группы. Пример: «Проанализировано карточек: \
{n}. Вот признаки, которые выделил ИИ:»
- description: список из 4-10 пунктов вида «Фасон: прямой, оверсайз». Включай только значения, \
которые есть примерно у четверти товаров группы и больше (см. правило о частоте). Значения \
признаков бери из данных слово в слово, не меняя написание.
- Число товаров в intro и features_intro пиши только как {n}, сам цифры не ставь. В intro \
{n} - число всех товаров, в features_intro - число товаров этой группы.
- Значения пиши на русском, коротко.

Ответь строго одним JSON-объектом, без пояснений и без markdown:
{{"grouping_note": "...", "intro": "...", "groups": [{{"title": "...", \
"skus": ["артикул", "..."], "summary": "...", "features_intro": "...", \
"description": ["Фасон: ...", "..."]}}]}}
"""


class AnalysisError(Exception):
    """Ответ модели непригоден или входные данные неполны: шаг 2 по страте повторяют."""


def system_prompt() -> str:
    return (SYSTEM_TEMPLATE
            .replace("{min_group}", str(policies.PROD_SPECS_MIN_GROUP_CARDS))
            .replace("{max_groups}", str(policies.PROD_SPECS_MAX_GROUPS))
            .replace("{{", "{").replace("}}", "}"))


# ---------- Признаки карточек ----------

def attrs_by_card_id(frame: pd.DataFrame) -> dict[str, dict]:
    """prod_specs_card из коннектора (PROD_SPECS_CARDS) -> {card_id: признаки}."""
    return {str(r.card_id): r.attrs for r in frame.itertuples() if isinstance(r.attrs, dict)}


def _clean_value(value) -> str | None:
    if value is None or isinstance(value, (list, dict)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().lower()
    return text or None


def field_values(attrs: dict, field: str) -> list[str]:
    """Значения признака карточки без повторов, в нижнем регистре. Для extra - названия."""
    value = attrs.get(field)
    if field == "extra":
        items = [i.get("name") for i in value if isinstance(i, dict)] if isinstance(value, list) else []
    elif field in LIST_FIELDS:
        items = value if isinstance(value, list) else []
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        text = _clean_value(item)
        if text and text not in out:
            out.append(text)
    return out


def feature_frequencies(attrs_list: list[dict]) -> dict[str, list[dict]]:
    """По каждому признаку: значения с числом карточек и долей от числа карточек.
    Карточка считается один раз на значение. Признаки без значений не включаются."""
    n = len(attrs_list)
    out: dict[str, list[dict]] = {}
    for field in FIELD_ORDER:
        counter: Counter = Counter()
        for attrs in attrs_list:
            counter.update(field_values(attrs, field))
        if counter:
            out[field] = [{"value": value, "count": count, "share": round(count / n, 3)}
                          for value, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out


# ---------- Запрос ----------

def _card_line(attrs: dict) -> str:
    parts = []
    for field in FIELD_ORDER:
        if field == "extra":
            items = attrs.get("extra")
            pairs = []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and _clean_value(item.get("name")):
                    value = _clean_value(item.get("value")) or "есть"
                    pairs.append(f"{_clean_value(item.get('name'))} — {value}")
            if pairs:
                parts.append(f"{FIELD_LABELS['extra']}: " + "; ".join(pairs))
            continue
        values = field_values(attrs, field)
        if values:
            parts.append(f"{FIELD_LABELS[field]}: " + ", ".join(values))
    return "; ".join(parts)


def _frequency_lines(attrs_list: list[dict]) -> list[str]:
    """Таблица частот по всем товарам: «фасон: оверсайз — 7 из 11, свободный — 2 из 11»."""
    n = len(attrs_list)
    lines = []
    for field, items in feature_frequencies(attrs_list).items():
        shown = ", ".join(f"{i['value']} — {i['count']} из {n}" for i in items[:FREQ_TOP])
        lines.append(f"{FIELD_LABELS[field]}: {shown}")
    return lines


def user_prompt(selection: Selection, attrs_by_card: dict[str, dict]) -> str:
    """Частоты признаков и список отобранных карточек с их признаками, лучшие первыми."""
    attrs_list = [attrs_by_card[str(c)] for c in selection.cards["card_id"]]
    lines = [f"Товаров: {selection.n_cards}", ""]
    frequencies = _frequency_lines(attrs_list)
    if frequencies:
        lines.append("Частота признаков среди всех товаров (сколько товаров с этим значением):")
        lines += [f"  {line}" for line in frequencies]
        lines.append("")
    lines.append("Товары:")
    for row in selection.cards.itertuples():
        name = re.sub(r"\s+", " ", str(row.name)).strip()[:MAX_NAME_CHARS]
        lines.append(f"Артикул {row.sku} — {name}")
        lines.append("  " + _card_line(attrs_by_card[str(row.card_id)]))
    return "\n".join(lines) + "\n"


# ---------- Разбор ответа ----------

def _load_json(content: str | None) -> dict:
    text = (content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AnalysisError("в ответе нет JSON: " + text[:200])
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as e:
        raise AnalysisError(f"JSON не разобран: {e}") from e
    if not isinstance(data, dict):
        raise AnalysisError("JSON не объект")
    return data


def _group_skus(raw, valid: set[str], assigned: set[str]) -> list[str]:
    """sku группы из ответа: только из выборки, без повторов и без уже занятых другой группой."""
    items = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    out: list[str] = []
    for item in items:
        sku = str(item).strip()
        if sku in valid and sku not in assigned and sku not in out:
            out.append(sku)
    return out


def _description(raw) -> list[str]:
    """Пункты описания: без маркеров и нумерации, пустые убраны, длина и число ограничены."""
    items = raw.splitlines() if isinstance(raw, str) else raw if isinstance(raw, list) else []
    out: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item)).strip()
        text = re.sub(r"^(?:[-–—•*·]+|\d+[.)])\s*", "", text).strip()
        if text:
            out.append(text[:MAX_LINE_CHARS])
    return out[:MAX_DESCRIPTION_LINES]


def _clean_text(raw, limit: int) -> str | None:
    """Текст модели: пробелы схлопнуты, обрамляющие кавычки сняты, длина ограничена; пусто - None."""
    if not isinstance(raw, str):
        return None
    text = re.sub(r"\s+", " ", raw).strip().strip("«»\"'").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _with_number(raw, n: int) -> str | None:
    """Фраза модели с подставленным числом карточек: {n} (в любом регистре) заменяется на n."""
    text = _clean_text(raw, MAX_PHRASE_CHARS)
    return None if text is None else _N_PLACEHOLDER.sub(str(n), text)


def _title(raw, number: int) -> str:
    text = re.sub(r"\s+", " ", str(raw)).strip().strip("«»\"'") if isinstance(raw, str) else ""
    return text[:MAX_TITLE_CHARS] or f"Группа {number}"


def build_result(content: str | None, selection: Selection, attrs_by_card: dict[str, dict],
                 model: str | None = None) -> dict:
    """Проверяет ответ модели и собирает результат шага 2.
    Нет пригодных групп - AnalysisError. Нет связного текста (intro, summary, features_intro,
    grouping_note) - поле пусто, группа остаётся: фронт показывает то, что есть.
    grouping_note - пояснение модели, как она группировала; закупщику не показывается."""
    data = _load_json(content)
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list):
        raise AnalysisError("в ответе нет списка groups")

    valid = set(selection.skus)
    assigned: set[str] = set()
    accepted: list[dict] = []
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        skus = _group_skus(raw.get("skus"), valid, assigned)
        description = _description(raw.get("description"))
        if len(skus) < policies.PROD_SPECS_MIN_GROUP_CARDS or not description:
            continue        # карточки такой группы уйдут в «Прочее»
        assigned.update(skus)
        accepted.append({"title": _title(raw.get("title"), len(accepted) + 1),
                         "skus": skus, "description": description,
                         "summary": _clean_text(raw.get("summary"), MAX_SUMMARY_CHARS),
                         "features_intro": raw.get("features_intro")})
    if not accepted:
        raise AnalysisError("нет пригодных групп")

    if len(accepted) > policies.PROD_SPECS_MAX_GROUPS:     # остаются самые крупные
        order = sorted(range(len(accepted)), key=lambda i: (-len(accepted[i]["skus"]), i))
        keep = sorted(order[:policies.PROD_SPECS_MAX_GROUPS])
        accepted = [accepted[i] for i in keep]
        assigned = {sku for group in accepted for sku in group["skus"]}

    cards = selection.cards
    sku_to_card = dict(zip(cards["sku"].astype(str), cards["card_id"].astype(str)))

    def finish(title: str, skus: list[str], description: list[str], summary: str | None,
               features_intro, other: bool) -> dict:
        members = set(skus)
        ordered = [s for s in selection.skus if s in members]       # порядок отбора
        group_attrs = [attrs_by_card[sku_to_card[s]] for s in ordered]
        shown = cards[cards["sku"].astype(str).isin(members)]
        return {"title": title, "skus": ordered, "n_cards": len(ordered),
                "summary": summary,
                "features_intro": _with_number(features_intro, len(ordered)),
                "description": description,
                "frequencies": feature_frequencies(group_attrs),
                "cards": [{"sku": str(r.sku), "name": str(r.name), "url": str(r.url)}
                          for r in shown.itertuples()],
                "other": other}

    groups = [finish(g["title"], g["skus"], g["description"], g["summary"], g["features_intro"], False)
              for g in accepted]
    leftover = [s for s in selection.skus if s not in assigned]
    if leftover:
        groups.append(finish(OTHER_TITLE, leftover, [], None, None, True))

    all_attrs = [attrs_by_card[sku_to_card[s]] for s in selection.skus]
    return {"prompt_version": PROMPT_VERSION, "model": model, "n_cards": selection.n_cards,
            "grouping_note": _clean_text(data.get("grouping_note"), MAX_NOTE_CHARS),
            "intro": _with_number(data.get("intro"), selection.n_cards),
            "frequencies": feature_frequencies(all_attrs), "groups": groups}


def analyze_selection(selection: Selection, attrs_by_card: dict[str, dict],
                      call_llm: Callable[..., dict]) -> dict:
    """Шаг 2 для одной страты: запрос модели через call_llm (CoreClient.llm) и разбор ответа.
    У каждой отобранной карточки должны быть признаки - иначе AnalysisError без вызова модели."""
    missing = [str(c) for c in selection.cards["card_id"] if str(c) not in attrs_by_card]
    if missing:
        raise AnalysisError(f"нет признаков у карточек: {len(missing)}")
    response = call_llm(system_prompt(), user_prompt(selection, attrs_by_card),
                        ref_type="stratum", ref_id=selection.stratum_id,
                        max_tokens=policies.PROD_SPECS_ANALYSIS_MAX_TOKENS)
    return build_result(response.get("content"), selection, attrs_by_card, response.get("model"))