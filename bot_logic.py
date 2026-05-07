import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from config import config
from database import DatabaseManager

logger = logging.getLogger(__name__)

CURRENCY = "ВШЭ-коины"

S = {
    "MAIN": "MAIN",
    "ADMIN_SETTINGS": "ADMIN_SETTINGS",
    "WAIT_COMMANDER_SECRET": "WAIT_COMMANDER_SECRET",
    "WAIT_TEAM_NAME": "WAIT_TEAM_NAME",
    "WAIT_REGISTER_CODE": "WAIT_REGISTER_CODE",
    "WAIT_ADD_MEMBER": "WAIT_ADD_MEMBER",
    "WAIT_REMOVE_MEMBER": "WAIT_REMOVE_MEMBER",
    "WAIT_SHOP_NAME": "WAIT_SHOP_NAME",
    "WAIT_ITEM_NAME": "WAIT_ITEM_NAME",
    "WAIT_ITEM_PRICE": "WAIT_ITEM_PRICE",
    "WAIT_ADMIN_USER": "WAIT_ADMIN_USER",
    "WAIT_REMOVE_ADMIN": "WAIT_REMOVE_ADMIN",
    "WAIT_ROLE_TAG": "WAIT_ROLE_TAG",
    "WAIT_PHANTOM_TAG": "WAIT_PHANTOM_TAG",
    "WAIT_COMMANDER_TAG": "WAIT_COMMANDER_TAG",
    "WAIT_FINE_AMOUNT": "WAIT_FINE_AMOUNT",
    "WAIT_FINE_REASON": "WAIT_FINE_REASON",
    "WAIT_BAN_TEAM": "WAIT_BAN_TEAM",
    "WAIT_COOLDOWN": "WAIT_COOLDOWN",
    "BUY_TEAM": "BUY_TEAM",
    "BUY_SHOP": "BUY_SHOP",
    "BUY_ITEM": "BUY_ITEM",
    "VIEW_TEAM": "VIEW_TEAM",
    "VIEW_SHOP": "VIEW_SHOP",
}


class FairBotLogic:
    def __init__(self, db: DatabaseManager, send_callback: Callable[[int, str, Optional[Dict]], None]):
        self.db = db
        self.send = send_callback
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self.last_purchase_time: Dict[int, float] = {}
        self._ensure_initial_admin()

    def _ensure_initial_admin(self):
        allowed_ids = list(config.ADMIN_IDS or [])
        if config.INITIAL_SUPER_ADMIN:
            allowed_ids.append(config.INITIAL_SUPER_ADMIN)
        result = self.db.sync_admins(allowed_ids, config.ADMIN_TAGS or [])
        logger.info("Admin sync complete: %s active, %s removed", result["active"], result["removed"])

    def _set_state(self, user_id: int, state: str, ctx: Optional[Dict[str, Any]] = None):
        self.user_states[user_id] = {"state": state, "ctx": ctx or {}}

    def _get_state(self, user_id: int) -> Dict[str, Any]:
        return self.user_states.get(user_id, {"state": S["MAIN"], "ctx": {}})

    def _clear_state(self, user_id: int):
        self.user_states.pop(user_id, None)

    @staticmethod
    def _payload(cmd: str) -> str:
        return json.dumps({"cmd": cmd}, ensure_ascii=False)

    @staticmethod
    def _action_payload(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _money(value: float) -> str:
        return f"{int(value)} {CURRENCY}" if float(value).is_integer() else f"{value:.1f} {CURRENCY}"

    def _reply_kb(self, rows: List[List[Dict[str, str]]], one_time: bool = False) -> Dict[str, Any]:
        return {
            "one_time": one_time,
            "buttons": [
                [
                    {
                        "action": {"type": "text", "label": btn["label"], "payload": self._payload(btn["cmd"])},
                        "color": btn.get("color", "secondary"),
                    }
                    for btn in row
                ]
                for row in rows
            ],
        }

    def _inline_kb(self, buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "inline": True,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "callback",
                            "label": btn["label"][:40],
                            "payload": self._action_payload(btn["payload"]),
                        },
                        "color": btn.get("color", "secondary"),
                    }
                ]
                for btn in buttons
            ],
        }

    def _wait_kb(self) -> Dict[str, Any]:
        return self._reply_kb([[{"label": "Отмена", "cmd": "cancel", "color": "negative"}]], one_time=True)

    def _main_kb(self, role: str, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if role == "admin":
            return self._reply_kb(
                [
                    [{"label": "Команды", "cmd": "teams", "color": "primary"}],
                    [{"label": "Статистика", "cmd": "statistics", "color": "primary"}],
                    [{"label": "Все сделки", "cmd": "transactions"}],
                    [{"label": "Настройки", "cmd": "settings"}, {"label": "Помощь", "cmd": "help"}],
                ]
            )

        if role == "commander":
            rows = []
            if not user or not user.get("team_id"):
                rows.append([{"label": "Создать анкету", "cmd": "create_team", "color": "positive"}])
            else:
                team = self.db.get_team(user["team_id"])
                edit_label = f"Редактировать анкету {team['reg_code']}" if team else "Редактировать анкету"
                rows.extend(
                    [
                        [{"label": "Моя команда", "cmd": "my_team", "color": "primary"}],
                        [{"label": "Код команды", "cmd": "team_code"}],
                        [{"label": edit_label, "cmd": "edit_team", "color": "primary"}],
                        [{"label": "Информация про лавки", "cmd": "shops_info"}],
                        [{"label": "Баланс", "cmd": "balance"}, {"label": "Профиль", "cmd": "profile"}],
                    ]
                )
            rows.append([{"label": "Помощь", "cmd": "help"}])
            return self._reply_kb(rows)

        if role == "member":
            return self._reply_kb(
                [
                    [{"label": "Моя команда", "cmd": "my_team", "color": "primary"}],
                    [{"label": "Участники", "cmd": "my_members"}, {"label": "Меню моей команды", "cmd": "my_team_menu"}],
                    [{"label": "Баланс", "cmd": "balance"}, {"label": "Профиль", "cmd": "profile"}],
                    [{"label": "Помощь", "cmd": "help"}],
                ]
            )

        if role == "phantom":
            return self._reply_kb(
                [
                    [{"label": "Список команд", "cmd": "view_flow", "color": "primary"}],
                    [{"label": "Меню команды", "cmd": "view_flow"}],
                    [{"label": "Профиль", "cmd": "profile"}, {"label": "Настройки", "cmd": "settings"}],
                    [{"label": "Помощь", "cmd": "help"}],
                ]
            )

        return self._reply_kb(
            [
                [{"label": "Начать", "cmd": "help", "color": "primary"}],
                [{"label": "Ввести код команды", "cmd": "register", "color": "positive"}],
                [{"label": "Я командир", "cmd": "commander_secret"}, {"label": "Фантом", "cmd": "be_phantom"}],
                [{"label": "Помощь", "cmd": "help"}],
            ]
        )

    def _is_super_admin_user(self, user_id: int) -> bool:
        return bool(config.INITIAL_SUPER_ADMIN and user_id == config.INITIAL_SUPER_ADMIN)

    def _settings_kb(self, user_id: int) -> Dict[str, Any]:
        stopped = self.db.get_setting("fair_stopped", "0") == "1"
        on_lunch = self.db.is_admin_on_lunch(user_id)
        rows = [
            [{"label": "Пауза покупок", "cmd": "cooldown"}],
            [{"label": "Запустить ярмарку" if stopped else "Остановить ярмарку", "cmd": "toggle_fair", "color": "negative" if not stopped else "positive"}],
            [{"label": "Выйти с обеда" if on_lunch else "Уйти на обед", "cmd": "lunch_out" if on_lunch else "lunch_in", "color": "positive" if on_lunch else "negative"}],
        ]
        if self._is_super_admin_user(user_id):
            rows.append([{"label": "Добавить админа", "cmd": "add_admin"}, {"label": "Удалить админа", "cmd": "remove_admin"}])
        rows.extend(
            [
                [{"label": "Роль по тегу", "cmd": "role_by_tag"}],
                [{"label": "Выдать командира", "cmd": "grant_commander"}, {"label": "Фантом", "cmd": "make_phantom"}],
                [{"label": "Назад", "cmd": "back"}],
            ]
        )
        return self._reply_kb(rows)

    def _phantom_settings_kb(self) -> Dict[str, Any]:
        return self._reply_kb(
            [
                [{"label": "Отказаться от роли фантома", "cmd": "leave_phantom", "color": "negative"}],
                [{"label": "Назад", "cmd": "back"}],
            ]
        )

    def _commander_edit_kb(self) -> Dict[str, Any]:
        return self._reply_kb(
            [
                [{"label": "Добавить участника", "cmd": "add_member"}, {"label": "Убрать участника", "cmd": "remove_member"}],
                [{"label": "Добавить лавку", "cmd": "add_shop"}, {"label": "Добавить товар/услугу", "cmd": "add_item"}],
                [{"label": "Участники", "cmd": "members"}, {"label": "Информация про лавки", "cmd": "shops_info"}],
                [{"label": "Назад", "cmd": "back"}],
            ]
        )

    def handle_message(self, user_id: int, text: str, payload: Optional[Any] = None, user_info: Optional[Dict[str, str]] = None) -> None:
        try:
            text = (text or "").strip()
            payload_dict = self._parse_payload(payload)
            user = self.db.ensure_user(
                user_id,
                tag=(user_info or {}).get("screen_name"),
                display_name=(user_info or {}).get("display_name"),
            )
            if self._is_super_admin_user(user_id) and user.get("role") != "admin":
                self.db.set_role(user_id, "admin")
                user = self.db.get_user(user_id) or user
            role = user["role"] if user else "unregistered"
            state_data = self._get_state(user_id)
            state = state_data["state"]
            ctx = state_data["ctx"]

            cmd = payload_dict.get("cmd") if payload_dict else None
            if not cmd:
                cmd = self._command_from_text(text)

            if cmd in ("help", "start") or text.lower() in ("/start", "начать", "помощь", "/help", "help"):
                self._clear_state(user_id)
                return self.send(user_id, self._help(role), self._main_kb(role, user))
            if cmd in ("cancel", "back") or text.lower() in ("отмена", "назад", "/cancel", "/back"):
                self._clear_state(user_id)
                return self.send(user_id, "Действие отменено.", self._main_kb(role, user))

            if self._fair_is_stopped(role) and not (role == "phantom" and cmd in ("settings", "leave_phantom")):
                self._clear_state(user_id)
                return self.send(user_id, "Ярмарка временно остановлена админом. Действия пользователей недоступны.", self._main_kb(role, user))

            if state != S["MAIN"] and not cmd:
                return self._handle_state_input(user_id, role, text, state, ctx)

            if cmd:
                return self._handle_command(user_id, role, cmd)

            return self.send(user_id, "Не понял команду. Используйте кнопки меню или напишите «Помощь».", self._main_kb(role, user))
        except Exception as exc:
            logger.error("Message handler failed for %s: %s", user_id, exc, exc_info=True)
            self.send(user_id, "Произошла ошибка. Я вернулся в главное меню.", self._main_kb("unregistered", None))

    def _fair_is_stopped(self, role: str) -> bool:
        return role != "admin" and self.db.get_setting("fair_stopped", "0") == "1"

    @staticmethod
    def _parse_payload(payload: Optional[Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _command_from_text(text: str) -> Optional[str]:
        aliases = {
            "начать": "help",
            "помощь": "help",
            "отмена": "cancel",
            "назад": "back",
            "команды": "teams",
            "список команд": "view_flow",
            "ярмарка": "buy_flow",
            "моя команда": "my_team",
            "участники": "my_members",
            "меню моей команды": "my_team_menu",
            "другие команды": "other_teams",
            "баланс": "balance",
            "статистика": "statistics",
            "настройки": "settings",
        }
        return aliases.get(text.lower())

    def handle_callback(self, user_id: int, payload: Dict[str, Any]) -> None:
        try:
            payload = self._parse_payload(payload)
            user = self.db.ensure_user(user_id)
            if self._is_super_admin_user(user_id) and user.get("role") != "admin":
                self.db.set_role(user_id, "admin")
                user = self.db.get_user(user_id) or user
            role = user["role"]
            action = payload.get("action")

            if self._fair_is_stopped(role):
                return self.send(user_id, "Ярмарка временно остановлена админом.")

            if action in ("approve_tx", "reject_tx"):
                return self._handle_admin_transaction(user_id, role, int(payload["id"]), action == "approve_tx")
            if action == "verify_team":
                return self._verify_team(user_id, role, int(payload["team_id"]), True)
            if action == "reject_team":
                return self._verify_team(user_id, role, int(payload["team_id"]), False)
            if action == "verify_shop":
                return self._verify_shop(user_id, role, int(payload["shop_id"]), True)
            if action == "reject_shop":
                return self._delete_shop(user_id, role, int(payload["shop_id"]), rejected=True)
            if action == "admin_delete_shop":
                return self._delete_shop(user_id, role, int(payload["shop_id"]))
            if action == "admin_ban_shop":
                return self._toggle_shop_ban(user_id, role, int(payload["shop_id"]))
            if action == "verify_item":
                return self._verify_item(user_id, role, int(payload["item_id"]), True)
            if action == "reject_item":
                return self._delete_item(user_id, role, int(payload["item_id"]), rejected=True)
            if action == "admin_delete_item":
                return self._delete_item(user_id, role, int(payload["item_id"]))
            if action == "team":
                return self._select_team(user_id, role, int(payload["team_id"]), payload.get("mode"))
            if action == "shop":
                return self._select_shop(user_id, role, int(payload["shop_id"]), int(payload["team_id"]), payload.get("mode", "view"))
            if action == "buy_item":
                return self._buy_item(user_id, role, int(payload["item_id"]))
            if action == "noop":
                return self.send(user_id, "Это строка просмотра. Для покупки откройте раздел «Ярмарка».")
            if action == "edit_shop":
                self._set_state(user_id, S["WAIT_ITEM_NAME"], {"shop_id": int(payload["shop_id"])})
                return self.send(user_id, "Введите название товара или услуги:", self._wait_kb())
            if action == "admin_ban_team":
                return self._toggle_team_ban(user_id, role, int(payload["team_id"]))
            if action == "admin_fine_team":
                if role != "admin":
                    return self.send(user_id, "Недостаточно прав.")
                self._set_state(user_id, S["WAIT_FINE_AMOUNT"], {"team_id": int(payload["team_id"]), "shop_id": None})
                return self.send(user_id, "Введите сумму штрафа для команды:", self._wait_kb())
            if action == "admin_fine_shop":
                if role != "admin":
                    return self.send(user_id, "Недостаточно прав.")
                self._set_state(user_id, S["WAIT_FINE_AMOUNT"], {"team_id": int(payload["team_id"]), "shop_id": int(payload["shop_id"])})
                return self.send(user_id, "Введите сумму штрафа для лавки:", self._wait_kb())

            self.send(user_id, "Действие больше не актуально. Откройте меню заново.", self._main_kb(role, user))
        except Exception as exc:
            logger.error("Callback failed for %s: %s", user_id, exc, exc_info=True)
            self.send(user_id, "Не удалось обработать кнопку. Попробуйте открыть меню заново.")

    def _handle_command(self, user_id: int, role: str, cmd: str) -> None:
        user = self.db.get_user(user_id)

        if cmd == "profile":
            return self._profile(user_id, role)
        if cmd == "my_team":
            if not user or not user.get("team_id"):
                if role == "phantom":
                    return self.send(user_id, "Фантомный пользователь не привязан к команде.", self._main_kb(role, user))
                return self.send(user_id, "Вы пока не привязаны к команде.", self._main_kb(role, user))
            return self._send_team_card(user_id, role, user["team_id"])
        if cmd == "my_members":
            if not user or not user.get("team_id"):
                if role == "phantom":
                    return self.send(user_id, "Фантомный пользователь не привязан к команде.", self._main_kb(role, user))
                return self.send(user_id, "Вы пока не привязаны к команде.", self._main_kb(role, user))
            return self._send_members(user_id, role, user["team_id"])
        if cmd == "my_team_menu":
            if not user or not user.get("team_id"):
                if role == "phantom":
                    return self.send(user_id, "Фантомный пользователь не привязан к команде.", self._main_kb(role, user))
                return self.send(user_id, "Вы пока не привязаны к команде.", self._main_kb(role, user))
            return self._send_team_menu(user_id, role, user["team_id"], allow_buy=False)
        if cmd == "balance":
            if not user or not user.get("team_id"):
                return self.send(user_id, "У вас нет команды.", self._main_kb(role, user))
            return self.send(user_id, f"Баланс команды: {self._money(self.db.get_team_balance(user['team_id']))}.", self._main_kb(role, user))
        if cmd == "other_teams":
            if role not in ("admin", "phantom"):
                return self.send(user_id, "Информация о других командах доступна только админам и фантомам.", self._main_kb(role, user))
            self._set_state(user_id, S["VIEW_TEAM"])
            return self._send_team_list(user_id, role, "view")
        if cmd == "view_flow":
            if role not in ("admin", "phantom"):
                return self.send(user_id, "Информация о командах доступна только админам и фантомам.", self._main_kb(role, user))
            self._set_state(user_id, S["VIEW_TEAM"])
            return self._send_team_list(user_id, role, "view")
        if cmd == "buy_flow":
            if role not in ("admin", "phantom"):
                return self.send(user_id, "Информация о других командах недоступна для вашей роли.", self._main_kb(role, user))
            if role == "phantom":
                return self.send(user_id, "Фантомные пользователи могут смотреть анкеты, но не покупать.", self._main_kb(role, user))
            self._set_state(user_id, S["BUY_TEAM"])
            return self._send_team_list(user_id, role, "buy")

        if role == "unregistered":
            if cmd == "register":
                self._set_state(user_id, S["WAIT_REGISTER_CODE"])
                return self.send(user_id, "Введите числовой код команды:", self._wait_kb())
            if cmd == "commander_secret":
                self._set_state(user_id, S["WAIT_COMMANDER_SECRET"])
                return self.send(user_id, "Введите одноразовый числовой код командира:", self._wait_kb())
            if cmd == "be_phantom":
                self._set_state(user_id, S["WAIT_PHANTOM_TAG"])
                return self.send(user_id, "Введите одноразовый числовой код фантома:", self._wait_kb())

        if role == "phantom":
            if cmd == "settings":
                return self.send(user_id, "Настройки фантома", self._phantom_settings_kb())
            if cmd == "leave_phantom":
                self.db.leave_phantom(user_id)
                self._clear_state(user_id)
                new_user = self.db.get_user(user_id)
                return self.send(user_id, "Роль фантома снята. Вы снова можете зарегистрироваться обычным способом.", self._main_kb("unregistered", new_user))

        if role == "commander":
            if cmd == "create_team":
                if user and user.get("team_id"):
                    return self.send(user_id, "Вы уже создали анкету команды.", self._main_kb(role, user))
                self._set_state(user_id, S["WAIT_TEAM_NAME"])
                return self.send(user_id, "Введите название команды:", self._wait_kb())
            if cmd == "team_code":
                team = self.db.get_team(user["team_id"]) if user and user.get("team_id") else None
                return self.send(user_id, f"Код команды: {team['reg_code']}" if team else "Команда не найдена.", self._main_kb(role, user))
            if cmd == "edit_team":
                team = self.db.get_team(user["team_id"]) if user and user.get("team_id") else None
                code = team["reg_code"] if team else ""
                return self.send(
                    user_id,
                    f"Редактирование анкеты {code}\nВыберите, что изменить:",
                    self._commander_edit_kb(),
                )
            if cmd == "shops_info":
                return self._send_shops_info(user_id, role, user["team_id"])
            if cmd == "members":
                return self._send_members(user_id, role, user["team_id"])
            if cmd == "add_member":
                self._set_state(user_id, S["WAIT_ADD_MEMBER"])
                return self.send(user_id, "Введите участника в едином формате: тег имя. Например: id123456 Иван Иванов.", self._wait_kb())
            if cmd == "remove_member":
                self._set_state(user_id, S["WAIT_REMOVE_MEMBER"])
                return self.send(user_id, "Введите тег участника для удаления из анкеты:", self._wait_kb())
            if cmd == "add_shop":
                self._set_state(user_id, S["WAIT_SHOP_NAME"])
                return self.send(user_id, "Введите название лавки:", self._wait_kb())
            if cmd == "add_item":
                shops = self.db.list_shops(user["team_id"])
                if not shops:
                    return self.send(user_id, "Сначала создайте лавку.", self._main_kb(role, user))
                buttons = [
                    {"label": shop["name"], "payload": {"action": "edit_shop", "shop_id": shop["id"]}, "color": "primary"}
                    for shop in shops
                ]
                return self.send(user_id, "Выберите лавку:", self._inline_kb(buttons))

        if role == "admin":
            if cmd == "teams":
                return self._send_admin_teams(user_id, role)
            if cmd == "statistics":
                return self._send_statistics(user_id, role)
            if cmd == "pending_tx":
                return self._send_pending_tx(user_id, role)
            if cmd == "transactions":
                return self._send_transactions(user_id, role)
            if cmd == "settings":
                self._set_state(user_id, S["ADMIN_SETTINGS"])
                stopped = "остановлена" if self.db.get_setting("fair_stopped", "0") == "1" else "идет"
                return self.send(user_id, f"Настройки\nЯрмарка сейчас: {stopped}.", self._settings_kb(user_id))
            if cmd == "toggle_fair":
                stopped = self.db.get_setting("fair_stopped", "0") == "1"
                self.db.set_setting("fair_stopped", "0" if stopped else "1")
                status = "запущена" if stopped else "остановлена"
                return self.send(user_id, f"Ярмарка {status}.", self._settings_kb(user_id))
            if cmd == "lunch_in":
                self.db.set_admin_lunch(user_id, True)
                return self.send(user_id, "Вы ушли на обед. Новые заявки будут назначаться другим активным админам.", self._settings_kb(user_id))
            if cmd == "lunch_out":
                self.db.set_admin_lunch(user_id, False)
                return self.send(user_id, "Вы вернулись с обеда. Новые заявки снова могут приходить вам.", self._settings_kb(user_id))
            if cmd == "add_admin":
                if not self._is_super_admin_user(user_id):
                    return self.send(user_id, "Добавлять админов может только суперадмин.", self._main_kb(role, user))
                self._set_state(user_id, S["WAIT_ADMIN_USER"])
                return self.send(user_id, "Введите VK user_id нового админа. Чтобы админ сохранился после перезапуска, добавьте его id или тег в .env.", self._wait_kb())
            if cmd == "remove_admin":
                if not self._is_super_admin_user(user_id):
                    return self.send(user_id, "Удалять админов может только суперадмин.", self._main_kb(role, user))
                admins = self.db.list_admin_users()
                lines = ["Текущие админы:"]
                lines.extend([f"- {admin['user_id']} (@{admin.get('tag') or ('id' + str(admin['user_id']))})" for admin in admins] or ["- нет"])
                lines.append("Введите VK user_id админа, которого нужно удалить.")
                self._set_state(user_id, S["WAIT_REMOVE_ADMIN"])
                return self.send(user_id, "\n".join(lines), self._wait_kb())
            if cmd == "role_by_tag":
                self._set_state(user_id, S["WAIT_ROLE_TAG"])
                return self.send(user_id, "Введите тег пользователя:", self._wait_kb())
            if cmd == "make_phantom":
                self._set_state(user_id, S["WAIT_PHANTOM_TAG"])
                return self.send(user_id, "Введите одноразовый числовой код для фантома:", self._wait_kb())
            if cmd == "grant_commander":
                self._set_state(user_id, S["WAIT_COMMANDER_TAG"])
                return self.send(user_id, "Введите одноразовый числовой код для командира:", self._wait_kb())
            if cmd == "cooldown":
                self._set_state(user_id, S["WAIT_COOLDOWN"])
                return self.send(user_id, "Введите паузу между покупками в секундах:", self._wait_kb())

        self.send(user_id, "Эта команда недоступна для вашей роли.", self._main_kb(role, user))

    def _handle_state_input(self, user_id: int, role: str, text: str, state: str, ctx: Dict[str, Any]) -> None:
        user = self.db.get_user(user_id)

        if state == S["WAIT_COMMANDER_SECRET"]:
            ok, msg = self.db.consume_role_key(user_id, "commander", text)
            if not ok:
                return self.send(user_id, msg, self._wait_kb())
            self._clear_state(user_id)
            return self.send(user_id, "Права командира выданы. Теперь можно создать анкету команды.", self._main_kb("commander", self.db.get_user(user_id)))

        if state == S["WAIT_PHANTOM_TAG"] and role != "admin":
            ok, msg = self.db.consume_role_key(user_id, "phantom", text)
            if not ok:
                return self.send(user_id, msg, self._wait_kb())
            self._clear_state(user_id)
            return self.send(user_id, "Вы зарегистрированы как фантомный пользователь.", self._main_kb("phantom", self.db.get_user(user_id)))

        if state == S["WAIT_REGISTER_CODE"]:
            ok, msg = self.db.try_register_by_code(user_id, text)
            self._clear_state(user_id)
            new_user = self.db.get_user(user_id)
            return self.send(user_id, msg, self._main_kb(new_user["role"], new_user))

        if state == S["WAIT_TEAM_NAME"]:
            if role != "commander":
                return self.send(user_id, "Создавать анкеты может только командир.", self._main_kb(role, user))
            if not (2 <= len(text) <= 60):
                return self.send(user_id, "Название должно быть от 2 до 60 символов.", self._wait_kb())
            team_id = self.db.create_team(text, user_id)
            team = self.db.get_team(team_id)
            self._clear_state(user_id)
            self._notify_team_review(team_id)
            return self.send(user_id, f"Анкета создана.\nID команды: {team_id}\nКод регистрации: {team['reg_code']}\nАдмин должен одобрить анкету перед продажами.", self._main_kb("commander", self.db.get_user(user_id)))

        if state == S["WAIT_ADD_MEMBER"]:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                self._clear_state(user_id)
                return self.send(user_id, "Ошибка ввода участника. Формат: тег имя.", self._commander_edit_kb())
            tag = parts[0]
            name = parts[1] if len(parts) > 1 else ""
            ok, msg = self.db.add_allowed_member(user["team_id"], tag, name)
            if not ok:
                self._clear_state(user_id)
                return self.send(user_id, msg, self._commander_edit_kb())
            self._clear_state(user_id)
            return self.send(user_id, msg, self._main_kb(role, user))

        if state == S["WAIT_REMOVE_MEMBER"]:
            self.db.remove_allowed_member(user["team_id"], text)
            self._clear_state(user_id)
            return self.send(user_id, f"Тег {text} удален из анкеты.", self._main_kb(role, user))

        if state == S["WAIT_SHOP_NAME"]:
            shop_id = self.db.create_shop(user["team_id"], text)
            self._clear_state(user_id)
            self._notify_shop_review(shop_id)
            return self.send(user_id, f"Лавка отправлена на одобрение админу. ID: {shop_id}", self._main_kb(role, user))

        if state == S["WAIT_ITEM_NAME"]:
            ctx["name"] = text
            self._set_state(user_id, S["WAIT_ITEM_PRICE"], ctx)
            return self.send(user_id, f"Введите цену в {CURRENCY}:", self._wait_kb())

        if state == S["WAIT_ITEM_PRICE"]:
            try:
                price = float(text.replace(",", "."))
            except ValueError:
                self._clear_state(user_id)
                return self.send(user_id, "Ошибка ввода цены. Введите целое число больше нуля.", self._commander_edit_kb())
            if price <= 0 or int(price) != price:
                self._clear_state(user_id)
                return self.send(user_id, "Ошибка ввода цены. Цена должна быть целым числом больше нуля.", self._commander_edit_kb())
            item_id = self.db.add_item(ctx["shop_id"], ctx["name"], price)
            self._clear_state(user_id)
            self._notify_item_review(item_id)
            return self.send(user_id, "Товар или услуга отправлены на одобрение админу.", self._main_kb(role, user))

        if role == "admin":
            return self._handle_admin_state(user_id, role, text, state, ctx)

        self._clear_state(user_id)
        self.send(user_id, "Действие сброшено.", self._main_kb(role, user))

    def _handle_admin_state(self, user_id: int, role: str, text: str, state: str, ctx: Dict[str, Any]) -> None:
        if state == S["WAIT_ADMIN_USER"]:
            if not self._is_super_admin_user(user_id):
                self._clear_state(user_id)
                return self.send(user_id, "Добавлять админов может только суперадмин.", self._main_kb(role, self.db.get_user(user_id)))
            if not text.strip().isdigit():
                return self.send(user_id, "Введите числовой VK user_id.", self._wait_kb())
            new_admin = int(text)
            self.db.set_role(new_admin, "admin")
            self._clear_state(user_id)
            return self.send(user_id, f"Пользователь {new_admin} назначен админом. Если его id/тега нет в .env, при следующем запуске бот снимет админские права.", self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_REMOVE_ADMIN"]:
            if not self._is_super_admin_user(user_id):
                self._clear_state(user_id)
                return self.send(user_id, "Удалять админов может только суперадмин.", self._main_kb(role, self.db.get_user(user_id)))
            if not text.strip().isdigit():
                return self.send(user_id, "Введите числовой VK user_id.", self._wait_kb())
            admin_id = int(text)
            if admin_id == config.INITIAL_SUPER_ADMIN:
                return self.send(user_id, "Суперадмина нельзя удалить через бота. Уберите его из .env, если нужно отключить.", self._wait_kb())
            removed = self.db.demote_admin(admin_id)
            self._clear_state(user_id)
            return self.send(user_id, "Админ удален." if removed else "Админ не найден.", self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_ROLE_TAG"]:
            found = self.db.find_user_by_tag(text)
            if not found:
                msg = "Пользователь с таким тегом не зарегистрирован."
            elif found.get("team_id"):
                team = self.db.get_team(found["team_id"])
                msg = f"Роль: {self._role_name(found['role'])}\nКоманда: {team['name'] if team else found['team_id']}"
            else:
                msg = f"Роль: {self._role_name(found['role'])}\nКоманда: нет"
            self._clear_state(user_id)
            return self.send(user_id, msg, self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_PHANTOM_TAG"]:
            ok, result = self.db.add_role_key("phantom", text, user_id)
            if not ok:
                return self.send(user_id, result, self._wait_kb())
            self._clear_state(user_id)
            return self.send(user_id, f"Одноразовый код фантома создан: {result}", self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_COMMANDER_TAG"]:
            ok, result = self.db.add_role_key("commander", text, user_id)
            if not ok:
                return self.send(user_id, result, self._wait_kb())
            self._clear_state(user_id)
            return self.send(user_id, f"Одноразовый код командира создан: {result}", self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_FINE_AMOUNT"]:
            try:
                amount = float(text.replace(",", "."))
            except ValueError:
                return self.send(user_id, "Введите сумму штрафа числом.", self._wait_kb())
            if amount <= 0:
                return self.send(user_id, "Сумма штрафа должна быть больше нуля.", self._wait_kb())
            ctx["amount"] = amount
            self._set_state(user_id, S["WAIT_FINE_REASON"], ctx)
            return self.send(user_id, "Введите причину штрафа:", self._wait_kb())

        if state == S["WAIT_FINE_REASON"]:
            self.db.add_fine(ctx["team_id"], ctx["amount"], text, user_id, ctx.get("shop_id"))
            self._clear_state(user_id)
            return self.send(user_id, f"Штраф {self._money(ctx['amount'])} применен.", self._main_kb(role, self.db.get_user(user_id)))

        if state == S["WAIT_BAN_TEAM"]:
            if not text.strip().isdigit():
                return self.send(user_id, "Введите числовой ID команды.", self._wait_kb())
            return self._toggle_team_ban(user_id, role, int(text))

        if state == S["WAIT_COOLDOWN"]:
            if not text.strip().isdigit():
                return self.send(user_id, "Введите паузу числом в секундах.", self._wait_kb())
            sec = int(text)
            if sec < 0:
                return self.send(user_id, "Пауза не может быть отрицательной.", self._wait_kb())
            self.db.set_setting("purchase_cooldown", str(sec))
            self._clear_state(user_id)
            return self.send(user_id, f"Пауза покупок: {sec} сек.", self._settings_kb(user_id))

    def _send_team_list(self, user_id: int, role: str, mode: str):
        if role not in ("admin", "phantom"):
            return self.send(user_id, "Информация о командах доступна только админам и фантомам.", self._main_kb(role, self.db.get_user(user_id)))
        teams = self.db.list_teams(verified_only=(mode != "admin"))
        if not teams:
            return self.send(user_id, "Команд пока нет.", self._main_kb(role, self.db.get_user(user_id)))
        buttons = [
            {
                "label": f"{team['name']} ({'одобрена' if team['is_verified'] else 'ждет'})",
                "payload": {"action": "team", "team_id": team["id"], "mode": mode},
                "color": "primary",
            }
            for team in teams
        ]
        return self.send(user_id, "Выберите команду:", self._inline_kb(buttons))

    def _send_admin_teams(self, user_id: int, role: str):
        teams = self.db.list_teams(False)
        if not teams:
            return self.send(user_id, "Команд пока нет.", self._main_kb(role, self.db.get_user(user_id)))
        buttons = []
        for team in teams:
            buttons.append({"label": f"{team['id']}. {team['name']}", "payload": {"action": "team", "team_id": team["id"], "mode": "admin"}, "color": "primary"})
            if not team["is_verified"]:
                buttons.append({"label": f"Одобрить {team['id']}", "payload": {"action": "verify_team", "team_id": team["id"]}, "color": "positive"})
                buttons.append({"label": f"Отклонить {team['id']}", "payload": {"action": "reject_team", "team_id": team["id"]}, "color": "negative"})
        return self.send(user_id, "Команды и заявки анкет:", self._inline_kb(buttons))

    def _select_team(self, user_id: int, role: str, team_id: int, mode: Optional[str] = None):
        if role not in ("admin", "phantom"):
            user = self.db.get_user(user_id)
            if not user or user.get("team_id") != team_id:
                return self.send(user_id, "Информация о других командах недоступна для вашей роли.", self._main_kb(role, user))
        if role == "admin":
            mode = "admin"
        elif mode == "admin":
            mode = "view"
        if not mode:
            state = self._get_state(user_id)["state"]
            mode = "buy" if state == S["BUY_TEAM"] else "view"

        self._send_team_card(user_id, role, team_id)

        shops = self.db.list_shops(team_id, verified_only=(mode != "admin"))
        buttons = []
        for shop in shops:
            status = ""
            if mode == "admin":
                flags = []
                if not shop.get("is_verified"):
                    flags.append("ждет")
                if shop.get("is_banned"):
                    flags.append("бан")
                status = f" ({', '.join(flags)})" if flags else ""
            buttons.append({"label": f"{shop['name']}{status}", "payload": {"action": "shop", "team_id": team_id, "shop_id": shop["id"], "mode": mode}, "color": "primary"})
            if mode == "admin":
                if not shop.get("is_verified"):
                    buttons.append({"label": f"Одобрить лавку: {shop['name']}", "payload": {"action": "verify_shop", "shop_id": shop["id"]}, "color": "positive"})
                    buttons.append({"label": f"Отклонить лавку: {shop['name']}", "payload": {"action": "reject_shop", "shop_id": shop["id"]}, "color": "negative"})
                buttons.append({"label": f"Штраф: {shop['name']}", "payload": {"action": "admin_fine_shop", "team_id": team_id, "shop_id": shop["id"]}, "color": "negative"})
                buttons.append({"label": f"{'Разбанить' if shop.get('is_banned') else 'Забанить'}: {shop['name']}", "payload": {"action": "admin_ban_shop", "shop_id": shop["id"]}, "color": "negative"})
                buttons.append({"label": f"Удалить лавку: {shop['name']}", "payload": {"action": "admin_delete_shop", "shop_id": shop["id"]}, "color": "negative"})

        if mode == "admin":
            team = self.db.get_team(team_id)
            buttons.extend(
                [
                    {"label": "Штраф команде", "payload": {"action": "admin_fine_team", "team_id": team_id}, "color": "negative"},
                    {"label": "Разбанить" if team and team["is_banned"] else "Бан", "payload": {"action": "admin_ban_team", "team_id": team_id}, "color": "negative"},
                ]
            )

        if not buttons:
            return
        self._set_state(user_id, S["BUY_SHOP"] if mode == "buy" else S["VIEW_SHOP"], {"team_id": team_id})
        self.send(user_id, "Лавки и действия:" if mode == "admin" else "Выберите лавку:", self._inline_kb(buttons))

    def _select_shop(self, user_id: int, role: str, shop_id: int, team_id: int, mode: str):
        items = self.db.list_items(shop_id, verified_only=(mode != "admin"))
        if not items:
            return self.send(user_id, "В лавке пока нет товаров или услуг.")
        buttons = []
        for item in items:
            payload = {"action": "buy_item", "item_id": item["id"]} if mode == "buy" else {"action": "noop"}
            status = " (ждет)" if mode == "admin" and not item.get("is_verified") else ""
            buttons.append({"label": f"{item['name']} - {self._money(item['price'])}{status}", "payload": payload, "color": "positive" if mode == "buy" else "secondary"})
            if mode == "admin":
                if not item.get("is_verified"):
                    buttons.append({"label": f"Одобрить: {item['name']}", "payload": {"action": "verify_item", "item_id": item["id"]}, "color": "positive"})
                    buttons.append({"label": f"Отклонить: {item['name']}", "payload": {"action": "reject_item", "item_id": item["id"]}, "color": "negative"})
                buttons.append({"label": f"Убрать: {item['name']}", "payload": {"action": "admin_delete_item", "item_id": item["id"]}, "color": "negative"})
        self._set_state(user_id, S["BUY_ITEM"] if mode == "buy" else S["VIEW_SHOP"], {"team_id": team_id, "shop_id": shop_id})
        self.send(user_id, "Товары и услуги:", self._inline_kb(buttons))

    def _buy_item(self, user_id: int, role: str, item_id: int):
        if role == "phantom":
            return self.send(user_id, "Фантомные пользователи не могут покупать.")
        item = self.db.get_item_for_purchase(item_id)
        if not item:
            return self.send(user_id, "Товар не найден.")
        cooldown = int(self.db.get_setting("purchase_cooldown", "0"))
        now = time.time()
        if cooldown and now - self.last_purchase_time.get(user_id, 0) < cooldown:
            wait = int(cooldown - (now - self.last_purchase_time.get(user_id, 0)))
            return self.send(user_id, f"Подождите {wait} сек. перед следующей покупкой.")
        tx = self.db.create_pending_transaction(item["team_id"], item["shop_id"], item["id"], user_id, item["price"])
        if not tx:
            return self.send(user_id, "Сделка не создана: команда не одобрена, забанена или нет админов.")
        self.last_purchase_time[user_id] = now
        self._clear_state(user_id)
        self._notify_admin(tx)
        self.send(user_id, f"Заявка на покупку «{item['name']}» отправлена админу.", self._main_kb(role, self.db.get_user(user_id)))

    def _send_team_card(self, user_id: int, role: str, team_id: int):
        viewer = self.db.get_user(user_id)
        include_pending = role == "admin" or bool(viewer and viewer.get("role") == "commander" and viewer.get("team_id") == team_id)
        team = self.db.get_team_structure(team_id, include_pending=include_pending)
        if not team:
            return self.send(user_id, "Команда не найдена.")
        lines = [
            f"Команда: {team['name']} (ID {team['id']})",
            f"Статус: {'одобрена' if team['is_verified'] else 'ждет одобрения'}",
            f"Бан: {'да' if team['is_banned'] else 'нет'}",
            f"Баланс команды: {self._money(self.db.get_team_balance(team_id))}",
            "",
            "Участники:",
        ]
        members = team["members"] or []
        lines.extend([f"- @{m['tag']} {m['name'] or ''}".strip() for m in members] or ["- пока не указаны"])
        lines.append("")
        lines.append("Лавки:")
        if team["shops"]:
            for shop in team["shops"]:
                flags = []
                if include_pending and not shop.get("is_verified"):
                    flags.append("ждет одобрения")
                if include_pending and shop.get("is_banned"):
                    flags.append("бан")
                status = f" [{', '.join(flags)}]" if flags else ""
                lines.append(f"- {shop['name']} (ID {shop['id']}){status}, баланс {self._money(shop['balance'])}")
                for item in shop["items"]:
                    item_status = " [ждет одобрения]" if include_pending and not item.get("is_verified") else ""
                    lines.append(f"  - {item['name']}: {self._money(item['price'])}{item_status}")
        else:
            lines.append("- пока нет")
        self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_user(user_id)))

    def _send_members(self, user_id: int, role: str, team_id: int):
        members = self.db.list_allowed_members(team_id)
        text = "Участники анкеты:\n" + "\n".join([f"@{m['tag']} {m['name'] or ''}".strip() for m in members]) if members else "Участники еще не добавлены."
        self.send(user_id, text, self._main_kb(role, self.db.get_user(user_id)))

    def _send_team_menu(self, user_id: int, role: str, team_id: int, allow_buy: bool):
        team = self.db.get_team_structure(team_id)
        if not team:
            return self.send(user_id, "Команда не найдена.", self._main_kb(role, self.db.get_user(user_id)))

        lines = [f"Меню команды «{team['name']}»"]
        if team["shops"]:
            for shop in team["shops"]:
                lines.append(f"\n{shop['name']}:")
                if shop["items"]:
                    for item in shop["items"]:
                        lines.append(f"- {item['name']}: {self._money(item['price'])}")
                else:
                    lines.append("- товаров и услуг пока нет")
        else:
            lines.append("Лавок пока нет.")

        self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_user(user_id)))

    def _send_shops_info(self, user_id: int, role: str, team_id: int):
        team = self.db.get_team_structure(team_id, include_pending=(role in ("admin", "commander")))
        if not team:
            return self.send(user_id, "Команда не найдена.", self._main_kb(role, self.db.get_user(user_id)))

        lines = [f"Лавки команды «{team['name']}»"]
        if team["shops"]:
            for shop in team["shops"]:
                lines.append(f"- {shop['name']} (ID {shop['id']}), баланс {self._money(shop['balance'])}")
                if shop["items"]:
                    for item in shop["items"]:
                        lines.append(f"  - {item['name']}: {self._money(item['price'])}")
                else:
                    lines.append("  - товаров и услуг пока нет")
        else:
            lines.append("- пока нет")
        self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_user(user_id)))

    def _send_statistics(self, user_id: int, role: str):
        stats = self.db.get_fair_statistics()
        if not stats:
            return self.send(user_id, "Статистики пока нет: команды не созданы.", self._main_kb(role, self.db.get_user(user_id)))

        lines = ["Статистика ярмарки", "", "Текущие победители:"]
        for pos, team in enumerate(stats[:5], start=1):
            lines.append(f"{pos}. {team['name']} — {self._money(team['balance'])}")

        lines.append("")
        lines.append("Команды:")
        for team in stats:
            lines.append(f"{team['name']} (ID {team['id']})")
            lines.append(f"Баланс команды: {self._money(team['balance'])}; заработано: {self._money(team['earned_total'])}")
            lines.append("Участники:")
            members = team["members"] or []
            if members:
                for member in members:
                    name = f" {member['name']}" if member.get("name") else ""
                    lines.append(f"- @{member['tag']}{name}; потрачено: {self._money(member.get('spent_total', 0))}")
            else:
                lines.append("- нет")
            lines.append("Лавки:")
            shops = team["shops"] or []
            if shops:
                for shop in shops:
                    lines.append(f"- {shop['name']} (ID {shop['id']}): баланс {self._money(shop['balance'])}; заработано {self._money(shop.get('earned_total') or 0)}")
            else:
                lines.append("- нет")
            lines.append("")

        self.send(user_id, "\n".join(lines).strip(), self._main_kb(role, self.db.get_user(user_id)))

    def _send_pending_tx(self, user_id: int, role: str):
        txs = self.db.get_pending_transactions(user_id)
        if not txs:
            txs = self.db.get_pending_transactions(None)
        if not txs:
            return self.send(user_id, "Ожидающих сделок нет.", self._main_kb(role, self.db.get_user(user_id)))
        buttons = []
        for tx in txs:
            buttons.append({"label": f"Одобрить #{tx['id']} ({self._money(tx['amount'])})", "payload": {"action": "approve_tx", "id": tx["id"]}, "color": "positive"})
            buttons.append({"label": f"Отклонить #{tx['id']}", "payload": {"action": "reject_tx", "id": tx["id"]}, "color": "negative"})
        self.send(user_id, f"Ожидают подтверждения: {len(txs)}", self._inline_kb(buttons))

    def _send_transactions(self, user_id: int, role: str):
        txs = self.db.get_all_transactions()
        if not txs:
            return self.send(user_id, "Сделок пока нет.", self._main_kb(role, self.db.get_user(user_id)))
        lines = ["Последние сделки:"]
        for tx in txs:
            lines.append(
                f"#{tx['id']} покупатель {self.db.user_label(tx['buyer_id'])}, "
                f"команда {tx['team_id']}, лавка {tx['shop_id']}, {self._money(tx['amount'])}, "
                f"{tx['status']}, админ {self.db.user_label(tx['assigned_admin_id'])}"
            )
        self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_user(user_id)))

    def _handle_admin_transaction(self, user_id: int, role: str, tx_id: int, approved: bool):
        if role != "admin":
            return self.send(user_id, "Только админ может подтверждать сделки.")
        tx = self.db.update_transaction_status(tx_id, "approved" if approved else "rejected")
        if not tx:
            return self.send(user_id, "Сделка уже обработана или не найдена.", self._main_kb(role, self.db.get_user(user_id)))
        self.send(tx["buyer_id"], f"Сделка #{tx_id} {'одобрена' if approved else 'отклонена'}.")
        team = self.db.get_team(tx["team_id"])
        if team:
            self.send(team["commander_id"], f"Сделка #{tx_id}: {'одобрена' if approved else 'отклонена'}, сумма {self._money(tx['amount'])}.")
        self.send(user_id, "Готово.", self._main_kb(role, self.db.get_user(user_id)))

    def _notify_admin(self, tx: Dict[str, Any]):
        item = self.db.get_item_for_purchase(tx["item_id"])
        text = (
            f"Новая сделка #{tx['id']}\n"
            f"Покупатель: {self.db.user_label(tx['buyer_id'])}\n"
            f"Команда: {item['team_name'] if item else tx['team_id']}\n"
            f"Лавка: {item['shop_name'] if item else tx['shop_id']}\n"
            f"Товар: {item['name'] if item else tx['item_id']}\n"
            f"Сумма: {self._money(tx['amount'])}"
        )
        kb = self._inline_kb(
            [
                {"label": "Одобрить", "payload": {"action": "approve_tx", "id": tx["id"]}, "color": "positive"},
                {"label": "Отклонить", "payload": {"action": "reject_tx", "id": tx["id"]}, "color": "negative"},
            ]
        )
        self.send(tx["assigned_admin_id"], text, kb)

    def _notify_admins(self, text: str, keyboard: Dict[str, Any]):
        admin_id = self.db.next_available_admin()
        if admin_id:
            self.send(admin_id, text, keyboard)

    def _notify_team_review(self, team_id: int):
        team = self.db.get_team(team_id)
        if not team:
            return
        text = (
            f"Новая команда на одобрение\n"
            f"Команда: {team['name']} (ID {team_id})\n"
            f"Командир: {self.db.user_label(team['commander_id'])}"
        )
        kb = self._inline_kb(
            [
                {"label": "Одобрить команду", "payload": {"action": "verify_team", "team_id": team_id}, "color": "positive"},
                {"label": "Отклонить команду", "payload": {"action": "reject_team", "team_id": team_id}, "color": "negative"},
            ]
        )
        self._notify_admins(text, kb)

    def _notify_shop_review(self, shop_id: int):
        shop = self.db.get_shop(shop_id)
        if not shop:
            return
        team = self.db.get_team(shop["team_id"])
        text = (
            f"Новая лавка на одобрение\n"
            f"Команда: {team['name'] if team else shop['team_id']}\n"
            f"Лавка: {shop['name']} (ID {shop_id})"
        )
        kb = self._inline_kb(
            [
                {"label": "Одобрить лавку", "payload": {"action": "verify_shop", "shop_id": shop_id}, "color": "positive"},
                {"label": "Отклонить лавку", "payload": {"action": "reject_shop", "shop_id": shop_id}, "color": "negative"},
            ]
        )
        self._notify_admins(text, kb)

    def _notify_item_review(self, item_id: int):
        item = self.db.get_item(item_id)
        if not item:
            return
        shop = self.db.get_shop(item["shop_id"])
        team = self.db.get_team(shop["team_id"]) if shop else None
        text = (
            f"Новый товар/услуга на одобрение\n"
            f"Команда: {team['name'] if team else '-'}\n"
            f"Лавка: {shop['name'] if shop else item['shop_id']}\n"
            f"Позиция: {item['name']}\n"
            f"Цена: {self._money(item['price'])}"
        )
        kb = self._inline_kb(
            [
                {"label": "Одобрить", "payload": {"action": "verify_item", "item_id": item_id}, "color": "positive"},
                {"label": "Отклонить", "payload": {"action": "reject_item", "item_id": item_id}, "color": "negative"},
            ]
        )
        self._notify_admins(text, kb)

    def _verify_shop(self, user_id: int, role: str, shop_id: int, approve: bool):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        shop = self.db.get_shop(shop_id)
        if not shop:
            return self.send(user_id, "Лавка не найдена.", self._main_kb(role, self.db.get_user(user_id)))
        ok = self.db.verify_shop(shop_id, approve)
        team = self.db.get_team(shop["team_id"])
        if ok and team:
            self.send(team["commander_id"], f"Лавка «{shop['name']}» одобрена.")
        self.send(user_id, "Лавка одобрена." if ok else "Лавка не найдена.", self._main_kb(role, self.db.get_user(user_id)))

    def _delete_shop(self, user_id: int, role: str, shop_id: int, rejected: bool = False):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        shop = self.db.get_shop(shop_id)
        ok = self.db.delete_shop(shop_id)
        if shop:
            team = self.db.get_team(shop["team_id"])
            if team:
                self.send(team["commander_id"], f"Лавка «{shop['name']}» {'отклонена' if rejected else 'удалена админом'}.")
        self.send(user_id, ("Лавка отклонена." if rejected else "Лавка удалена.") if ok else "Лавка не найдена.", self._main_kb(role, self.db.get_user(user_id)))

    def _toggle_shop_ban(self, user_id: int, role: str, shop_id: int):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        shop = self.db.get_shop(shop_id)
        if not shop:
            return self.send(user_id, "Лавка не найдена.", self._main_kb(role, self.db.get_user(user_id)))
        new_state = not bool(shop.get("is_banned"))
        self.db.toggle_shop_ban(shop_id, new_state)
        team = self.db.get_team(shop["team_id"])
        if team:
            self.send(team["commander_id"], f"Лавка «{shop['name']}» {'забанена' if new_state else 'разбанена'}.")
        self.send(user_id, "Лавка забанена." if new_state else "Лавка разбанена.", self._main_kb(role, self.db.get_user(user_id)))

    def _verify_item(self, user_id: int, role: str, item_id: int, approve: bool):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        item = self.db.get_item(item_id)
        if not item:
            return self.send(user_id, "Товар/услуга не найдены.", self._main_kb(role, self.db.get_user(user_id)))
        ok = self.db.verify_item(item_id, approve)
        shop = self.db.get_shop(item["shop_id"])
        team = self.db.get_team(shop["team_id"]) if shop else None
        if ok and team:
            self.send(team["commander_id"], f"Товар/услуга «{item['name']}» одобрены.")
        self.send(user_id, "Товар/услуга одобрены." if ok else "Товар/услуга не найдены.", self._main_kb(role, self.db.get_user(user_id)))

    def _delete_item(self, user_id: int, role: str, item_id: int, rejected: bool = False):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        item = self.db.get_item(item_id)
        ok = self.db.delete_item(item_id)
        if item:
            shop = self.db.get_shop(item["shop_id"])
            team = self.db.get_team(shop["team_id"]) if shop else None
            if team:
                self.send(team["commander_id"], f"Товар/услуга «{item['name']}» {'отклонены' if rejected else 'убраны админом'}.")
        self.send(user_id, ("Товар/услуга отклонены." if rejected else "Товар/услуга убраны.") if ok else "Товар/услуга не найдены.", self._main_kb(role, self.db.get_user(user_id)))

    def _verify_team(self, user_id: int, role: str, team_id: int, approve: bool):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        ok = self.db.verify_team(team_id, approve)
        team = self.db.get_team(team_id)
        if team:
            self.send(team["commander_id"], "Анкета команды одобрена." if approve else "Анкета команды отклонена.")
        self.send(user_id, "Статус анкеты обновлен." if ok else "Команда не найдена.", self._main_kb(role, self.db.get_user(user_id)))

    def _toggle_team_ban(self, user_id: int, role: str, team_id: int):
        if role != "admin":
            return self.send(user_id, "Недостаточно прав.")
        team = self.db.get_team(team_id)
        if not team:
            return self.send(user_id, "Команда не найдена.", self._main_kb(role, self.db.get_user(user_id)))
        new_state = not bool(team["is_banned"])
        self.db.toggle_ban(team_id, new_state)
        self._clear_state(user_id)
        self.send(user_id, "Команда забанена." if new_state else "Команда разбанена.", self._main_kb(role, self.db.get_user(user_id)))

    def _profile(self, user_id: int, role: str):
        user = self.db.get_user(user_id)
        team = self.db.get_team(user["team_id"]) if user and user.get("team_id") else None
        text = f"Профиль\nРоль: {self._role_name(role)}\nТег: @{user.get('tag') if user else f'id{user_id}'}"
        if team:
            text += f"\nКоманда: {team['name']}"
        if user:
            text += f"\nПотрачено: {self._money(user.get('spent_total') or 0)}"
        self.send(user_id, text, self._main_kb(role, user))

    def _help(self, role: str) -> str:
        return (
            f"Бот ярмарки фиксирует анкеты команд, лавки, товары и сделки в валюте {CURRENCY}.\n\n"
            "Командир создает анкету, добавляет участников по тегам, лавки и товары. "
            "Участник регистрируется по числовому коду команды только если его тег есть в анкете. "
            "Покупка отправляется админу по кольцу: номер сделки mod количество админов. "
            "После одобрения баланс лавки и команды обновляется."
        )

    @staticmethod
    def _role_name(role: str) -> str:
        return {
            "admin": "админ",
            "commander": "командир",
            "member": "участник",
            "phantom": "фантом",
            "unregistered": "не зарегистрирован",
        }.get(role, role)
