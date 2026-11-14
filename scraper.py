"""
Получение цены товара со страницы Ozon.

ВАЖНО: Ozon активно противодействует автоматическому сбору данных
(меняет вёрстку, подключает защиту от ботов, требует JS). Поэтому:
  1) Этот модуль сначала пытается достать цену "дёшево" — из сырого HTML
     (JSON-LD/встроенные данные), без браузера.
  2) Если не вышло — запускает headless-браузер (Playwright) и рендерит
     страницу как настоящий пользователь.
Оба способа могут со временем сломаться, если Ozon изменит структуру
страницы — в таком случае нужно будет обновить селекторы/регулярки ниже.
Также стоит свериться с пользовательским соглашением Ozon на предмет
допустимости автоматического обращения к сайту.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class PriceResult:
    price: Optional[float]
    name: Optional[str]
    raw_source: str  # "html" или "browser", для отладки


async def _try_from_raw_html(url: str) -> Optional[PriceResult]:
    """Быстрая попытка без браузера: ищем JSON-LD или цену прямо в HTML."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    html = resp.text

    # 1) Пытаемся найти блок JSON-LD с разметкой Product/Offer
    for match in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if isinstance(offers, dict) and "price" in offers:
                try:
                    price = float(offers["price"])
                    return PriceResult(price=price, name=item.get("name"), raw_source="html")
                except (TypeError, ValueError):
                    pass

    # 2) Запасной вариант: ищем числовое поле вида "cardPrice":"1 234 ₽"
    # или "price":123456 где-то во встроенном состоянии страницы.
    m = re.search(r'"cardPrice"\s*:\s*"([\d\s]+)', html)
    if m:
        price_str = m.group(1).replace("\xa0", "").replace(" ", "")
        if price_str.isdigit():
            return PriceResult(price=float(price_str), name=None, raw_source="html")

    return None


async def _try_from_browser(url: str) -> Optional[PriceResult]:
    """Более надёжная, но тяжёлая попытка — реальный рендеринг страницы."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT, locale="ru-RU")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Ozon подгружает цену асинхронно — дадим странице немного времени.
            await page.wait_for_timeout(3000)

            name = None
            try:
                name = await page.locator("h1").first.text_content(timeout=5000)
            except Exception:
                pass

            price_text = None
            # Селекторы Ozon часто меняются — это лишь один из рабочих на
            # момент написания. Если цена перестанет находиться, нужно
            # открыть страницу товара, найти блок с ценой через
            # "Просмотр кода" и обновить селектор ниже.
            for selector in [
                "[data-widget='webPrice'] span",
                "[data-widget='webOfferPrice'] span",
            ]:
                try:
                    price_text = await page.locator(selector).first.text_content(timeout=5000)
                    if price_text:
                        break
                except Exception:
                    continue

            price = None
            if price_text:
                digits = re.sub(r"[^\d]", "", price_text)
                if digits:
                    price = float(digits)

            if price is None:
                return None
            return PriceResult(price=price, name=(name or "").strip() or None, raw_source="browser")
        finally:
            await browser.close()


async def get_price(url: str) -> PriceResult:
    """Главная функция: возвращает PriceResult(price=None, ...), если цену
    получить не удалось (например, товар недоступен или Ozon заблокировал
    запрос)."""
    result = await _try_from_raw_html(url)
    if result and result.price is not None:
        return result

    result = await _try_from_browser(url)
    if result:
        return result

    return PriceResult(price=None, name=None, raw_source="none")


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not test_url:
        print("Использование: python scraper.py <ссылка на товар Ozon>")
    else:
        res = asyncio.run(get_price(test_url))
        print(res)
