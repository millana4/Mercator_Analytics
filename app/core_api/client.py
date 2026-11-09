# ---------- Клиент HTTP API ядра: задания товарных ниш ----------
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx

from app.config import policies
from app.config.settings import get_settings


class CoreApiError(Exception):
    """Базовая ошибка обращения к ядру."""


class CoreUnavailable(CoreApiError):
    """Ядро недоступно или ответило ошибкой сервера — задание досылается позже."""


class CoreRejected(CoreApiError):
    """Ядро отклонило задание (400) — причину показываем пользователю."""


class CoreLlmError(CoreApiError):
    """Ядро вызвало модель, но та не ответила или ответ непригоден.
    kind: AUTH (ключи, оплата), TIMEOUT, OTHER - как в журнале обращений ядра."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# Не больше sku за один вызов разметки: предел ядра (ProdSpecsController.MAX_SKUS).
LABEL_BATCH = 200
# Единственное назначение вызова модели, разрешённое ядром для аналитики.
LLM_PURPOSE = "prod_specs_analysis"


class CoreClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0,
                 llm_timeout: float = 150.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout
        self._llm_timeout = llm_timeout

    def create_niche(self, niche: Any) -> None:
        """Отправить задание ниши. niche — объект с полями модели Niche."""
        body = {
            "external_id": str(niche.id),
            "marketplace": niche.marketplace,
            "category": niche.category,
            "geo": niche.geo,
            "description": niche.description,
            "reference_url": niche.reference_url,
        }
        response = self._request("POST", "/api/v1/niches", json=body)
        if response.status_code == 202:
            return
        if response.status_code == 400:
            raise CoreRejected(self._error_text(response))
        raise CoreUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")

    def delete_niche(self, external_id: UUID) -> None:
        """Удалить нишу в ядре. 404 — в ядре её уже нет, это тоже успех."""
        response = self._request("DELETE", f"/api/v1/niches/{external_id}")
        if response.status_code in (200, 404):
            return
        raise CoreUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")

    def label_cards(self, marketplace: str, skus: list[str]) -> dict:
        """Поставить карточки в очередь разметки признаков в ядре (шаг 1 рекомендаций).
        Пустые sku и повторы убираются, список делится на пачки по LABEL_BATCH.
        Возвращает {requested, found, accepted, missing} суммой по пачкам."""
        unique = list(dict.fromkeys(str(s).strip() for s in skus if str(s).strip()))
        total: dict = {"requested": 0, "found": 0, "accepted": 0, "missing": []}
        for start in range(0, len(unique), LABEL_BATCH):
            batch = unique[start:start + LABEL_BATCH]
            response = self._request("POST", "/api/v1/prod_specs/cards",
                                     json={"marketplace": marketplace, "skus": batch})
            if response.status_code == 400:
                raise CoreRejected(self._error_text(response))
            if response.status_code != 202:
                raise CoreUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")
            body = self._json_object(response)
            if not body:
                raise CoreUnavailable(f"Ответ ядра не разобран: {response.text[:300]}")
            for key in ("requested", "found", "accepted"):
                total[key] += int(body.get(key, 0))
            total["missing"].extend(body.get("missing") or [])
        return total

    def llm(self, system_prompt: str, user_prompt: str, *, ref_type: str | None = None,
            ref_id: str | None = None, max_tokens: int | None = None) -> dict:
        """Вызвать модель через ядро (шаг 2 рекомендаций): ключи Yandex хранятся только в ядре.
        Возвращает {content, model, tokens, prompt_tokens, completion_tokens}.
        Модель не ответила - CoreLlmError (kind), ядро недоступно - CoreUnavailable."""
        body: dict = {"purpose": LLM_PURPOSE, "ref_type": ref_type, "ref_id": ref_id,
                      "system_prompt": system_prompt, "user_prompt": user_prompt}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        response = self._request("POST", "/api/v1/prod_specs/llm", json=body,
                                 timeout=self._llm_timeout)
        if response.status_code == 200:
            answer = self._json_object(response)
            if "content" not in answer:
                raise CoreUnavailable(f"Ответ ядра не разобран: {response.text[:300]}")
            return answer
        if response.status_code in (502, 504):
            error = self._json_object(response)
            if error.get("kind"):
                raise CoreLlmError(str(error["kind"]), str(error.get("error") or response.text[:300]))
        if response.status_code == 400:
            raise CoreRejected(self._error_text(response))
        raise CoreUnavailable(f"HTTP {response.status_code}: {response.text[:300]}")

    def admin_request(self, method: str, path: str, params: dict | None,
                      content: bytes | None) -> httpx.Response:
        """Запрос к админскому API ядра для шлюза: метод, параметры и тело (JSON) как есть.
        Ответ ядра возвращается целиком; ошибка связи - CoreUnavailable."""
        headers = {"Content-Type": "application/json"} if content else None
        return self._request(method, path, params=params, content=content,
                             extra_headers=headers)

    def _request(self, method: str, path: str, extra_headers: dict | None = None,
                 timeout: float | None = None, **kwargs: Any) -> httpx.Response:
        try:
            # trust_env=False: напрямую, без системного прокси
            with httpx.Client(timeout=timeout or self._timeout, trust_env=False) as client:
                return client.request(method, self._base_url + path,
                                      headers={**self._headers, **(extra_headers or {})},
                                      **kwargs)
        except httpx.HTTPError as e:
            raise CoreUnavailable(f"{type(e).__name__}: {e}") from e

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        """Тело ответа как словарь; не JSON или не объект - пустой словарь."""
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            return response.json().get("error") or response.text
        except ValueError:
            return response.text


@lru_cache
def get_core_client() -> CoreClient:
    s = get_settings()
    return CoreClient(s.core_api_url, s.service_api_key,
                      llm_timeout=policies.PROD_SPECS_LLM_TIMEOUT_SECONDS)