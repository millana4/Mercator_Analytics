# ---------- Клиент ядра: разметка карточек и вызов модели (рекомендации по закупкам) ----------
import httpx
import pytest

from app.core_api.client import (CoreClient, CoreLlmError, CoreRejected, CoreUnavailable,
                                 LABEL_BATCH)


class Recorder:
    """Подмена CoreClient._request: записывает вызовы, отдаёт заготовленные ответы по очереди."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, path, extra_headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "path": path, "timeout": timeout, **kwargs})
        return self.responses.pop(0)


def client_with(monkeypatch, *responses: httpx.Response) -> tuple[CoreClient, Recorder]:
    client = CoreClient("http://core", "key", llm_timeout=150.0)
    recorder = Recorder(*responses)
    monkeypatch.setattr(client, "_request", recorder)
    return client, recorder


def accepted(requested: int, found: int, accepted_count: int, missing=()) -> httpx.Response:
    return httpx.Response(202, json={"requested": requested, "found": found,
                                     "accepted": accepted_count, "missing": list(missing)})


# ---------- Разметка карточек ----------

def test_label_cards_posts_and_returns_counts(monkeypatch):
    client, rec = client_with(monkeypatch, accepted(3, 2, 2, ["333"]))

    result = client.label_cards("ozon", ["111", "222", "333"])

    assert result == {"requested": 3, "found": 2, "accepted": 2, "missing": ["333"]}
    assert len(rec.calls) == 1
    assert rec.calls[0]["method"] == "POST"
    assert rec.calls[0]["path"] == "/api/v1/prod_specs/cards"
    assert rec.calls[0]["json"] == {"marketplace": "ozon", "skus": ["111", "222", "333"]}


def test_label_cards_cleans_duplicates_and_blanks(monkeypatch):
    client, rec = client_with(monkeypatch, accepted(2, 2, 2))

    client.label_cards("ozon", [" 111 ", "222", "111", "", "  "])

    assert rec.calls[0]["json"]["skus"] == ["111", "222"]


def test_label_cards_empty_makes_no_request(monkeypatch):
    client, rec = client_with(monkeypatch)

    result = client.label_cards("ozon", [])

    assert result == {"requested": 0, "found": 0, "accepted": 0, "missing": []}
    assert rec.calls == []


def test_label_cards_splits_into_batches(monkeypatch):
    skus = [str(i) for i in range(2 * LABEL_BATCH + 50)]
    client, rec = client_with(monkeypatch,
                              accepted(LABEL_BATCH, LABEL_BATCH, LABEL_BATCH),
                              accepted(LABEL_BATCH, LABEL_BATCH - 1, LABEL_BATCH - 1, ["x"]),
                              accepted(50, 50, 40))

    result = client.label_cards("ozon", skus)

    assert [len(c["json"]["skus"]) for c in rec.calls] == [LABEL_BATCH, LABEL_BATCH, 50]
    assert result["requested"] == 450
    assert result["found"] == LABEL_BATCH + LABEL_BATCH - 1 + 50
    assert result["accepted"] == LABEL_BATCH + LABEL_BATCH - 1 + 40
    assert result["missing"] == ["x"]


def test_label_cards_rejected(monkeypatch):
    client, _ = client_with(monkeypatch, httpx.Response(400, json={"error": "Список sku пуст"}))

    with pytest.raises(CoreRejected, match="Список sku пуст"):
        client.label_cards("ozon", ["111"])


def test_label_cards_server_error(monkeypatch):
    client, _ = client_with(monkeypatch, httpx.Response(500, text="сбой"))

    with pytest.raises(CoreUnavailable, match="HTTP 500"):
        client.label_cards("ozon", ["111"])


def test_label_cards_unparsable_answer(monkeypatch):
    client, _ = client_with(monkeypatch, httpx.Response(202, text="не json"))

    with pytest.raises(CoreUnavailable):
        client.label_cards("ozon", ["111"])


# ---------- Вызов модели ----------

ANSWER = {"content": "ответ", "model": "qwen3.6-35b-a3b", "tokens": 1500,
          "prompt_tokens": 1200, "completion_tokens": 300}


def test_llm_returns_answer(monkeypatch):
    client, rec = client_with(monkeypatch, httpx.Response(200, json=ANSWER))

    result = client.llm("система", "данные", ref_type="stratum", ref_id="s-1", max_tokens=3000)

    assert result == ANSWER
    call = rec.calls[0]
    assert call["path"] == "/api/v1/prod_specs/llm"
    assert call["timeout"] == 150.0
    assert call["json"] == {"purpose": "prod_specs_analysis", "ref_type": "stratum", "ref_id": "s-1",
                            "system_prompt": "система", "user_prompt": "данные", "max_tokens": 3000}


def test_llm_max_tokens_only_when_given(monkeypatch):
    client, rec = client_with(monkeypatch, httpx.Response(200, json=ANSWER))

    client.llm("система", "данные")

    assert "max_tokens" not in rec.calls[0]["json"]


def test_llm_auth_error(monkeypatch):
    body = {"error": "AUTH: нет ключей", "kind": "AUTH"}
    client, _ = client_with(monkeypatch, httpx.Response(502, json=body))

    with pytest.raises(CoreLlmError, match="нет ключей") as info:
        client.llm("система", "данные")
    assert info.value.kind == "AUTH"


def test_llm_timeout_error(monkeypatch):
    body = {"error": "TIMEOUT: таймаут 120 с", "kind": "TIMEOUT"}
    client, _ = client_with(monkeypatch, httpx.Response(504, json=body))

    with pytest.raises(CoreLlmError) as info:
        client.llm("система", "данные")
    assert info.value.kind == "TIMEOUT"


def test_llm_gateway_error_without_kind_is_unavailable(monkeypatch):
    client, _ = client_with(monkeypatch, httpx.Response(502, text="Bad Gateway"))

    with pytest.raises(CoreUnavailable):
        client.llm("система", "данные")


def test_llm_rejected(monkeypatch):
    client, _ = client_with(monkeypatch, httpx.Response(400, json={"error": "Допустимое назначение"}))

    with pytest.raises(CoreRejected, match="Допустимое назначение"):
        client.llm("система", "данные")


def test_core_down_is_unavailable():
    client = CoreClient("http://127.0.0.1:1", "key", timeout=1, llm_timeout=1)

    with pytest.raises(CoreUnavailable):
        client.label_cards("ozon", ["111"])
    with pytest.raises(CoreUnavailable):
        client.llm("система", "данные")