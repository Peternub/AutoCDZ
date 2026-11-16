"""Команды и меню закрытого прототипа."""

import logging
from datetime import datetime, timezone, timedelta

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)

from autocdz.storage import subscription_active

MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📚 Мои ЦДЗ"), KeyboardButton(text="🔗 Подключение МЭШ")],
    [KeyboardButton(text="⚡ Автоматизация"), KeyboardButton(text="💎 Подписка")],
    [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🆘 Поддержка")],
], resize_keyboard=True)

MESH_UNAVAILABLE = (
    "🔗 Подключение МЭШ пока недоступно.\n\n"
    "Для синхронизации требуется согласованная интеграция. "
    "Пароли и токены mos.ru в Telegram не отправляй. "
    "Бот пока не получает задания и не отправляет ответы в МЭШ."
)


def buttons(*items):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data)] for text, data in items
    ])


def display_date(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")


class PrivateAccess(BaseMiddleware):
    def __init__(self, storage):
        self.storage = storage

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        if not isinstance(message, Message) or message.chat.type != ChatType.PRIVATE:
            if isinstance(event, CallbackQuery):
                await event.answer("Открой личный чат с ботом", show_alert=True)
            return
        if not event.from_user or event.from_user.is_bot:
            return
        await self.storage.register(event.from_user.id)
        return await handler(event, data)


def create_router(config, storage):
    router = Router()
    access = PrivateAccess(storage)
    router.message.outer_middleware(access)
    router.callback_query.outer_middleware(access)

    @router.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "Привет! 👋\n\nЭто закрытый прототип AutoCDZ. "
            "Доступны настройки, тестовая подписка и управление аккаунтом.\n\n"
            "Подключение МЭШ, ИИ и автосдача ещё не работают. "
            "Платные услуги пока не продаются.\n\n"
            "Храним твой Telegram ID, дату регистрации, настройки и срок тестового доступа. "
            "Удалить эти данные: /delete_me.", reply_markup=MENU,
        )

    @router.message(Command("menu", "help"))
    async def menu(message: Message):
        await message.answer("Выбери раздел. Удаление данных: /delete_me.", reply_markup=MENU)

    @router.message(F.text == "📚 Мои ЦДЗ")
    async def assignments(message: Message):
        await message.answer(
            "📚 Заданий пока нет.\nМЭШ не подключена, синхронизация недоступна.",
            reply_markup=buttons(("Обновить список", "refresh"), ("История", "history")),
        )

    @router.message(F.text == "🔗 Подключение МЭШ")
    async def mesh(message: Message):
        await message.answer(MESH_UNAVAILABLE, reply_markup=buttons(("Отключить МЭШ", "disconnect")))

    @router.message(F.text == "⚡ Автоматизация")
    async def automation(message: Message):
        await message.answer(
            "⚡ Автоматизация отключена.\n\n"
            "Планируемые режимы: мониторинг, подготовка решений с подтверждением, автосдача. "
            "Они станут доступны после подключения интеграции. "
            "Тестовая подписка сама по себе их не включает.",
        )

    @router.message(F.text == "💎 Подписка")
    async def subscription(message: Message):
        user = await storage.user(message.from_user.id)
        status = f"Тестовый доступ до {display_date(user['subscription_until'])}." if subscription_active(user) else "Активной подписки нет."
        await message.answer(
            f"💎 {status}\n\nПродажа подписок и подтверждение платежей Tribute пока отключены. "
            "Для закрытого теста администратор может выдать доступ вручную. "
            "Оплата не требуется.",
        )

    @router.message(F.text == "⚙️ Настройки")
    async def settings(message: Message):
        user = await storage.user(message.from_user.id)
        enabled = bool(user["notifications"])
        await message.answer(
            f"⚙️ Уведомления: {'включены' if enabled else 'выключены'}.\n"
            "Настройка сохранена для будущей интеграции.\nИнтервал будущей проверки: 30 минут.",
            reply_markup=buttons(
                ("Выключить уведомления" if enabled else "Включить уведомления", f"notify:{0 if enabled else 1}"),
                ("Удалить мои данные", "delete:ask"),
            ),
        )

    @router.message(F.text == "🆘 Поддержка")
    async def support(message: Message):
        if config.support_url:
            await message.answer("🆘 Связаться с поддержкой:", reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Поддержка", url=config.support_url)]]))
        else:
            await message.answer("🆘 Контакт поддержки ещё не настроен. Обратись к владельцу тестового бота.")

    @router.message(Command("delete_me"))
    async def delete_ask(message: Message):
        await message.answer("Удалить твои настройки, тестовую подписку и данные аккаунта?",
                             reply_markup=buttons(("Да, удалить", "delete:confirm"), ("Отмена", "delete:cancel")))

    @router.message(Command("admin"))
    async def admin(message: Message):
        if message.from_user.id != config.admin_id:
            await message.answer("Доступ запрещён.")
            return
        stats = await storage.stats()
        await message.answer(
            f"Администратор\nПользователей: {stats['users']}\nАктивный тестовый доступ: {stats['active']}\n\n"
            "/grant ID ДНИ — выдать тестовый доступ (1–365 дней).\n"
            "Это не подтверждение оплаты. МЭШ и обработка заданий отключены.",
        )

    @router.message(Command("grant"))
    async def grant(message: Message):
        if message.from_user.id != config.admin_id:
            await message.answer("Доступ запрещён.")
            return
        try:
            _, user_id, days = message.text.split()
            expires = await storage.grant(int(user_id), int(days), message.from_user.id)
        except (ValueError, TypeError) as error:
            reason = str(error) if str(error).startswith(("Срок", "Пользователь")) else "Формат: /grant ID ДНИ"
            await message.answer(reason)
            return
        await message.answer(f"Тестовый доступ выдан до {display_date(expires)}.")

    @router.callback_query()
    async def callback(query: CallbackQuery):
        await query.answer()
        user_id = query.from_user.id
        if query.data in ("notify:0", "notify:1"):
            enabled = query.data == "notify:1"
            await storage.set_notifications(user_id, enabled)
            await query.message.edit_text(f"Уведомления {'включены' if enabled else 'выключены'}.")
        elif query.data == "delete:ask":
            await delete_ask(query.message)
        elif query.data == "delete:confirm":
            await storage.delete(user_id)
            await query.message.edit_text("Твои данные удалены. Повторная регистрация: /start.")
        elif query.data == "delete:cancel":
            await query.message.edit_text("Удаление отменено.")
        elif query.data == "disconnect":
            await query.message.edit_text("Аккаунт МЭШ не подключён. Токенов и паролей МЭШ у бота нет.")
        elif query.data in ("refresh", "history"):
            await query.message.answer("Заданий и истории обработки пока нет. Интеграция МЭШ недоступна.")
        else:
            await query.message.answer("Кнопка устарела. Открой /menu.")

    @router.message()
    async def unknown(message: Message):
        await message.answer("Выбери раздел в меню. Пароли и токены отправлять не нужно.", reply_markup=MENU)

    @router.errors()
    async def on_error(event: ErrorEvent):
        # Исключаем содержимое сообщений и секреты из журнала.
        logging.error("Ошибка обработки события: %s", type(event.exception).__name__)
        if event.update.message and event.update.message.chat.type == ChatType.PRIVATE:
            await event.update.message.answer("Не удалось выполнить действие. Попробуй позже.")
        elif event.update.callback_query:
            await event.update.callback_query.answer("Не удалось выполнить действие. Попробуй позже.", show_alert=True)
        return True

    return router
