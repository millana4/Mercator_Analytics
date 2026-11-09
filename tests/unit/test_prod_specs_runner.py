# ---------- Рекомендации по закупкам: запуск по стратам (подмена коннектора, ядра и хранилища) ----------
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from app.config import policies
from app.connectors import schema
from app.core_api.client import CoreLlmError, CoreUnavailable
from app.prod_specs.analysis import PROMPT_VERSION
from app.prod_specs.runner import (Analysis, UnknownStratum, decide, list_strata, markdown_table,
                                   relabel, resolve_strata, run_prod_specs, stratum_label,
                                   subcategory_label)
from app.prod_specs.stratum_selection import selection_hash

SET_ID = "set-1"
A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
NOW = datetime(2026, 11, 8, 4, 0)


class FakeConnector:
    """Сет в двух стратах: A - n_a подходящих карточек (sku 100...), B - n_b (sku 200...)."""

    def __init__(self, n_a: int = 12, n_b: int = 11, status: str = "active",
                 category: str = "kids_clothing") -> None:
        self.category = category
        rows = []
        for stratum, first, n in ((A, 100, n_a), (B, 200, n_b)):
            for i in range(n):
                sku = str(first + i)
                rows.append({"stratum_id": stratum, "card_id": f"card-{sku}", "sku": sku,
                             "name": f"Худи {sku}", "url": f"https://ozon/{sku}"})
        self.candidates = pd.DataFrame(rows, columns=["stratum_id", "card_id", "sku", "name", "url"])
        self.status = status
        self.labeled: set[str] = set()      # card_id с признаками

    def get_sets(self):
        base = {"marketplace": "ozon", "category": self.category, "geo": "СПб"}
        return pd.DataFrame([{"set_id": SET_ID, "status": self.status, **base},
                             {"set_id": "old-set", "status": "retired", **base}])

    def get_strata(self, set_id):
        return pd.DataFrame({"stratum_id": [A, B], "subcategory": ["Худи", "Свитшоты"],
                             "gender": ["f", "m"], "item": ["Худи", "Свитшот"]})

    def get_prod_specs_candidates(self, set_id):
        return self.candidates

    def get_search_ranking(self, set_id, date_from):
        return schema.empty(schema.SEARCH_RANKING)

    def get_reviews(self, skus):
        return pd.DataFrame({"sku": list(skus), "reviews_count": [int(s) for s in skus]})

    def get_prod_specs_cards(self, card_ids):
        ids = [c for c in card_ids if c in self.labeled]
        return pd.DataFrame({"card_id": ids, "prompt_version": ["v1"] * len(ids),
                             "attrs": [{"garment_type": "худи"}] * len(ids)})


class FakeCore:
    def __init__(self, connector: FakeConnector, label_immediately: bool = True) -> None:
        self.connector = connector
        self.label_immediately = label_immediately
        self.label_calls: list[tuple[str, list[str]]] = []
        self.llm_calls: list[dict] = []
        self.llm_errors: list[Exception] = []

    def label_cards(self, marketplace, skus):
        self.label_calls.append((marketplace, list(skus)))
        if self.label_immediately:
            self.connector.labeled.update(f"card-{s}" for s in skus)
        return {"requested": len(skus), "found": len(skus), "accepted": len(skus), "missing": []}

    def llm(self, system, user, **kwargs):
        self.llm_calls.append({"system": system, "user": user, **kwargs})
        if self.llm_errors:
            raise self.llm_errors.pop(0)
        skus = re.findall(r"Артикул (\d+)", user)
        group = {"title": "Худи", "skus": skus, "description": ["Фасон: прямой"]}
        return {"content": json.dumps({"groups": [group]}, ensure_ascii=False), "model": "qwen-test"}


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, Analysis]] = {}     # stratum_id -> (set_id, анализ)
        self.saved: list[dict] = []
        self.labels: dict[str, str] = {}                    # stratum_id -> подпись

    def get(self, stratum_id):
        row = self.rows.get(stratum_id)
        return row[1] if row else None

    def find_source(self, selection_hash_):
        for _, analysis_ in self.rows.values():
            if analysis_.selection_hash == selection_hash_:
                return analysis_
        return None

    def save(self, record):
        self.saved.append(record)
        self.labels[record["stratum_id"]] = record["stratum_label"]
        self.rows[record["stratum_id"]] = (record["set_id"], Analysis(
            stratum_id=record["stratum_id"], selection_hash=record["selection_hash"],
            prompt_version=record["result"].get("prompt_version"), model=record["model"],
            result=record["result"]))

    def set_label(self, stratum_id, label):
        if stratum_id not in self.rows:
            return False
        changed = self.labels.get(stratum_id) != label
        self.labels[stratum_id] = label
        return changed

    def delete_not_in_sets(self, set_ids):
        gone = [s for s, (set_id, _) in self.rows.items() if set_id not in set_ids]
        for stratum_id in gone:
            del self.rows[stratum_id]
        return len(gone)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


def run(connector, core, store, clock=None, **kwargs):
    clock = clock or Clock()
    kwargs.setdefault("wait_hours", 0.1)        # 360 с
    kwargs.setdefault("poll_seconds", 300)
    return run_prod_specs(connector=connector, core=core, store=store, sleep=clock.sleep,
                          clock=clock.time, now=NOW, **kwargs)


def analysis(h="h", version=PROMPT_VERSION, stratum="s-old", result=None, model="m") -> Analysis:
    return Analysis(stratum_id=stratum, selection_hash=h, prompt_version=version, model=model,
                    result=result if result is not None else {})


A_SKUS = [str(100 + i) for i in range(12)]


# ---------- Вспомогательные функции ----------

def test_resolve_prefix_and_full_id():
    known = {A: "а", B: "б"}

    assert resolve_strata([A[:8]], known) == [A]
    assert resolve_strata([B], known) == [B]
    assert resolve_strata([A[:8].upper()], known) == [A]


def test_resolve_comma_separated_and_deduped():
    known = {A: "а", B: "б"}

    assert resolve_strata([f"{A[:8]}, {B[:8]}", A], known) == [A, B]


def test_resolve_rejects_short_unknown_and_ambiguous():
    known = {A: "а", "aaaaaaaa-1111-0000-0000-000000000009": "в"}

    with pytest.raises(UnknownStratum, match="короче"):
        resolve_strata(["aaaa"], known)
    with pytest.raises(UnknownStratum, match="не найдена"):
        resolve_strata(["deadbeef"], known)
    with pytest.raises(UnknownStratum, match="нескольким"):
        resolve_strata(["aaaaaaaa"], known)


def test_subcategory_label_matches_dashboard_format():
    assert subcategory_label("Блузы и рубашки", "f", 1) == "Блузы и рубашки, девочки"
    assert subcategory_label("Рубашки", "m", 1) == "Рубашки, мальчики"
    assert subcategory_label("Боди и песочники", "baby", 1) == "Боди и песочники, малыши"
    assert subcategory_label("Боди", "b", 1) == "Боди, малыши"
    assert subcategory_label("Брюки", "u", 1) == "Брюки (u, слой 1)"     # как ключ среза
    assert subcategory_label("Брюки", "u", None) == "Брюки (u)"
    assert subcategory_label("Брюки", None, 1) == "Брюки"
    assert subcategory_label("Брюки", pd.NA, pd.NA) == "Брюки"


def test_stratum_label_adds_item_after_subcategory():
    assert stratum_label("f", "Блузы и рубашки", "Рубашка", 1) == "Блузы и рубашки, девочки: Рубашка"
    assert stratum_label("f", "Худи", "Худи", 2) == "Худи, девочки"
    assert stratum_label("baby", "Комбинезоны", "Комбинезон", 1) == "Комбинезоны, малыши: Комбинезон"
    assert stratum_label("m", "Брюки", None, 1) == "Брюки, мальчики"


def test_markdown_table():
    table = markdown_table(["Имя", "Число"], [["а|б", 1], ["в", 2]])

    assert table.splitlines() == ["| Имя | Число |", "|---|---|", "| а\\|б | 1 |", "| в | 2 |"]


SEL = SimpleNamespace(selection_hash="h")


def test_decide_skip_when_same_composition_whatever_the_prompt_version():
    assert decide(SEL, analysis(stratum="s"), None, False) == "skip"
    assert decide(SEL, analysis(stratum="s"), analysis(), False) == "skip"
    assert decide(SEL, analysis(version="v0"), None, False) == "skip"


def test_decide_copy_when_source_exists():
    assert decide(SEL, None, analysis(), False) == "copy"
    assert decide(SEL, analysis(h="other"), analysis(), False) == "copy"


def test_decide_analyze_otherwise():
    assert decide(SEL, None, None, False) == "analyze"
    assert decide(SEL, analysis(h="other"), None, False) == "analyze"


def test_decide_force():
    assert decide(SEL, analysis(), analysis(), True) == "analyze"


# ---------- Запуск ----------

def test_single_stratum_runs_only_that_one():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["analyzed"] == 1
    assert list(store.rows) == [A]
    assert len(core.label_calls) == 1
    assert core.label_calls[0][0] == "ozon"
    assert sorted(core.label_calls[0][1]) == A_SKUS
    assert len(core.llm_calls) == 1
    record = store.saved[0]
    assert record["n_cards"] == 12
    assert record["stratum_label"] == "Худи, девочки"
    assert record["selection_hash"] == selection_hash(A_SKUS)
    assert record["copied_from_stratum"] is None
    assert record["result"]["groups"][0]["title"] == "Худи"
    assert record["model"] == "qwen-test"


def test_second_run_changes_nothing():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    run(connector, core, store, only_strata=[A[:8]])

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["skipped"] == 1
    assert len(core.label_calls) == 1
    assert len(core.llm_calls) == 1
    assert len(store.saved) == 1


def test_full_run_covers_all_strata_and_cleans_inactive_sets():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    store.rows["old-stratum"] = ("old-set", analysis(h="x", stratum="old-stratum"))

    stats = run(connector, core, store)

    assert stats["analyzed"] == 2
    assert stats["removed"] == 1
    assert set(store.rows) == {A, B}
    assert len(core.label_calls) == 1 and len(core.label_calls[0][1]) == 23


def test_single_stratum_run_keeps_old_records():
    connector = FakeConnector()
    store = FakeStore()
    store.rows["old-stratum"] = ("old-set", analysis(h="x", stratum="old-stratum"))

    stats = run(connector, FakeCore(connector), store, only_strata=[A[:8]])

    assert "old-stratum" in store.rows
    assert stats["removed"] == 0


def test_copy_from_other_stratum_without_model_calls():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    old_result = {"prompt_version": PROMPT_VERSION, "groups": ["x"], "n_cards": 12}
    store.rows["old-stratum"] = ("old-set", analysis(
        h=selection_hash(A_SKUS), stratum="old-stratum", result=old_result, model="old-model"))

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["copied"] == 1
    assert core.llm_calls == [] and core.label_calls == []
    record = store.saved[0]
    assert record["copied_from_stratum"] == "old-stratum"
    assert record["result"]["groups"] == ["x"]
    assert record["model"] == "old-model"
    assert record["stratum_id"] == A and record["set_id"] == SET_ID


def test_copy_does_not_depend_on_prompt_version():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    store.rows["old-stratum"] = ("old-set", analysis(
        h=selection_hash(A_SKUS), version="v0", stratum="old-stratum", result={"prompt_version": "v0"}))

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["copied"] == 1
    assert core.llm_calls == [] and core.label_calls == []


def test_labeled_cards_are_not_resent():
    connector = FakeConnector()
    connector.labeled = {f"card-{100 + i}" for i in range(4)}
    core, store = FakeCore(connector), FakeStore()

    run(connector, core, store, only_strata=[A[:8]])

    assert sorted(core.label_calls[0][1]) == [str(100 + i) for i in range(4, 12)]
    assert len(core.llm_calls) == 1


def test_waits_until_cards_are_labeled():
    connector = FakeConnector()
    core, store, clock = FakeCore(connector, label_immediately=False), FakeStore(), Clock()
    clock.on_sleep = lambda: connector.labeled.update(connector.candidates["card_id"])

    stats = run(connector, core, store, clock, only_strata=[A[:8]])

    assert clock.sleeps == [300]
    assert stats["analyzed"] == 1


def test_timeout_with_enough_labeled_cards_gives_partial_analysis():
    connector = FakeConnector()
    connector.labeled = {f"card-{100 + i}" for i in range(2, 12)}      # 10 из 12
    core, store, clock = FakeCore(connector, label_immediately=False), FakeStore(), Clock()

    stats = run(connector, core, store, clock, only_strata=[A[:8]])

    assert sorted(core.label_calls[0][1]) == ["100", "101"]
    assert clock.sleeps == [300, 300]
    assert stats["partial"] == 1
    record = store.saved[0]
    assert record["n_cards"] == 10
    assert record["selection_hash"] == selection_hash([str(102 + i) for i in range(10)])
    assert len(re.findall("Артикул", core.llm_calls[0]["user"])) == 10


def test_timeout_with_too_few_labeled_cards_does_nothing():
    connector = FakeConnector()
    connector.labeled = {f"card-{100 + i}" for i in range(3, 12)}      # 9 из 12
    core, store = FakeCore(connector, label_immediately=False), FakeStore()

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["not_ready"] == 1
    assert store.saved == [] and core.llm_calls == []


def test_llm_error_is_retried():
    connector = FakeConnector()
    core, store, clock = FakeCore(connector), FakeStore(), Clock()
    core.llm_errors = [CoreLlmError("TIMEOUT", "таймаут"), CoreUnavailable("недоступно")]

    stats = run(connector, core, store, clock, only_strata=[A[:8]])

    assert len(core.llm_calls) == 3
    assert clock.sleeps == [policies.PROD_SPECS_LLM_RETRY_PAUSE_SECONDS] * 2
    assert stats["analyzed"] == 1


def test_llm_fails_after_all_attempts():
    connector = FakeConnector()
    core, store, clock = FakeCore(connector), FakeStore(), Clock()
    core.llm_errors = [CoreLlmError("OTHER", "сбой")] * policies.PROD_SPECS_LLM_ATTEMPTS

    stats = run(connector, core, store, clock, only_strata=[A[:8]])

    assert stats["failed"] == 1
    assert store.saved == []
    assert len(clock.sleeps) == policies.PROD_SPECS_LLM_ATTEMPTS - 1


def test_auth_error_aborts_whole_run():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    core.llm_errors = [CoreLlmError("AUTH", "нет ключей")]

    stats = run(connector, core, store)

    assert stats["aborted"] == 1
    assert store.saved == []
    assert len(core.llm_calls) == 1          # вторая страта не пробовалась


def test_dry_run_changes_nothing(capsys):
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()

    stats = run(connector, core, store, dry_run=True)

    assert stats["to_analyze"] == 2
    assert stats["to_label"] == 23
    assert core.label_calls == [] and core.llm_calls == []
    assert store.saved == []
    out = capsys.readouterr().out
    assert "Худи, девочки" in out and "анализ" in out


def test_force_reanalyzes_same_composition():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    run(connector, core, store, only_strata=[A[:8]])

    stats = run(connector, core, store, only_strata=[A[:8]], force=True)

    assert stats["analyzed"] == 1
    assert len(core.llm_calls) == 2
    assert len(core.label_calls) == 1          # карточки уже с признаками


def test_stratum_with_too_few_cards_is_skipped():
    connector = FakeConnector(n_a=9)
    core, store = FakeCore(connector), FakeStore()

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["too_few"] == 1
    assert store.saved == [] and core.label_calls == []


def test_unknown_stratum_raises():
    connector = FakeConnector()

    with pytest.raises(UnknownStratum):
        run(connector, FakeCore(connector), FakeStore(), only_strata=["deadbeef"])


def test_no_active_sets_changes_nothing():
    connector = FakeConnector(status="retired")
    store = FakeStore()
    store.rows["old-stratum"] = ("old-set", analysis(stratum="old-stratum"))

    stats = run(connector, FakeCore(connector), store)

    assert not stats
    assert "old-stratum" in store.rows


# ---------- Категории ----------

def test_disabled_category_is_not_processed(monkeypatch):
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("clothing",))
    connector = FakeConnector()                       # сет детской одежды
    core, store = FakeCore(connector), FakeStore()

    stats = run(connector, core, store)

    assert not stats.get("analyzed")
    assert core.label_calls == [] and core.llm_calls == [] and store.saved == []
    assert list_strata(connector=connector, store=store, now=NOW) == []


def test_disabled_category_stratum_cannot_be_requested(monkeypatch):
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("clothing",))
    connector = FakeConnector()

    with pytest.raises(UnknownStratum, match="PROD_SPECS_CATEGORIES"):
        run(connector, FakeCore(connector), FakeStore(), only_strata=[A[:8]])


def test_disabled_category_keeps_existing_results(monkeypatch):
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("clothing",))
    connector = FakeConnector()
    store = FakeStore()
    store.rows[A] = (SET_ID, analysis(h="x", stratum=A))

    run(connector, FakeCore(connector), store)

    assert A in store.rows          # выключение категории готовые результаты не стирает


def test_second_category_is_enabled_by_setting(monkeypatch):
    connector = FakeConnector(category="clothing")
    core, store = FakeCore(connector), FakeStore()

    assert run(connector, core, store)["analyzed"] == 0     # по умолчанию только детская одежда
    monkeypatch.setattr(policies, "PROD_SPECS_CATEGORIES", ("kids_clothing", "clothing"))
    stats = run(connector, core, store)

    assert stats["analyzed"] == 2
    assert {r["category"] for r in store.saved} == {"clothing"}


# ---------- Подписи страт ----------

def test_relabel_updates_existing_labels_only():
    connector = FakeConnector()
    store = FakeStore()
    store.rows[A] = (SET_ID, analysis(h="x", stratum=A))
    store.labels[A] = "девочки, Худи"                 # подпись прежнего формата

    changed = relabel(connector=connector, store=store)

    assert changed == 1
    assert store.labels[A] == "Худи, девочки"
    assert B not in store.labels                       # у страты без анализа подписи нет
    assert relabel(connector=connector, store=store) == 0


def test_skipped_stratum_gets_new_label():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    run(connector, core, store, only_strata=[A[:8]])
    store.labels[A] = "девочки, Худи"

    stats = run(connector, core, store, only_strata=[A[:8]])

    assert stats["skipped"] == 1
    assert store.labels[A] == "Худи, девочки"


def test_list_strata_shows_state():
    connector = FakeConnector()
    core, store = FakeCore(connector), FakeStore()
    run(connector, core, store, only_strata=[A[:8]])

    rows = list_strata(connector=connector, store=store, now=NOW)

    by_id = {r["stratum_id"]: r for r in rows}
    assert by_id[A]["label"] == "Худи, девочки"
    assert (by_id[A]["suitable"], by_id[A]["selected"], by_id[A]["labeled"]) == (12, 12, 12)
    assert by_id[A]["state"] == "актуален"
    assert (by_id[B]["suitable"], by_id[B]["selected"], by_id[B]["labeled"]) == (11, 11, 0)
    assert by_id[B]["state"] == "нет"


def test_main_passes_wait_and_poll_options(monkeypatch):
    from collections import Counter

    import app.prod_specs.runner as runner_module
    captured = {}
    monkeypatch.setattr(runner_module, "run_prod_specs",
                        lambda **kwargs: captured.update(kwargs) or Counter())

    code = runner_module.main(["--stratum", A[:8], "--poll-seconds", "20", "--wait-hours", "0.5"])

    assert code == 0
    assert captured["poll_seconds"] == 20
    assert captured["wait_hours"] == 0.5
    assert captured["only_strata"] == [A[:8]]