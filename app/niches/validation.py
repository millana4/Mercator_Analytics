# ---------- Проверка ссылки на референс ----------
import re
from urllib.parse import urlsplit

from app.config.policies import MARKETPLACE_DOMAINS

# Путь карточки товара по маркетплейсам.
PRODUCT_PATHS: dict[str, re.Pattern] = {
    "ozon": re.compile(r"^/product/(?:[^/]*-)?\d+/?$"),
}


class ReferenceUrlError(ValueError):
    """Ссылка не подходит — текст ошибки показывается пользователю."""


def validate_reference_url(marketplace: str, url: str) -> None:
    """Ссылка: http(s), домен выбранного маркетплейса, путь карточки товара."""
    mp = marketplace.lower()
    domains = MARKETPLACE_DOMAINS.get(mp)
    if not domains:
        raise ReferenceUrlError(
            f"Ссылки на референс для маркетплейса «{marketplace}» пока не поддерживаются")

    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ReferenceUrlError("Некорректная ссылка: нужна ссылка, начинающаяся с http:// или https://")

    host = parts.hostname.lower()
    if not any(host == d or host.endswith("." + d) for d in domains):
        raise ReferenceUrlError(
            f"Ссылка ведёт не на {marketplace}: укажите ссылку на карточку {marketplace} "
            f"или выберите другой маркетплейс")

    pattern = PRODUCT_PATHS.get(mp)
    if pattern and not pattern.match(parts.path or ""):
        raise ReferenceUrlError(
            "Ссылка не на карточку товара: нужна полная ссылка вида "
            "https://www.ozon.ru/product/...-<артикул>/")