"""Команды и меню закрытого прототипа."""

import logging
import json
from datetime import datetime, timezone, timedelta

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)

from autocdz.storage import subscription_active
from autocdz.domain import Assignment

MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📚 Мои ЦДЗ"), KeyboardButton(text="🔗 Подключить МЭШ")],
    [KeyboardButton(text="⚡ Автоматизация"), KeyboardButton(text="💎 Подписка")],
    [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🆘 Поддержка")],
    [KeyboardButton(text="📊 История"), KeyboardButton(text="🔒 Отключить МЭШ")],
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


STATUSES = {"queued": "В очереди", "ready": "Решение готово, не сдано", "unsupported": "Неподдерживаемый формат материалов", "attention": "Требует внимания"}


def create_router(config, storage, integration=None):
    router = Router()
    access = PrivateAccess(storage)
    router.message.outer_middleware(access)
    router.callback_query.outer_middleware(access)

    @router.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "Привет! 👋\n\nЭто закрытый прототип AutoCDZ. "
            "Доступны настройки, тестовая подписка и управление аккаунтом.\n\n"
            "Интеграция МЭШ доступна только владельцу в экспериментальном режиме. "
            "Получение вопросов реальных ЦДЗ и автосдача ещё не работают. "
            "Платные услуги пока не продаются.\n\n"
            "Храним Telegram ID, настройки, срок доступа и данные подключённой интеграции, если она включена. "
            "Удалить эти данные: /delete_me.", reply_markup=MENU,
        )

    @router.message(Command("menu", "help"))
    async def menu(message: Message):
        await message.answer("Выбери раздел. Удаление данных: /delete_me.", reply_markup=MENU)

    @router.message(F.text == "📚 Мои ЦДЗ")
    async def assignments(message: Message):
        await show_assignments(message, message.from_user.id)

    async def show_assignments(message, user_id):
        rows = await integration.assignments(user_id) if integration else []
        if not rows:
            await message.answer("📚 Заданий пока нет. Подключи МЭШ и включи автоматизацию. Синхронизация выполняется не чаще раза в 30 минут.")
            return
        for row in rows:
            task = Assignment.model_validate_json(row["payload"])
            await message.answer(
                f"📚 {task.subject}\n{task.title}\nСрок: {task.due_at}\n"
                f"Статус: {STATUSES.get(row['status'], 'Требует внимания')}\n\n{task.description[:2200]}",
                reply_markup=buttons(("История решений", "history")),
            )

    @router.message(F.text.in_({"🔗 Подключение МЭШ", "🔗 Подключить МЭШ"}))
    async def mesh(message: Message):
        if not integration or not config.mesh_enabled or message.from_user.id != config.admin_id:
            await message.answer(MESH_UNAVAILABLE)
            return
        connection = await integration.connection(message.from_user.id)
        state = "Не подключено" if not connection else ("Сессия сохранена" if connection["status"] == "active" else "Требуется повторный вход")
        ticket = await integration.ticket(message.from_user.id)
        await message.answer(
            f"🔗 {state}.\n\nЗакрытый тест собственного аккаунта: подключение существующей сессии через HTTPS. "
            "Пароль не нужен. Сессия может иметь широкие права. Автосдача отключена. Ссылка действует 10 минут.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть защищённую форму", url=f"{config.public_url}/connect#{ticket}")],
                [InlineKeyboardButton(text="Отключить и удалить сессию", callback_data="disconnect")],
            ]),
        )

    @router.message(F.text == "⚡ Автоматизация")
    async def automation(message: Message):
        connection = await integration.connection(message.from_user.id) if integration else None
        if connection and config.mesh_enabled and connection["status"] == "active":
            enabled = bool(connection["automation"])
            await message.answer(
                f"⚡ Автоматизация {'включена' if enabled else 'выключена'}.\n"
                "Доступно чтение списка ДЗ каждые 30 минут при активном доступе. "
                "Вопросы неизвестных форматов не передаются ИИ. Автосдача отключена.",
                reply_markup=buttons(("Приостановить" if enabled else "Включить", f"auto:{0 if enabled else 1}")),
            )
            return
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
            f"💎 {status}\n\nТариф Standard: 349 ₽ за 30 дней.\nПродажа подписок и подтверждение платежей Tribute пока отключены. "
            "Для закрытого теста администратор может выдать доступ вручную. "
            "Оплата не требуется.",
        )

    @router.message(F.text == "⚙️ Настройки")
    async def settings(message: Message):
        user = await storage.user(message.from_user.id)
        enabled = bool(user["notifications"])
        await message.answer(
            f"⚙️ Уведомления: {'включены' if enabled else 'выключены'}.\n"
            "Интервал проверки подключённого аккаунта: не чаще раза в 30 минут.",
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
        await message.answer("Удалить настройки, подписку, сессию МЭШ, задания и решения?",
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
            "Это не подтверждение оплаты. Автосдача отключена.",
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
            if integration:
                await integration.disconnect(user_id)
            await query.message.edit_text("МЭШ отключена. Сессия, задания и решения удалены. Ссылки подключения отозваны.")
        elif query.data == "refresh":
            await show_assignments(query.message, user_id)
        elif query.data == "history":
            await show_history(query.message, user_id)
        elif query.data in ("auto:0", "auto:1"):
            if not integration or not config.mesh_enabled or user_id != config.admin_id:
                await query.message.answer("Экспериментальная интеграция недоступна.")
            elif await integration.automation(user_id, query.data == "auto:1"):
                await query.message.edit_text("Настройка сохранена. Автосдача отключена.")
            else:
                await query.message.answer("Требуется активный доступ и действующая сессия МЭШ.")
        else:
            await query.message.answer("Кнопка устарела. Открой /menu.")

    async def show_history(message, user_id):
        rows = await integration.history(user_id) if integration else []
        if not rows:
            await message.answer("📊 История ИИ-обработки пока пуста. Отправка работ отключена.")
            return
        for row in rows:
            task = Assignment.model_validate_json(row["payload"])
            text = f"📊 {task.subject}\nСтатус: {STATUSES.get(row['status'], 'Требует внимания')}\n"
            if row["solution"]:
                for answer in json.loads(row["solution"]):
                    text += f"\nВопрос {answer['question_id']}: {answer['values'] or answer['pairs']}\n{answer['explanation']}\n"
            await message.answer(text[:3900])

    @router.message(F.text == "📊 История")
    async def history(message: Message):
        await show_history(message, message.from_user.id)

    @router.message(F.text == "🔒 Отключить МЭШ")
    async def disconnect(message: Message):
        await message.answer("Удалить сессию МЭШ, связанные задания и решения?",
                             reply_markup=buttons(("Да, отключить", "disconnect"), ("Отмена", "delete:cancel")))

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
