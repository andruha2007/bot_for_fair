import json
import logging
import random
import time
from typing import Any, Callable, Dict, Optional

import requests

from config import config

logger = logging.getLogger(__name__)


class VKLongPollServer:
    def __init__(self, token: str, group_id: int, api_version: str = "5.131"):
        self.token = token
        self.group_id = group_id
        self.api_version = api_version
        self.server_url: Optional[str] = None
        self.server_key: Optional[str] = None
        self.server_ts: Optional[str] = None

    def _api_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.vk.ru/method/{method}"
        params = {
            **params,
            "access_token": self.token,
            "v": self.api_version,
        }
        try:
            response = requests.post(url, params=params, timeout=10)
            return response.json()
        except Exception as exc:
            logger.error("VK API request %s failed: %s", method, exc, exc_info=True)
            return {"error": {"error_msg": str(exc)}}

    def _get_longpoll_server(self) -> bool:
        response = self._api_request("groups.getLongPollServer", {"group_id": self.group_id})
        data = response.get("response")
        if not data:
            logger.error("Long Poll server error: %s", response)
            return False

        self.server_url = data["server"]
        self.server_key = data["key"]
        self.server_ts = data["ts"]
        logger.info("Connected to VK Long Poll")
        return True

    def send_message(self, user_id: int, text: str, keyboard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": user_id,
            "message": text,
            "random_id": random.randint(1, 2**31 - 1),
        }
        if keyboard:
            params["keyboard"] = keyboard if isinstance(keyboard, str) else json.dumps(keyboard, ensure_ascii=False)

        response = self._api_request("messages.send", params)
        if "error" in response:
            logger.error("Failed to send message to %s: %s", user_id, response["error"])
        return response

    def start_polling(
        self,
        message_handler: Callable[[int, str, Optional[Any]], None],
        callback_handler: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ):
        if not self._get_longpoll_server():
            logger.error("Cannot start Long Poll. Bot stopped.")
            return

        logger.info("Bot started. Waiting for events...")

        while True:
            try:
                response = requests.get(
                    self.server_url,
                    params={
                        "act": "a_check",
                        "key": self.server_key,
                        "ts": self.server_ts,
                        "wait": config.LP_WAIT_TIME,
                        "v": self.api_version,
                    },
                    timeout=config.LP_WAIT_TIME + 5,
                )
                data = response.json()

                if "failed" in data:
                    logger.warning("Long Poll failed: %s", data.get("failed"))
                    time.sleep(2)
                    self._get_longpoll_server()
                    continue

                self.server_ts = data["ts"]

                for event in data.get("updates", []):
                    event_type = event.get("type")

                    if event_type == "message_new":
                        message = event.get("object", {}).get("message", {})
                        user_id = message.get("from_id")
                        if not user_id:
                            continue
                        text = message.get("text", "").strip()
                        payload = message.get("payload")
                        try:
                            message_handler(user_id, text, payload)
                        except Exception as exc:
                            logger.error("Message handler failed for %s: %s", user_id, exc, exc_info=True)
                            self.send_message(user_id, "Произошла ошибка. Напишите /help или попробуйте позже.")

                    elif event_type == "message_event" and callback_handler:
                        obj = event.get("object", {})
                        user_id = obj.get("user_id")
                        payload = obj.get("payload", {})
                        if isinstance(payload, str):
                            try:
                                payload = json.loads(payload)
                            except json.JSONDecodeError:
                                logger.error("Cannot parse callback payload: %s", payload)
                                payload = {}
                        if user_id:
                            try:
                                callback_handler(user_id, payload)
                            except Exception as exc:
                                logger.error("Callback handler failed for %s: %s", user_id, exc, exc_info=True)
                                self.send_message(user_id, "Не удалось обработать кнопку. Попробуйте открыть меню заново.")

            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException as exc:
                logger.warning("Connection error: %s", exc)
                time.sleep(3)
                self._get_longpoll_server()
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as exc:
                logger.error("Critical Long Poll loop error: %s", exc, exc_info=True)
                time.sleep(5)
