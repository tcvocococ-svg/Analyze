"""
Telegram-бот для отслеживания цен на товары Ozon.

Добавить товар можно двумя способами:
  - просто прислать ссылку на товар сообщением (без команды);
  - или командой /add <ссылка>.

Остальные команды:
  /start           — приветствие и список команд
  /list            — список добавленных товаров с последней известной ценой
  /price <id>      — получить СВЕЖУЮ цену прямо сейчас (id — из /list)
  /history <id>    — история изменения цены товара
  /remove <id>     — удалить товар из списка

Раз в сутки бот в фоне обновляет цены всех товаров (чтобы /list показывал
не совсем старые данные), но никого не уведомляет — по вашему запросу бот
просто показывает цену, когда вы её спрашиваете.

Запуск:
  1) pip install -r requirements.txt
  2) python -m playwright install chromium   (нужно один раз)
  3) export TELEGRAM_BOT_TOKEN="ваш_токен_от_BotFather"
  4) python bot.py
"""
import logging
import os
import re

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import storage
from scraper import get_price

OZON_URL_RE = re.compile(r"https?://(?:www\.)?ozon\.ru\S*")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DAILY_REFRESH_SECONDS = 24 * 60 * 60


def format_price(price: float | None) -> str:
    if price is None:
        return "не удалось получить цену"
    return f"{price:,.0f} ₽".replace(",", " ")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я слежу за ценами на товары Ozon.\n\n"
        "Чтобы добавить товар, просто пришлите мне ссылку на него "
        "(можно без команды) — или используйте команды ниже:\n\n"
        "/add <ссылка на товар> — добавить товар\n"
        "/list — список ваших товаров и последние известные цены\n"
        "/price <id> — узнать текущую цену прямо сейчас\n"
        "/history <id> — история изменения цены\n"
        "/remove <id> — удалить товар\n\n"
        "Цены в списке фоном обновляются раз в сутки, "
        "а /price всегда проверяет актуальную цену на сайте."
    )


async def add_tracked_url(update: Update, url: str) -> None:
    """Общая логика добавления товара — используется и командой /add,
    и обработчиком обычных сообщений со ссылкой."""
    await update.message.reply_text("Проверяю цену, подождите несколько секунд…")
    result = await get_price(url)

    item_id = storage.add_item(
        user_id=update.effective_user.id,
        url=url,
        name=result.name,
        price=result.price,
    )

    title = result.name or url
    await update.message.reply_text(
        f"Добавлено (id {item_id}): {title}\n"
        f"Текущая цена: {format_price(result.price)}"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Использование: /add <ссылка на товар Ozon> "
            "(или просто пришлите ссылку сообщением, без команды)"
        )
        return

    url = context.args[0]
    if "ozon.ru" not in url:
        await update.message.reply_text(
            "Похоже, это не ссылка на Ozon. Пришлите ссылку вида https://www.ozon.ru/product/..."
        )
        return

    await add_tracked_url(update, url)


async def handle_plain_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Если прислали просто ссылку без команды — добавляем товар сами.
    Можно прислать и несколько ссылок в одном сообщении, добавим все."""
    text = update.message.text or ""
    urls = OZON_URL_RE.findall(text)
    if not urls:
        await update.message.reply_text(
            "Не нашёл ссылку на ozon.ru в сообщении. "
            "Пришлите ссылку вида https://www.ozon.ru/product/... "
            "или посмотрите /start."
        )
        return

    for url in urls:
        await add_tracked_url(update, url)


async def list_items(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = storage.list_items(update.effective_user.id)
    if not rows:
        await update.message.reply_text("Список пуст. Добавьте товар через /add <ссылка>.")
        return

    lines = []
    for row in rows:
        title = row["name"] or row["url"]
        lines.append(
            f"id {row['id']}: {title}\n"
            f"   цена: {format_price(row['last_price'])} "
            f"(обновлено: {row['last_checked'] or '—'})"
        )
    await update.message.reply_text("\n".join(lines))


async def price_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /price <id> (id смотрите в /list)")
        return

    item_id = int(context.args[0])
    row = storage.get_item(item_id, update.effective_user.id)
    if row is None:
        await update.message.reply_text("Товар с таким id не найден в вашем списке.")
        return

    await update.message.reply_text("Проверяю актуальную цену…")
    result = await get_price(row["url"])
    storage.update_price(item_id, result.price)

    title = result.name or row["name"] or row["url"]
    await update.message.reply_text(f"{title}\nТекущая цена: {format_price(result.price)}")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /history <id> (id смотрите в /list)")
        return

    item_id = int(context.args[0])
    row = storage.get_item(item_id, update.effective_user.id)
    if row is None:
        await update.message.reply_text("Товар с таким id не найден в вашем списке.")
        return

    entries = storage.get_history(item_id)
    if not entries:
        await update.message.reply_text("Истории пока нет.")
        return

    title = row["name"] or row["url"]
    lines = [f"История цены: {title}\n"]
    # Записи приходят от новых к старым — переворачиваем, чтобы читать сверху вниз по времени.
    for entry in reversed(entries):
        date_str = entry["checked_at"][:16].replace("T", " ")
        lines.append(f"{date_str} — {format_price(entry['price'])}")
    await update.message.reply_text("\n".join(lines))


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /remove <id> (id смотрите в /list)")
        return

    item_id = int(context.args[0])
    ok = storage.remove_item(item_id, update.effective_user.id)
    if ok:
        await update.message.reply_text(f"Товар id {item_id} удалён.")
    else:
        await update.message.reply_text("Товар с таким id не найден в вашем списке.")


async def daily_refresh(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фоновая задача: раз в сутки обновляет сохранённые цены всех товаров."""
    rows = storage.all_items()
    logger.info("Ежедневное обновление цен: %d товаров", len(rows))
    for row in rows:
        try:
            result = await get_price(row["url"])
            storage.update_price(row["id"], result.price)
        except Exception:
            logger.exception("Не удалось обновить цену для id=%s", row["id"])


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Переменная окружения TELEGRAM_BOT_TOKEN не задана. "
            "Получите токен у @BotFather и экспортируйте его перед запуском."
        )

    storage.init_db()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_items))
    app.add_handler(CommandHandler("price", price_now))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_plain_link))

    app.job_queue.run_repeating(daily_refresh, interval=DAILY_REFRESH_SECONDS, first=DAILY_REFRESH_SECONDS)

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
