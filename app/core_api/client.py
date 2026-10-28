# ---------- Клиент HTTP API ядра: задания товарных ниш ----------
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx

from app.config.settings import get_settings


class CoreApiError(Exception):
    """Базовая ошибка обращения к ядру."""


class CoreUnavailable(CoreApiError):
    """Ядро недоступно или ответило ошибкой сервера — задание досылается позже."""


class CoreRejected(CoreApiError):
    """Ядро отклонило задание (400) — причину показываем пользователю."""


class CoreClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout

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

    def admin_request(self, method: str, path: str, params: dict | None,
                      content: bytes | None) -> httpx.Response:
        """Запрос к админскому API ядра для шлюза: метод, параметры и тело (JSON) как есть.
        Ответ ядра возвращается целиком; ошибка связи - CoreUnavailable."""
        headers = {"Content-Type": "application/json"} if content else None
        return self._request(method, path, params=params, content=content,
                             extra_headers=headers)

    def _request(self, method: str, path: str, extra_headers: dict | None = None,
                 **kwargs: Any) -> httpx.Response:
        try:
            # trust_env=False: напрямую, без системного прокси
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                return client.request(method, self._base_url + path,
                                      headers={**self._headers, **(extra_headers or {})},
                                      **kwargs)
        except httpx.HTTPError as e:
            raise CoreUnavailable(f"{type(e).__name__}: {e}") from e

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        try:
            return response.json().get("error") or response.text
        except ValueError:
            return response.text


@lru_cache
def get_core_client() -> CoreClient:
    s = get_settings()
    return CoreClient(s.core_api_url, s.service_api_key)