# ---------- Рекомендации по закупкам: недельный запуск и ручная команда ----------
# Для каждой страты активного сета: отбор карточек, поиск готового результата по отпечатку
# состава (пропуск или копия из другой страты), разметка недостающих карточек в ядре (шаг 1),
# ожидание, шаг 2 (группы и описания), запись. Ручной запуск с аргументами - см. main().
import argparse
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from app.config import policies
from app.core_api.client import (CoreApiError, CoreLlmError, CoreRejected, CoreUnavailable,
                                 get_core_client)
from app.prod_specs.analysis import AnalysisError, analyze_selection, attrs_by_card_id
from app.prod_specs.stratum_selection import (Selection, restrict_to_labeled, select_for_set,
                                              unlabeled_skus)

logger = logging.getLogger(__name__)

STATUS_SKIP, STATUS_COPY, STATUS_ANALYZE = "skip", "copy", "analyze"
ACTION_TEXT = {STATUS_SKIP: "без изменений", STATUS_COPY: "копия из другой страты",
               STATUS_ANALYZE: "анализ"}
MIN_PREFIX = 8   # короче id страты в аргументе не принимается

# Код пола в ключе среза и его подпись - как fmtLevelKey на фронтенде (utils/format.js)
GENDER_LABELS = {"f": "девочки", "m": "мальчики", "b": "малыши", "baby": "малыши"}


class UnknownStratum(Exception):
    """Страта из аргумента не найдена или указана неоднозначно."""


class StepFailed(Exception):
    """Шаг 2 по страте не удался после всех попыток."""


@dataclass(frozen=True)
class Analysis:
    """Сохранённый анализ страты (то, что нужно запуску для сравнения и копирования)."""
    stratum_id: str
    selection_hash: str
    prompt_version: str | None
    model: str | None
    result: dict
    analyzed_at: datetime | None = None


@dataclass
class SetPlan:
    set_row: dict
    labels: dict[str, str]              # все страты сета: подпись
    suitable: dict[str, int]            # подходящих карточек по стратам
    selections: dict[str, Selection]    # страты с отбором (подходящих не меньше минимума)


@dataclass
class Item:
    set_row: dict
    stratum_id: str
    label: str
    selection: Selection
    action: str = ""
    source: Analysis | None = None


# ---------- Вспомогательные ----------

def _text(value) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def subcategory_label(title, gender, layer=None) -> str:
    """Название подкатегории так же, как в блоке «Подкатегория» на дашборде (fmtLevelKey):
    «Блузы и рубашки, девочки». Пол не из списка - ключ среза как есть: «Брюки (u, слой 1)»."""
    name, code = _text(title), _text(gender)
    label = GENDER_LABELS.get(code)
    if label:
        return f"{name}, {label}"
    if not code:
        return name
    layer_text = "" if layer is None or pd.isna(layer) else f", слой {int(layer)}"
    return f"{name} ({code}{layer_text})"


def stratum_label(gender, subcategory, item, layer=None) -> str:
    """Подпись страты в списке: подкатегория как на дашборде и предмет через двоеточие
    («Блузы и рубашки, девочки: Рубашка»); предмет, совпадающий с подкатегорией, не повторяется."""
    head = subcategory_label(subcategory, gender, layer)
    name = _text(item)
    return f"{head}: {name}" if name and name != _text(subcategory) else head


def stratum_labels(connector, set_id: str) -> dict[str, str]:
    """Подписи всех страт сета."""
    strata = connector.get_strata(set_id)
    return {str(r.stratum_id): stratum_label(r.gender, r.subcategory, r.item, getattr(r, "layer", None))
            for r in strata.itertuples()}


def markdown_table(headers: list[str], rows: list[list]) -> str:
    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def resolve_strata(requested: list[str], known: dict[str, str]) -> list[str]:
    """Id страт из аргументов: целиком или первые MIN_PREFIX и больше символов
    (через запятую или повтором аргумента). Не найдена или неоднозначна - UnknownStratum."""
    out: list[str] = []
    for raw in requested:
        for part in raw.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if len(token) < MIN_PREFIX:
                raise UnknownStratum(f"Id страты «{token}» короче {MIN_PREFIX} символов")
            matches = [sid for sid in known if sid.lower().startswith(token)]
            exact = [sid for sid in matches if sid.lower() == token]
            matches = exact or matches
            if not matches:
                raise UnknownStratum(
                    f"Страта не найдена (или её категория не включена в PROD_SPECS_CATEGORIES): {token}")
            if len(matches) > 1:
                raise UnknownStratum(f"Id «{token}» подходит нескольким стратам, укажите длиннее")
            if matches[0] not in out:
                out.append(matches[0])
    return out


def decide(selection: Selection, current: Analysis | None, source: Analysis | None,
           force: bool) -> str:
    """Что делать со стратой: пропуск (состав тот же), копия результата
    из страты с тем же составом или анализ. force - всегда анализ."""
    if force:
        return STATUS_ANALYZE
    if current is not None and current.selection_hash == selection.selection_hash:
        return STATUS_SKIP
    if source is not None:
        return STATUS_COPY
    return STATUS_ANALYZE


def build_plan(connector, set_row: dict, now: datetime) -> SetPlan:
    """Подписи страт сета, число подходящих карточек и отбор по стратам."""
    set_id = str(set_row["set_id"])
    labels = stratum_labels(connector, set_id)
    candidates = connector.get_prod_specs_candidates(set_id)
    if candidates.empty:
        return SetPlan(set_row, labels, {}, {})
    candidates = candidates.drop_duplicates(["stratum_id", "card_id"])
    suitable = {str(k): int(v) for k, v in candidates.groupby("stratum_id").size().items()}
    since = now - timedelta(days=policies.PROD_SPECS_SEARCH_WINDOW_DAYS)
    ranking = connector.get_search_ranking(set_id, since)
    reviews = connector.get_reviews(sorted(candidates["sku"].astype(str).unique()))
    return SetPlan(set_row, labels, suitable, select_for_set(candidates, ranking, reviews))


def _record(item: Item, selection: Selection, result: dict, model: str | None,
            copied_from: str | None = None) -> dict:
    row = item.set_row
    return {"set_id": str(row["set_id"]), "marketplace": str(row["marketplace"]),
            "category": str(row["category"]), "geo": str(row["geo"]),
            "stratum_id": item.stratum_id, "stratum_label": item.label,
            "selection_hash": selection.selection_hash, "skus": list(selection.skus),
            "n_cards": selection.n_cards, "model": model, "result": result,
            "copied_from_stratum": copied_from}


def _card_ids(selections: list[Selection]) -> list[str]:
    return sorted({str(c) for s in selections for c in s.cards["card_id"]})


# ---------- Шаг 2 с повторами ----------

def analyze_with_retries(selection: Selection, attrs_by_card: dict[str, dict], call_llm,
                         attempts: int, pause: float, sleep) -> dict:
    """Шаг 2 до attempts попыток. Ошибка модели, непригодный ответ, недоступное ядро -
    повтор; отказ ядра (400) - сразу StepFailed; ошибка авторизации - пробрасывается."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return analyze_selection(selection, attrs_by_card, call_llm)
        except CoreLlmError as e:
            if e.kind == "AUTH":
                raise
            last = e
        except CoreRejected as e:
            raise StepFailed(f"ядро отклонило запрос: {e}") from e
        except (AnalysisError, CoreUnavailable) as e:
            last = e
        logger.warning("Шаг 2, попытка %d из %d не удалась: %s", attempt, attempts, last)
        if attempt < attempts:
            sleep(pause)
    raise StepFailed(str(last))


def _finish(item: Item, selection: Selection, attrs_all: dict, core, store, sleep,
            stats: Counter, partial: bool = False) -> None:
    try:
        result = analyze_with_retries(selection, attrs_all, core.llm,
                                      policies.PROD_SPECS_LLM_ATTEMPTS,
                                      policies.PROD_SPECS_LLM_RETRY_PAUSE_SECONDS, sleep)
    except StepFailed as e:
        logger.error("Страта «%s»: шаг 2 не удался: %s", item.label, e)
        stats["failed"] += 1
        return
    store.save(_record(item, selection, result, result.get("model")))
    stats["partial" if partial else "analyzed"] += 1
    logger.info("Страта «%s»: анализ записан (карточек %d%s)", item.label, selection.n_cards,
                ", не все размечены" if partial else "")


def _analyze_items(items: list[Item], connector, core, store, wait_seconds: float,
                   poll_seconds: float, sleep, clock, stats: Counter) -> None:
    """Разметка недостающих карточек в ядре (шаг 1), ожидание и шаг 2 по готовности."""
    attrs_all = attrs_by_card_id(connector.get_prod_specs_cards(_card_ids([i.selection for i in items])))

    to_label: dict[str, list[str]] = {}
    for item in items:
        to_label.setdefault(str(item.set_row["marketplace"]), []).extend(
            unlabeled_skus(item.selection, attrs_all))
    try:
        for marketplace, skus in to_label.items():
            if skus:
                answer = core.label_cards(marketplace, skus)
                stats["to_label"] += len(skus)
                logger.info("Разметка карточек (%s): отправлено %d, принято ядром %s, нет в ядре %d",
                            marketplace, len(skus), answer.get("accepted"), len(answer.get("missing") or []))
    except CoreApiError as e:
        logger.error("Разметка не запущена, ядро недоступно или отклонило запрос: %s", e)
        stats["aborted"] += 1
        return

    pending = {i.stratum_id: i for i in items}
    deadline = clock() + wait_seconds
    while pending:
        attrs_all = attrs_by_card_id(connector.get_prod_specs_cards(
            _card_ids([i.selection for i in pending.values()])))
        for stratum_id, item in list(pending.items()):
            if not unlabeled_skus(item.selection, attrs_all):
                _finish(item, item.selection, attrs_all, core, store, sleep, stats)
                del pending[stratum_id]
        if not pending or clock() >= deadline:
            break
        sleep(poll_seconds)

    for item in pending.values():       # время ожидания вышло
        restricted = restrict_to_labeled(item.selection, attrs_all)
        if restricted is not None and restricted.n_cards >= policies.PROD_SPECS_MIN_CARDS:
            _finish(item, restricted, attrs_all, core, store, sleep, stats, partial=True)
        else:
            stats["not_ready"] += 1
            logger.warning("Страта «%s»: за %.1f ч размечено %d из %d карточек - не анализируется",
                           item.label, wait_seconds / 3600,
                           0 if restricted is None else restricted.n_cards, item.selection.n_cards)


# ---------- Запуск ----------

def describe_plan(items: list[Item], attrs_all: dict) -> str:
    rows = [[i.label, i.selection.n_cards, ACTION_TEXT[i.action],
             len(unlabeled_skus(i.selection, attrs_all)) if i.action == STATUS_ANALYZE else 0]
            for i in items]
    return markdown_table(["Страта", "Отобрано", "Решение", "Карточек к разметке"], rows)


def run_prod_specs(only_strata: list[str] | None = None, dry_run: bool = False,
                   force: bool = False, wait_hours: float | None = None,
                   poll_seconds: float | None = None, *, connector=None, core=None, store=None,
                   sleep=time.sleep, clock=time.monotonic, now: datetime | None = None) -> Counter:
    """Анализ страт активных сетов. only_strata - только эти страты (id или префикс).
    dry_run - показать решения, ничего не отправлять и не писать. Возвращает счётчики."""
    if connector is None:
        from app.connectors.factory import get_connector
        connector = get_connector()
    if store is None:
        from app.prod_specs.store import SqlStore
        store = SqlStore()
    if core is None and not dry_run:
        core = get_core_client()
    now = now or datetime.now()
    wait_seconds = (policies.PROD_SPECS_WAIT_HOURS if wait_hours is None else wait_hours) * 3600
    poll = policies.PROD_SPECS_POLL_MINUTES * 60 if poll_seconds is None else poll_seconds
    stats: Counter = Counter()

    sets = connector.get_sets()
    active = sets[sets["status"] == "active"]
    # анализ только у включённых категорий; очистка ниже смотрит на все активные сеты,
    # чтобы выключение категории не стирало готовые результаты
    enabled = active[active["category"].isin(policies.PROD_SPECS_CATEGORIES)]
    plans = [build_plan(connector, row.to_dict(), now) for _, row in enabled.iterrows()]

    wanted: set[str] | None = None
    if only_strata:
        known = {sid: label for plan in plans for sid, label in plan.labels.items()}
        wanted = set(resolve_strata(only_strata, known))
        for plan in plans:
            for sid in wanted:
                if sid in plan.labels and sid not in plan.selections:
                    logger.info("Страта «%s»: подходящих карточек %d, меньше %d - не анализируется",
                                plan.labels[sid], plan.suitable.get(sid, 0), policies.PROD_SPECS_MIN_CARDS)
                    stats["too_few"] += 1

    items = [Item(plan.set_row, sid, plan.labels.get(sid, sid), selection)
             for plan in plans for sid, selection in plan.selections.items()
             if wanted is None or sid in wanted]
    for item in items:
        current = store.get(item.stratum_id)
        source = None if force else store.find_source(item.selection.selection_hash)
        item.action = decide(item.selection, current, source, force)
        item.source = source if item.action == STATUS_COPY else None
        stats[{STATUS_SKIP: "skipped", STATUS_COPY: "copied", STATUS_ANALYZE: "to_analyze"}[item.action]] += 1

    to_analyze = [i for i in items if i.action == STATUS_ANALYZE]
    if dry_run:
        attrs_all = attrs_by_card_id(connector.get_prod_specs_cards(
            _card_ids([i.selection for i in to_analyze])))
        stats["to_label"] = sum(len(unlabeled_skus(i.selection, attrs_all)) for i in to_analyze)
        print(describe_plan(items, attrs_all))
        return stats

    for item in items:
        if item.action == STATUS_SKIP:
            store.set_label(item.stratum_id, item.label)     # формат подписи мог измениться
        elif item.action == STATUS_COPY:
            store.save(_record(item, item.selection, dict(item.source.result), item.source.model,
                               copied_from=item.source.stratum_id))
            logger.info("Страта «%s»: состав совпал со стратой %s - результат скопирован",
                        item.label, item.source.stratum_id)

    if to_analyze:
        try:
            _analyze_items(to_analyze, connector, core, store, wait_seconds, poll, sleep, clock, stats)
        except CoreLlmError as e:
            logger.error("Остановлено: ядро не смогло обратиться к модели (%s): %s", e.kind, e)
            stats["aborted"] += 1

    if wanted is None and not stats["aborted"] and len(active):
        stats["removed"] = store.delete_not_in_sets([str(r.set_id) for r in active.itertuples()])
    logger.info("Рекомендации по закупкам: %s", dict(stats) or "изменений нет")
    return stats


def list_strata(connector=None, store=None, now: datetime | None = None) -> list[dict]:
    """Страты активных сетов и состояние анализа (для --list): ничего не меняет и не тратит."""
    if connector is None:
        from app.connectors.factory import get_connector
        connector = get_connector()
    if store is None:
        from app.prod_specs.store import SqlStore
        store = SqlStore()
    now = now or datetime.now()
    sets = connector.get_sets()
    active = sets[sets["status"] == "active"]
    plans = [build_plan(connector, row.to_dict(), now)
             for _, row in active[active["category"].isin(policies.PROD_SPECS_CATEGORIES)].iterrows()]
    attrs_all = attrs_by_card_id(connector.get_prod_specs_cards(
        _card_ids([s for p in plans for s in p.selections.values()])))

    rows: list[dict] = []
    for plan in plans:
        for sid, label in plan.labels.items():
            selection = plan.selections.get(sid)
            current = store.get(sid)
            if selection is None:
                state = "прежний (мало карточек)" if current else "—"
                selected = labeled = 0
            else:
                selected = selection.n_cards
                labeled = sum(1 for c in selection.cards["card_id"] if str(c) in attrs_all)
                if current is None:
                    state = "нет"
                elif current.selection_hash == selection.selection_hash:
                    state = "актуален"
                else:
                    state = "устарел"
            rows.append({"category": str(plan.set_row["category"]), "stratum_id": sid, "label": label,
                         "suitable": plan.suitable.get(sid, 0), "selected": selected,
                         "labeled": labeled, "state": state})
    return sorted(rows, key=lambda r: (r["category"], r["label"]))


def relabel(connector=None, store=None) -> int:
    """Обновляет подписи сохранённых анализов по текущему формату (модель не вызывается).
    Возвращает число изменённых подписей."""
    if connector is None:
        from app.connectors.factory import get_connector
        connector = get_connector()
    if store is None:
        from app.prod_specs.store import SqlStore
        store = SqlStore()
    sets = connector.get_sets()
    changed = 0
    for _, row in sets[sets["status"] == "active"].iterrows():
        for stratum_id, label in stratum_labels(connector, str(row["set_id"])).items():
            changed += int(store.set_label(stratum_id, label))
    return changed


def format_list(rows: list[dict]) -> str:
    return markdown_table(
        ["Категория", "Страта (id)", "Название", "Подходящих", "Отобрано", "С признаками", "Анализ"],
        [[r["category"], r["stratum_id"], r["label"], r["suitable"], r["selected"], r["labeled"],
          r["state"]] for r in rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.prod_specs.runner",
        description="Рекомендации по закупкам. Без аргументов - все страты активных сетов, "
                    "как недельное задание.")
    parser.add_argument("--list", action="store_true",
                        help="показать страты и состояние анализа (ничего не меняет)")
    parser.add_argument("--stratum", action="append", default=[], metavar="ID",
                        help="только эта страта: id или первые 8+ символов; можно повторять "
                             "и перечислять через запятую")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать решения по стратам, в ядро не обращаться и ничего не писать")
    parser.add_argument("--force", action="store_true",
                        help="пересчитать шаг 2, даже если состав и версия промпта не менялись")
    parser.add_argument("--wait-hours", type=float, default=None,
                        help=f"сколько ждать разметку карточек, ч (по умолчанию {policies.PROD_SPECS_WAIT_HOURS})")
    parser.add_argument("--poll-seconds", type=float, default=None,
                        help=f"как часто проверять разметку, с (по умолчанию {policies.PROD_SPECS_POLL_MINUTES * 60})")
    parser.add_argument("--relabel", action="store_true",
                        help="обновить подписи сохранённых анализов по текущему формату (без модели)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    try:
        if args.relabel:
            print(f"Подписей страт обновлено: {relabel()}")
            return 0
        if args.list:
            print(f"Категории с рекомендациями: {', '.join(policies.PROD_SPECS_CATEGORIES)}\n")
            print(format_list(list_strata()))
            return 0
        stats = run_prod_specs(only_strata=args.stratum or None, dry_run=args.dry_run,
                               force=args.force, wait_hours=args.wait_hours,
                               poll_seconds=args.poll_seconds)
    except UnknownStratum as e:
        print(f"Ошибка: {e}")
        return 2
    print("Итог: " + (", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "изменений нет"))
    return 0


if __name__ == "__main__":
    sys.exit(main())