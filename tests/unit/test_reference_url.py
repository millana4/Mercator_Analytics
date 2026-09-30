# ---------- Проверка ссылки на референс ----------
import pytest

from app.niches.validation import ReferenceUrlError, validate_reference_url


@pytest.mark.parametrize("url", [
    "https://www.ozon.ru/product/platye-dlya-devochki-123456789/",
    "https://www.ozon.ru/product/123456789",
    "http://ozon.ru/product/platye-123456789/",
    "https://m.ozon.ru/product/platye-123456789/?sh=abc",
])
def test_valid_product_url(url):
    validate_reference_url("ozon", url)


def test_marketplace_case_insensitive():
    validate_reference_url("Ozon", "https://www.ozon.ru/product/platye-123456789/")


@pytest.mark.parametrize("url, message", [
    ("ftp://www.ozon.ru/product/platye-123456789/", "Некорректная ссылка"),
    ("www.ozon.ru/product/platye-123456789/", "Некорректная ссылка"),
    ("https://www.wildberries.ru/catalog/123456789/detail.aspx", "не на ozon"),
    ("https://notozon.ru/product/platye-123456789/", "не на ozon"),
    ("https://ozon.ru.example.com/product/platye-123456789/", "не на ozon"),
    ("https://ozon.ru/t/AbCdEf1", "не на карточку товара"),
    ("https://www.ozon.ru/category/platya-dlya-devochek-7502/", "не на карточку товара"),
    ("https://www.ozon.ru/product/platye/", "не на карточку товара"),
])
def test_invalid_url(url, message):
    with pytest.raises(ReferenceUrlError, match=message):
        validate_reference_url("ozon", url)


def test_unsupported_marketplace():
    with pytest.raises(ReferenceUrlError, match="не поддерживаются"):
        validate_reference_url("wildberries", "https://www.wildberries.ru/catalog/1/detail.aspx")