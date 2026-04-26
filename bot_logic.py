# bot_logic.py
import logging
import json
import re
from typing import Optional, Callable, Dict, Any, List
from database import DatabaseManager
from config import config

logger = logging.getLogger(__name__)


class FairBotLogic:
    """
    Бизнес-логика бота.
    Не зависит от VK API напрямую — использует callback для отправки сообщений.
    """

    def __init__(self, db: DatabaseManager, send_callback: Callable[[int, str, Optional[Dict]], None]):
        self.db = db
        self.send = send_callback  # callback: (user_id, text, keyboard_payload) -> None
        self._ensure_initial_admin()

    def _ensure_initial_admin(self):
        admin_id = config.INITIAL_SUPER_ADMIN
        user = self.db.get_user(admin_id)
        if not user or user["role"] != "admin":
            self.db.set_role(admin_id, "admin")
            logger.info(f"👑 Назначен супер-админ: {admin_id}")

    # === Вспомогательные методы для клавиатур ===
    @staticmethod
    def _kb_inline(buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Создаёт inline-клавиатуру в формате VK API"""
        return {
            "one_time": False,
            "inline": True,
            "buttons": [[{"action": {"type": "callback", "payload": json.dumps(b["payload"])},
                          "color": b.get("color", "default"),
                          "label": b["label"]}] for b in buttons]
        }

    @staticmethod
    def _kb_shop_selection(shops: List[Dict]) -> Dict[str, Any]:
        buttons = [
            {"label": f"🏪 {s['name']}", "payload": {"action": "select_shop", "shop_id": s["id"]}, "color": "primary"}
            for s in shops]
        return FairBotLogic._kb_inline(buttons)

    @staticmethod
    def _kb_item_selection(items: List[Dict], shop_name: str) -> Dict[str, Any]:
        buttons = [{"label": f"💰 {i['name']} ({i['price']} б.)",
                    "payload": {"action": "buy_item", "item_id": i["id"], "amount": i["price"]},
                    "color": "positive"} for i in items]
        buttons.append({"label": "🔙 Назад", "payload": {"action": "back"}, "color": "secondary"})
        return FairBotLogic._kb_inline(buttons)

    @staticmethod
    def _kb_admin_transaction(trans_id: int) -> Dict[str, Any]:
        return {
            "one_time": False,
            "inline": True,
            "buttons": [[
                {"action": {"type": "callback", "payload": json.dumps({"action": "approve", "t_id": trans_id})},
                 "color": "positive", "label": "✅ Подтвердить"},
                {"action": {"type": "callback", "payload": json.dumps({"action": "reject", "t_id": trans_id})},
                 "color": "negative", "label": "❌ Отклонить"}
            ]]
        }

    # === Обработчик текстовых сообщений ===
    def handle_message(self, user_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return

        user = self.db.get_user(user_id)
        role = user["role"] if user else "unregistered"

        # === Общие команды ===
        if text == "/start":
            return self.send(user_id, f"👋 Привет! Ваша роль: {role}\nНапишите /help для списка команд.")

        if text == "/help":
            return self.send(user_id,
                             "📜 СПИСОК КОМАНД:\n"
                             "/create_team <название> — создать команду (1 раз)\n"
                             "/add_shop <название> — добавить лавку (командир)\n"
                             "/add_item <лавка_id> <товар> <цена> — добавить товар (командир)\n"
                             "/join <код> — присоединиться к команде по коду командира\n"
                             "/view_team <id> — посмотреть меню команды\n"
                             "/stats — статистика (админ)\n"
                             "/phase <before/during/after> — сменить фазу (админ)\n"
                             "/admin_add <id> — назначить админа (админ)\n"
                             "/admin_role <id> — проверить роль (админ)\n"
                             "/phantom <id> — сделать наблюдателем (админ)\n"
                             "/fine <team_id> <сумма> <причина> — штраф (админ)\n"
                             "/ban <team_id> — заблокировать сделки команды (админ)"
                             )

        # === Регистрация / Командир ===
        if text.startswith("/create_team "):
            if role not in ("unregistered", "phantom"):
                return self.send(user_id, "⛔ У вас уже есть команда или роль.")
            name = text.split(" ", 1)[1].strip()
            tid = self.db.create_team(name, user_id)
            team = self.db.get_team(tid)
            return self.send(user_id,
                             f"🏆 Команда '{name}' создана!\n🆔 ID: {tid}\n🔑 Код для участников: `{team['reg_code']}`\nИспользуйте /add_shop и /add_item для наполнения.")

        if text.startswith("/join "):
            code = text.split(" ", 1)[1].strip()
            ok, msg = self.db.try_register_by_code(user_id, code)
            return self.send(user_id, msg)

        # === Командир: управление лавками ===
        if role == "commander":
            team = self.db.get_team(user["team_id"])
            if team and not team["is_verified"]:
                return self.send(user_id, "⏳ Команда ещё не верифицирована администратором.")

            if text.startswith("/add_shop "):
                name = text.split(" ", 1)[1].strip()
                sid = self.db.create_shop(team["id"], name)
                return self.send(user_id, f"🏪 Лавка '{name}' создана. ID: {sid}")

            if text.startswith("/add_item "):
                try:
                    parts = text.split(" ", 3)
                    if len(parts) < 4: raise ValueError
                    shop_id, name, price = int(parts[1]), parts[2], float(parts[3])
                    self.db.add_item(shop_id, name, price)
                    return self.send(user_id, f"📦 Товар '{name}' добавлен в лавку {shop_id} за {price} б.")
                except:
                    return self.send(user_id, "❌ Формат: /add_item <лавка_id> <название> <цена>")

        # === Покупка: просмотр команды ===
        if text.startswith("/view_team "):
            try:
                tid = int(text.split(" ", 1)[1])
            except ValueError:
                return self.send(user_id, "❌ Неверный ID команды.")

            structure = self.db.get_team_structure(tid)
            if not structure:
                return self.send(user_id, "🔍 Команда не найдена.")

            balance = self.db.get_team_balance(tid)
            msg = f"📋 Анкета: {structure['name']}\n👤 Командир: {structure['commander_id']}\n✅ Верификация: {'Да' if structure['is_verified'] else 'Нет'}\n💰 Баланс: {balance:.1f} б."

            if structure["shops"]:
                self.send(user_id, msg)
                return self.send(user_id, "🛒 Выберите лавку:", self._kb_shop_selection(structure["shops"]))
            return self.send(user_id, msg)

        # === Админ-панель ===
        if role == "admin":
            if text.startswith("/admin_add "):
                try:
                    self.db.set_role(int(text.split(" ", 1)[1]), "admin")
                    return self.send(user_id, "✅ Админ назначен.")
                except:
                    return self.send(user_id, "❌ Неверный user_id")

            if text.startswith("/phantom "):
                try:
                    self.db.set_role(int(text.split(" ", 1)[1]), "phantom")
                    return self.send(user_id, "👻 Пользователь стал наблюдателем.")
                except:
                    return self.send(user_id, "❌ Неверный user_id")

            if text.startswith("/admin_role "):
                try:
                    u = self.db.get_user(int(text.split(" ", 1)[1]))
                    if not u: return self.send(user_id, "❌ Не найден.")
                    t_name = f"(Команда: {self.db.get_team(u['team_id'])['name']})" if u["team_id"] else ""
                    return self.send(user_id, f"🔍 Роль: {u['role']} {t_name}")
                except:
                    return self.send(user_id, "❌ Неверный user_id")

            if text.startswith("/phase "):
                phase = text.split(" ", 1)[1].strip()
                self.db.set_phase(phase)
                return self.send(user_id, f"🌍 Фаза изменена на: {phase}")

            if text == "/stats":
                phase = self.db.get_phase()
                teams = self.db._get_conn().__enter__().execute("SELECT id, name FROM teams").fetchall()
                lines = [f"📊 СТАТИСТИКА (Фаза: {phase})"]
                for t in teams:
                    bal = self.db.get_team_balance(t["id"])
                    lines.append(f"Команда '{t['name']}' (ID {t['id']}): 💰 {bal:.1f} б.")
                return self.send(user_id, "\n".join(lines))

            if text.startswith("/fine "):
                try:
                    parts = text.split(" ", 3)
                    if len(parts) < 4: raise ValueError
                    tid, amount, reason = int(parts[1]), float(parts[2]), parts[3]
                    self.db.add_fine(tid, amount, reason, user_id)
                    return self.send(user_id, f"💸 Штраф {amount} б. для команды {tid} применён.")
                except:
                    return self.send(user_id, "❌ Формат: /fine <team_id> <сумма> <причина>")

            if text.startswith("/ban "):
                try:
                    tid = int(text.split(" ", 1)[1])
                    self.db.toggle_ban(tid, True)
                    return self.send(user_id, f"🚫 Команда {tid} временно заблокирована.")
                except:
                    return self.send(user_id, "❌ Неверный ID команды")

        # === Покупка товара ===
        if text.startswith("/buy "):
            try:
                item_id = int(text.split(" ", 1)[1])
                with self.db._get_conn() as conn:
                    item = conn.execute("""SELECT i.*, s.team_id, s.id as shop_id 
                                          FROM items i JOIN shops s ON i.shop_id=s.id 
                                          WHERE i.id=?""", (item_id,)).fetchone()
                    if not item:
                        return self.send(user_id, "❌ Товар не найден.")

                    buyer = self.db.get_user(user_id)
                    if not buyer or buyer["role"] == "unregistered":
                        return self.send(user_id, "⛔ Сначала зарегистрируйтесь через /join <код>")

                    res = self.db.create_pending_transaction(
                        item["team_id"], item["shop_id"], item["id"], user_id, item["price"]
                    )
                    if not res:
                        return self.send(user_id, "🚫 Сделки временно заблокированы или нет админов.")

                    # Отправляем уведомление назначенному админу
                    self._notify_admin_about_transaction(res)
                    return self.send(user_id, "📨 Заявка отправлена администратору. Ожидайте подтверждения.")
            except ValueError:
                return self.send(user_id, "❌ Формат: /buy <item_id>")
            except Exception as e:
                logger.error(f"Ошибка покупки: {e}")
                return self.send(user_id, "❌ Произошла ошибка. Попробуйте позже.")

    # === Обработчик callback (inline-кнопки) ===
    def handle_callback(self, user_id: int, payload: Dict[str, Any]) -> None:
        action = payload.get("action")

        # --- Покупка: выбор лавки/товара ---
        if action == "select_shop":
            shop_id = payload.get("shop_id")
            with self.db._get_conn() as conn:
                shop = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
                if not shop: return self.send(user_id, "❌ Лавка не найдена.")
                items = conn.execute("SELECT id, name, price FROM items WHERE shop_id=?", (shop_id,)).fetchall()
                if not items: return self.send(user_id, "📭 В этой лавке пока нет товаров.")
                return self.send(user_id, f"🛒 Товары в лавке '{shop['name']}':",
                                 self._kb_item_selection([dict(i) for i in items], shop["name"]))

        if action == "buy_item":
            item_id = payload.get("item_id")
            amount = payload.get("amount")
            # Создаём транзакцию
            with self.db._get_conn() as conn:
                item = conn.execute(
                    "SELECT i.*, s.team_id, s.id as shop_id FROM items i JOIN shops s ON i.shop_id=s.id WHERE i.id=?",
                    (item_id,)).fetchone()
                if not item: return self.send(user_id, "❌ Товар не найден.")
                buyer = self.db.get_user(user_id)
                if not buyer: return self.send(user_id, "⛔ Ошибка авторизации.")

                res = self.db.create_pending_transaction(item["team_id"], item["shop_id"], item["id"], user_id, amount)
                if not res: return self.send(user_id, "🚫 Сделки временно заблокированы.")
                self._notify_admin_about_transaction(res)
                return self.send(user_id, "📨 Заявка отправлена администратору. Ожидайте подтверждения.")

        # --- Админ: подтверждение транзакции ---
        if action in ("approve", "reject") and payload.get("t_id"):
            trans_id = payload["t_id"]
            self.db.update_transaction_status(trans_id, "approved" if action == "approve" else "rejected")

            with self.db._get_conn() as conn:
                trans = conn.execute("SELECT * FROM transactions WHERE id=?", (trans_id,)).fetchone()
                if not trans: return

                msg = "✅ Сделка одобрена. Баланс лавки обновлён." if action == "approve" else "❌ Сделка отклонена."
                self.send(trans["buyer_id"], msg)

                team = self.db.get_team(trans["team_id"])
                if team:
                    self.send(team["commander_id"],
                              f"📝 Ваша лавка: сделка #{trans_id} — {action} ({trans['amount']} б.)")

            # Ответ для show_snackbar
            return self.send(user_id, "", {"type": "show_snackbar", "text": f"Сделка {action}ed!"})

    def _notify_admin_about_transaction(self, trans: Dict[str, Any]) -> None:
        """Отправляет уведомление назначенному админу о новой сделке"""
        admin_id = trans["assigned_admin_id"]
        with self.db._get_conn() as conn:
            team = conn.execute("SELECT name FROM teams WHERE id=?", (trans["team_id"],)).fetchone()
            shop = conn.execute("SELECT name FROM shops WHERE id=?", (trans["shop_id"],)).fetchone()
            item = conn.execute("SELECT name FROM items WHERE id=?", (trans["item_id"],)).fetchone()

        text = (f"🆕 НОВАЯ СДЕЛКА (#{trans['id']})\n"
                f"👤 Покупатель: {trans['buyer_id']}\n"
                f"🏪 Команда: {team['name']} | Лавка: {shop['name']}\n"
                f"📦 Товар: {item['name']} | 💰 Сумма: {trans['amount']}\n"
                f"⏳ Ожидает подтверждения...")

        self.send(admin_id, text, self._kb_admin_transaction(trans["id"]))