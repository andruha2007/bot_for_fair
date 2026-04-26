# database.py
import sqlite3
import random
import logging
from typing import Optional, List, Tuple, Dict, Any
from contextlib import contextmanager
from config import config

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """Контекстный менеджер для безопасной работы с соединением"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT CHECK(role IN ('admin', 'commander', 'member', 'phantom', 'unregistered')) DEFAULT 'unregistered',
                    team_id INTEGER REFERENCES teams(id),
                    is_pending BOOLEAN DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    commander_id INTEGER REFERENCES users(user_id),
                    reg_code TEXT,
                    code_active BOOLEAN DEFAULT 1,
                    is_verified BOOLEAN DEFAULT 0,
                    is_banned BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS shops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER REFERENCES teams(id),
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER REFERENCES shops(id),
                    name TEXT NOT NULL,
                    price REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER REFERENCES teams(id),
                    shop_id INTEGER REFERENCES shops(id),
                    item_id INTEGER REFERENCES items(id),
                    buyer_id INTEGER REFERENCES users(user_id),
                    amount REAL NOT NULL,
                    status TEXT CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
                    assigned_admin_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS fines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER REFERENCES teams(id),
                    amount REAL NOT NULL,
                    reason TEXT,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO settings (key, value) VALUES 
                    ('phase', 'before'), 
                    ('transaction_counter', '0');
            """)

    # === Пользователи ===
    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def set_role(self, user_id: int, role: str, team_id: Optional[int] = None):
        with self._get_conn() as conn:
            conn.execute("""INSERT OR REPLACE INTO users (user_id, role, team_id, is_pending) 
                            VALUES (?, ?, ?, 0)""", (user_id, role, team_id))

    def add_pending_member(self, team_id: int, user_id: int):
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, role, is_pending) VALUES (?, 'unregistered', 1)",
                         (user_id,))

    def try_register_by_code(self, user_id: int, code: str) -> Tuple[bool, str]:
        with self._get_conn() as conn:
            team = conn.execute("SELECT id, reg_code FROM teams WHERE reg_code=? AND code_active=1", (code,)).fetchone()
            if not team:
                return False, "❌ Неверный или неактивный код регистрации."

            user = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if user and user["team_id"]:
                return False, "⛔ Вы уже привязаны к команде."

            conn.execute("""UPDATE users SET role='member', team_id=?, is_pending=0 
                            WHERE user_id=? AND is_pending=1""", (team["id"], user_id))
            if conn.rowcount == 0:
                conn.execute(
                    "INSERT OR REPLACE INTO users (user_id, role, team_id, is_pending) VALUES (?, 'member', ?, 0)",
                    (user_id, team["id"]))
            return True, f"✅ Вы успешно присоединились к команде!"

    # === Команды и лавки ===
    def create_team(self, name: str, commander_id: int) -> int:
        code = str(random.randint(10000, 99999))
        with self._get_conn() as conn:
            cur = conn.execute("INSERT INTO teams (name, commander_id, reg_code) VALUES (?, ?, ?)",
                               (name, commander_id, code))
            team_id = cur.lastrowid
            self.set_role(commander_id, 'commander', team_id)
            return team_id

    def get_team(self, team_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            return dict(row) if row else None

    def create_shop(self, team_id: int, name: str) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("INSERT INTO shops (team_id, name) VALUES (?, ?)", (team_id, name))
            return cur.lastrowid

    def add_item(self, shop_id: int, name: str, price: float) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("INSERT INTO items (shop_id, name, price) VALUES (?, ?, ?)", (shop_id, name, price))
            return cur.lastrowid

    def get_team_structure(self, team_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team: return None
            team_dict = dict(team)
            team_dict["shops"] = []
            shops = conn.execute("SELECT id, name FROM shops WHERE team_id=?", (team_id,)).fetchall()
            for shop in shops:
                items = conn.execute("SELECT id, name, price FROM items WHERE shop_id=?", (shop["id"],)).fetchall()
                team_dict["shops"].append({"id": shop["id"], "name": shop["name"], "items": [dict(i) for i in items]})
            return team_dict

    # === Транзакции ===
    def get_admins(self) -> List[int]:
        with self._get_conn() as conn:
            return [row["user_id"] for row in conn.execute("SELECT user_id FROM users WHERE role='admin'").fetchall()]

    def create_pending_transaction(self, team_id: int, shop_id: int, item_id: int, buyer_id: int, amount: float) -> \
    Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            banned = conn.execute("SELECT is_banned FROM teams WHERE id=?", (team_id,)).fetchone()
            if banned and banned["is_banned"]:
                return None

            counter = int(
                conn.execute("SELECT value FROM settings WHERE key='transaction_counter'").fetchone()["value"])
            admins = self.get_admins()
            if not admins:
                logger.warning("Нет админов для маршрутизации транзакции!")
                return None

            assigned_admin = admins[counter % len(admins)]
            cur = conn.execute("""INSERT INTO transactions 
                                  (team_id, shop_id, item_id, buyer_id, amount, status, assigned_admin_id)
                                  VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                               (team_id, shop_id, item_id, buyer_id, amount, assigned_admin))
            conn.execute("UPDATE settings SET value=? WHERE key='transaction_counter'", (counter + 1,))

            return {
                "id": cur.lastrowid, "team_id": team_id, "shop_id": shop_id,
                "item_id": item_id, "buyer_id": buyer_id, "amount": amount,
                "assigned_admin_id": assigned_admin
            }

    def update_transaction_status(self, trans_id: int, status: str):
        with self._get_conn() as conn:
            conn.execute("""UPDATE transactions SET status=?, approved_at=CURRENT_TIMESTAMP 
                            WHERE id=? AND status='pending'""", (status, trans_id))

    def get_pending_transactions(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM transactions WHERE status='pending'").fetchall()]

    # === Балансы ===
    def get_shop_balance(self, shop_id: int) -> float:
        with self._get_conn() as conn:
            res = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM transactions 
                                  WHERE shop_id=? AND status='approved'""", (shop_id,)).fetchone()
            return float(res[0])

    def get_team_balance(self, team_id: int) -> float:
        with self._get_conn() as conn:
            income = conn.execute("""SELECT COALESCE(SUM(t.amount), 0) FROM transactions t
                                     JOIN shops s ON t.shop_id=s.id 
                                     WHERE s.team_id=? AND t.status='approved'""", (team_id,)).fetchone()[0]
            fines = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM fines WHERE team_id=?", (team_id,)).fetchone()[0]
            return float(income - fines)

    def add_fine(self, team_id: int, amount: float, reason: str, admin_id: int):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO fines (team_id, amount, reason, created_by) VALUES (?, ?, ?, ?)",
                         (team_id, amount, reason, admin_id))

    def toggle_ban(self, team_id: int, is_banned: bool):
        with self._get_conn() as conn:
            conn.execute("UPDATE teams SET is_banned=? WHERE id=?", (is_banned, team_id))

    def get_phase(self) -> str:
        with self._get_conn() as conn:
            return conn.execute("SELECT value FROM settings WHERE key='phase'").fetchone()[0]

    def set_phase(self, phase: str):
        with self._get_conn() as conn:
            conn.execute("UPDATE settings SET value=? WHERE key='phase'", (phase,))