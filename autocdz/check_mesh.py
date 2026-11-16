"""Проверка собственной сохранённой сессии без вывода её содержимого."""

import asyncio

from autocdz.config import Config
from autocdz.integration_store import IntegrationStore
from autocdz.mesh import MeshConnector, SessionExpired, MeshUnavailable
from autocdz.storage import Storage


async def check():
    config = Config.load()
    if not config.mesh_enabled:
        print("Интеграция отключена. Сначала настройте HTTPS и экспериментальный доступ.")
        return 1
    storage = Storage(config.database_path)
    await storage.open()
    try:
        store = IntegrationStore(config.database_path, config.encryption_key)
        connection = await store.connection(config.admin_id, decrypt=True)
        if not connection or connection["status"] != "active":
            print("Сначала подключите собственную сессию через форму из бота.")
            return 1
        try:
            tasks = await MeshConnector().fetch(connection["token"], connection["profile_id"])
        except SessionExpired:
            await store.sync_result(config.admin_id, connection["session_id"], "expired")
            print("Сессия истекла или доступ отклонён. Требуется повторное подключение.")
            return 1
        except MeshUnavailable:
            print("Не удалось подтвердить чтение ДЗ. Секреты и ответ сервера не выводятся.")
            return 1
        print(f"Чтение списка ДЗ подтверждено. Получено записей: {len(tasks)}.")
        print("Чтение вопросов цифровых материалов и сдача этой проверкой не подтверждаются.")
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(check()))
    except Exception as error:
        print(f"Проверка остановлена: {type(error).__name__}. Проверьте настройки.")
        raise SystemExit(1)
