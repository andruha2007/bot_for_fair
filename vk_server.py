# vk_server.py
import requests
import time
import random
import logging
from typing import Optional, Callable, Dict, Any
from config import config

logger = logging.getLogger(__name__)


class VKLongPollServer:
    def __init__(self, token: str, group_id: int, api_version: str = "5.131"):
        # Проверка токена при инициализации
        if not token or token.startswith("vk1.a.ВАШ"):
            raise ValueError("❌ Неверный VK_BOT_TOKEN. Проверьте файл .env")

        self.token = token
        self.group_id = group_id
        self.api_version = api_version

    def _api_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Универсальный метод для запросов к VK API"""
        url = f"https://api.vk.ru/method/{method}"
        params.update({
            "access_token": self.token,
            "v": self.api_version
        })
        resp = requests.post(url, params=params, timeout=10)
        return resp.json()

    def _get_longpoll_server(self) -> bool:
        """Получает новые данные для Long Poll"""
        resp = self._api_request("groups.getLongPollServer", {"group_id": self.group_id})
        if "response" not in resp:
            logger.error(f"❌ Ошибка получения Long Poll сервера: {resp}")
            return False

        self.server_url = resp["response"]["server"]
        self.server_key = resp["response"]["key"]
        self.server_ts = resp["response"]["ts"]
        logger.info(f"✅ Подключен к Long Poll серверу: {self.server_url}")
        return True

    def send_message(self, user_id: int, text: str, keyboard: Optional[Dict] = None) -> Dict[str, Any]:
        """Отправляет сообщение пользователю"""
        params = {
            "user_id": user_id,
            "message": text,
            "random_id": random.randint(0, 2 ** 31)
        }
        if keyboard:
            params["keyboard"] = str(keyboard).replace("'", '"')  # VK требует JSON-строку

        resp = self._api_request("messages.send", params)
        if "error" in resp:
            logger.error(f"❌ Ошибка отправки сообщения {user_id}: {resp['error']}")
        return resp

    def start_polling(self, message_handler: Callable[[int, str], None],
                      callback_handler: Optional[Callable[[int, Dict], None]] = None):
        """
        Запускает цикл Long Poll.
        message_handler: функция для обработки текстовых сообщений (user_id, text)
        callback_handler: функция для обработки callback-событий (user_id, payload)
        """
        if not self._get_longpoll_server():
            return

        logger.info(f"🚀 Бот запущен! Жду события... (Ctrl+C для остановки)")

        while True:
            try:
                resp = requests.get(
                    self.server_url,
                    params={
                        "act": "a_check",
                        "key": self.server_key,
                        "ts": self.server_ts,
                        "wait": config.LP_WAIT_TIME,
                        "v": self.api_version
                    },
                    timeout=config.LP_WAIT_TIME + 5
                )
                data = resp.json()

                # Обработка ошибок Long Poll
                if "failed" in data:
                    logger.warning(f"⚠️ Long Poll ошибка: {data.get('failed')}, переподключение...")
                    time.sleep(2)
                    if not self._get_longpoll_server():
                        time.sleep(5)
                    continue

                self.server_ts = data["ts"]

                # Обработка событий
                for event in data.get("updates", []):
                    event_type = event.get("type")

                    if event_type == "message_new":
                        msg = event["object"]["message"]
                        user_id = msg["from_id"]
                        text = msg.get("text", "").strip()
                        message_handler(user_id, text)

                    elif event_type == "message_event" and callback_handler:
                        # Обработка inline-кнопок (callback)
                        obj = event["object"]
                        user_id = obj["user_id"]
                        payload_str = obj.get("payload", "{}")
                        try:
                            import json
                            payload = json.loads(payload_str)
                            callback_handler(user_id, payload)
                        except json.JSONDecodeError:
                            logger.error(f"❌ Ошибка парсинга payload: {payload_str}")

            except requests.exceptions.Timeout:
                continue  # Нормальная ситуация, повторяем запрос
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Ошибка соединения: {e}, переподключение...")
                time.sleep(5)
                self._get_longpoll_server()
            except KeyboardInterrupt:
                logger.info("👋 Бот остановлен пользователем")
                break
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка: {e}", exc_info=True)
                time.sleep(5)