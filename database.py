import logging
import random
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("DB transaction failed: %s", exc, exc_info=True)
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT CHECK(role IN ('admin', 'commander', 'member', 'phantom', 'unregistered')) DEFAULT 'unregistered',
                    team_id INTEGER REFERENCES teams(id),
                    tag TEXT,
                    display_name TEXT,
                    spent_total REAL DEFAULT 0,
                    is_pending BOOLEAN DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    commander_id INTEGER REFERENCES users(user_id),
                    reg_code TEXT UNIQUE,
                    code_active BOOLEAN DEFAULT 1,
                    is_verified BOOLEAN DEFAULT 0,
                    is_banned BOOLEAN DEFAULT 0,
                    earned_total REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS team_allowed_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL,
                    name TEXT,
                    linked_user_id INTEGER REFERENCES users(user_id),
                    UNIQUE(team_id, tag)
                );

                CREATE TABLE IF NOT EXISTS shops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    earned_total REAL DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    is_active BOOLEAN DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL REFERENCES teams(id),
                    shop_id INTEGER NOT NULL REFERENCES shops(id),
                    item_id INTEGER NOT NULL REFERENCES items(id),
                    buyer_id INTEGER NOT NULL REFERENCES users(user_id),
                    amount REAL NOT NULL,
                    status TEXT CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
                    assigned_admin_id INTEGER REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS fines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER NOT NULL REFERENCES teams(id),
                    shop_id INTEGER REFERENCES shops(id),
                    amount REAL NOT NULL,
                    reason TEXT,
                    created_by INTEGER REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                INSERT OR IGNORE INTO settings (key, value) VALUES
                    ('phase', 'before'),
                    ('transaction_counter', '0'),
                    ('purchase_cooldown', '0'),
                    ('fair_stopped', '0'),
                    ('commander_secret', '1111');
                """
            )
            self._migrate(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
                CREATE INDEX IF NOT EXISTS idx_users_tag ON users(tag);
                CREATE INDEX IF NOT EXISTS idx_teams_commander ON teams(commander_id);
                CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
                """
            )

    def _migrate(self, conn: sqlite3.Connection):
        def columns(table: str) -> set[str]:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

        user_cols = columns("users")
        if "tag" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN tag TEXT")
        if "display_name" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        if "spent_total" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN spent_total REAL DEFAULT 0")

        team_cols = columns("teams")
        if "earned_total" not in team_cols:
            conn.execute("ALTER TABLE teams ADD COLUMN earned_total REAL DEFAULT 0")

        shop_cols = columns("shops")
        if "is_active" not in shop_cols:
            conn.execute("ALTER TABLE shops ADD COLUMN is_active BOOLEAN DEFAULT 1")
        if "earned_total" not in shop_cols:
            conn.execute("ALTER TABLE shops ADD COLUMN earned_total REAL DEFAULT 0")

        item_cols = columns("items")
        if "is_active" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN is_active BOOLEAN DEFAULT 1")

        fine_cols = columns("fines")
        if "shop_id" not in fine_cols:
            conn.execute("ALTER TABLE fines ADD COLUMN shop_id INTEGER REFERENCES shops(id)")
        self._refresh_financial_totals(conn)

    def _refresh_financial_totals(self, conn: sqlite3.Connection):
        conn.execute("UPDATE users SET spent_total=0")
        conn.execute("UPDATE shops SET earned_total=0")
        conn.execute("UPDATE teams SET earned_total=0")
        conn.execute(
            """
            UPDATE users
            SET spent_total=COALESCE((
                SELECT SUM(amount) FROM transactions
                WHERE transactions.buyer_id=users.user_id AND transactions.status='approved'
            ), 0)
            """
        )
        conn.execute(
            """
            UPDATE shops
            SET earned_total=COALESCE((
                SELECT SUM(amount) FROM transactions
                WHERE transactions.shop_id=shops.id AND transactions.status='approved'
            ), 0)
            """
        )
        conn.execute(
            """
            UPDATE teams
            SET earned_total=COALESCE((
                SELECT SUM(amount) FROM transactions
                WHERE transactions.team_id=teams.id AND transactions.status='approved'
            ), 0)
            """
        )

    @staticmethod
    def normalize_tag(tag: str) -> str:
        tag = tag.strip().lower()
        if tag.startswith("https://vk.com/"):
            tag = tag.rsplit("/", 1)[-1]
        return tag.lstrip("@")

    def ensure_user(self, user_id: int, tag: Optional[str] = None) -> Dict[str, Any]:
        with self._get_conn() as conn:
            norm_tag = self.normalize_tag(tag or f"id{user_id}")
            conn.execute(
                """
                INSERT INTO users (user_id, role, tag)
                VALUES (?, 'unregistered', ?)
                ON CONFLICT(user_id) DO UPDATE SET tag=COALESCE(users.tag, excluded.tag)
                """,
                (user_id, norm_tag),
            )
            return dict(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def set_role(self, user_id: int, role: str, team_id: Optional[int] = None, tag: Optional[str] = None):
        with self._get_conn() as conn:
            norm_tag = self.normalize_tag(tag or f"id{user_id}")
            conn.execute(
                """
                INSERT INTO users (user_id, role, team_id, tag, is_pending)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    role=excluded.role,
                    team_id=excluded.team_id,
                    tag=COALESCE(users.tag, excluded.tag),
                    is_pending=0
                """,
                (user_id, role, team_id, norm_tag),
            )

    def set_role_by_tag(self, tag: str, role: str) -> int:
        norm_tag = self.normalize_tag(tag)
        with self._get_conn() as conn:
            existing = conn.execute("SELECT user_id FROM users WHERE tag=?", (norm_tag,)).fetchone()
            if existing:
                user_id = existing["user_id"]
            elif norm_tag.startswith("id") and norm_tag[2:].isdigit():
                user_id = int(norm_tag[2:])
            else:
                user_id = -random.randint(10**8, 10**9 - 1)
            conn.execute(
                """
                INSERT INTO users (user_id, role, tag, team_id, is_pending)
                VALUES (?, ?, ?, NULL, 0)
                ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, team_id=NULL, is_pending=0
                """,
                (user_id, role, norm_tag),
            )
            return user_id

    def find_user_by_tag(self, tag: str) -> Optional[Dict[str, Any]]:
        norm_tag = self.normalize_tag(tag)
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE tag=?", (norm_tag,)).fetchone()
            return dict(row) if row else None

    def create_team(self, name: str, commander_id: int) -> int:
        with self._get_conn() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id=?", (commander_id,)).fetchone()
            if not user:
                conn.execute(
                    "INSERT INTO users (user_id, role, tag) VALUES (?, 'commander', ?)",
                    (commander_id, f"id{commander_id}"),
                )
                user = conn.execute("SELECT * FROM users WHERE user_id=?", (commander_id,)).fetchone()
            if user["team_id"]:
                raise ValueError("У пользователя уже есть команда")

            code = self._new_code(conn)
            cur = conn.execute(
                "INSERT INTO teams (name, commander_id, reg_code) VALUES (?, ?, ?)",
                (name.strip(), commander_id, code),
            )
            team_id = cur.lastrowid
            conn.execute("UPDATE users SET role='commander', team_id=? WHERE user_id=?", (team_id, commander_id))
            conn.execute(
                "INSERT OR IGNORE INTO team_allowed_members (team_id, tag, name, linked_user_id) VALUES (?, ?, ?, ?)",
                (team_id, user["tag"] or f"id{commander_id}", "Командир", commander_id),
            )
            return team_id

    def _new_code(self, conn: sqlite3.Connection) -> str:
        for _ in range(100):
            code = str(random.randint(10000, 99999))
            if not conn.execute("SELECT 1 FROM teams WHERE reg_code=?", (code,)).fetchone():
                return code
        raise RuntimeError("Не удалось создать уникальный код")

    def get_team(self, team_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            return dict(row) if row else None

    def verify_team(self, team_id: int, is_verified: bool = True) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE teams SET is_verified=? WHERE id=?", (1 if is_verified else 0, team_id))
            return cur.rowcount > 0

    def list_teams(self, verified_only: bool = False) -> List[Dict[str, Any]]:
        query = "SELECT * FROM teams"
        params: tuple[Any, ...] = ()
        if verified_only:
            query += " WHERE is_verified=1"
        query += " ORDER BY name"
        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def add_allowed_member(self, team_id: int, tag: str, name: str = ""):
        norm_tag = self.normalize_tag(tag)
        with self._get_conn() as conn:
            user = conn.execute("SELECT user_id FROM users WHERE tag=?", (norm_tag,)).fetchone()
            linked_user_id = user["user_id"] if user else None
            conn.execute(
                """
                INSERT INTO team_allowed_members (team_id, tag, name, linked_user_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(team_id, tag) DO UPDATE SET name=excluded.name
                """,
                (team_id, norm_tag, name.strip(), linked_user_id),
            )

    def remove_allowed_member(self, team_id: int, tag: str):
        norm_tag = self.normalize_tag(tag)
        with self._get_conn() as conn:
            conn.execute("DELETE FROM team_allowed_members WHERE team_id=? AND tag=?", (team_id, norm_tag))

    def list_allowed_members(self, team_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM team_allowed_members WHERE team_id=? ORDER BY name, tag", (team_id,)
                ).fetchall()
            ]

    def try_register_by_code(self, user_id: int, code: str, tag: Optional[str] = None) -> Tuple[bool, str]:
        norm_tag = self.normalize_tag(tag or f"id{user_id}")
        with self._get_conn() as conn:
            team = conn.execute(
                "SELECT * FROM teams WHERE reg_code=? AND code_active=1", (code.strip(),)
            ).fetchone()
            if not team:
                return False, "Неверный или неактивный код команды."

            existing = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if existing and existing["team_id"]:
                return False, "Вы уже привязаны к команде."

            matches = conn.execute(
                "SELECT * FROM team_allowed_members WHERE tag=?", (norm_tag,)
            ).fetchall()
            if len(matches) != 1 or matches[0]["team_id"] != team["id"]:
                return (
                    False,
                    "Ошибка регистрации: ваш тег не найден в анкете этой команды или найден в нескольких анкетах. "
                    "Попробуйте еще раз или обратитесь к админу.",
                )

            conn.execute(
                """
                INSERT INTO users (user_id, role, team_id, tag, is_pending)
                VALUES (?, 'member', ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET role='member', team_id=excluded.team_id, tag=excluded.tag, is_pending=0
                """,
                (user_id, team["id"], norm_tag),
            )
            conn.execute(
                "UPDATE team_allowed_members SET linked_user_id=? WHERE id=?",
                (user_id, matches[0]["id"]),
            )
            return True, f"Вы присоединились к команде «{team['name']}»."

    def create_shop(self, team_id: int, name: str) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("INSERT INTO shops (team_id, name) VALUES (?, ?)", (team_id, name.strip()))
            return cur.lastrowid

    def list_shops(self, team_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM shops WHERE team_id=? AND is_active=1 ORDER BY name", (team_id,)
                ).fetchall()
            ]

    def add_item(self, shop_id: int, name: str, price: float) -> int:
        if price <= 0:
            raise ValueError("Цена должна быть больше нуля")
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO items (shop_id, name, price) VALUES (?, ?, ?)", (shop_id, name.strip(), price)
            )
            return cur.lastrowid

    def list_items(self, shop_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM items WHERE shop_id=? AND is_active=1 ORDER BY name", (shop_id,)
                ).fetchall()
            ]

    def get_item_for_purchase(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT i.id, i.name, i.price, i.shop_id, s.team_id, s.name AS shop_name, t.name AS team_name,
                       t.is_banned, t.is_verified
                FROM items i
                JOIN shops s ON s.id=i.shop_id
                JOIN teams t ON t.id=s.team_id
                WHERE i.id=? AND i.is_active=1 AND s.is_active=1
                """,
                (item_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_team_structure(self, team_id: int) -> Optional[Dict[str, Any]]:
        team = self.get_team(team_id)
        if not team:
            return None
        team["members"] = self.list_allowed_members(team_id)
        team["shops"] = []
        for shop in self.list_shops(team_id):
            shop["items"] = self.list_items(shop["id"])
            shop["balance"] = self.get_shop_balance(shop["id"])
            team["shops"].append(shop)
        return team

    def get_admins(self) -> List[int]:
        with self._get_conn() as conn:
            return [
                row["user_id"]
                for row in conn.execute("SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id").fetchall()
            ]

    def list_admin_users(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT user_id, role, team_id, tag, display_name FROM users WHERE role='admin' ORDER BY user_id"
                ).fetchall()
            ]

    def demote_admin(self, user_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE users SET role='unregistered', team_id=NULL WHERE user_id=? AND role='admin'",
                (user_id,),
            )
            return cur.rowcount > 0

    def sync_admins(self, allowed_ids: List[int], allowed_tags: List[str]) -> Dict[str, int]:
        normalized_tags = {self.normalize_tag(tag) for tag in allowed_tags if tag}
        normalized_tags.update({f"id{admin_id}" for admin_id in allowed_ids if admin_id})
        allowed_id_set = {admin_id for admin_id in allowed_ids if admin_id}

        with self._get_conn() as conn:
            for admin_id in allowed_id_set:
                conn.execute(
                    """
                    INSERT INTO users (user_id, role, tag, is_pending)
                    VALUES (?, 'admin', ?, 0)
                    ON CONFLICT(user_id) DO UPDATE SET role='admin', tag=COALESCE(users.tag, excluded.tag), is_pending=0
                    """,
                    (admin_id, f"id{admin_id}"),
                )

            for tag in normalized_tags:
                existing = conn.execute("SELECT user_id FROM users WHERE tag=?", (tag,)).fetchone()
                if existing:
                    conn.execute("UPDATE users SET role='admin', team_id=NULL, is_pending=0 WHERE user_id=?", (existing["user_id"],))

            removed = 0
            for row in conn.execute("SELECT user_id, tag FROM users WHERE role='admin'").fetchall():
                tag = self.normalize_tag(row["tag"] or f"id{row['user_id']}")
                if row["user_id"] not in allowed_id_set and tag not in normalized_tags:
                    conn.execute("UPDATE users SET role='unregistered', team_id=NULL WHERE user_id=?", (row["user_id"],))
                    removed += 1

            total = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            return {"active": int(total), "removed": removed}

    def create_pending_transaction(self, team_id: int, shop_id: int, item_id: int, buyer_id: int, amount: float) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            team = conn.execute("SELECT is_banned, is_verified FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team or team["is_banned"] or not team["is_verified"]:
                return None

            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, role, tag) VALUES (?, 'unregistered', ?)",
                (buyer_id, f"id{buyer_id}"),
            )
            counter = int(conn.execute("SELECT value FROM settings WHERE key='transaction_counter'").fetchone()["value"])
            admins = [
                row["user_id"]
                for row in conn.execute(
                    "SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id"
                ).fetchall()
            ]
            if not admins:
                return None
            assigned_admin = admins[counter % len(admins)]
            cur = conn.execute(
                """
                INSERT INTO transactions (team_id, shop_id, item_id, buyer_id, amount, assigned_admin_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (team_id, shop_id, item_id, buyer_id, amount, assigned_admin),
            )
            conn.execute("UPDATE settings SET value=? WHERE key='transaction_counter'", (str(counter + 1),))
            return {
                "id": cur.lastrowid,
                "team_id": team_id,
                "shop_id": shop_id,
                "item_id": item_id,
                "buyer_id": buyer_id,
                "amount": amount,
                "assigned_admin_id": assigned_admin,
            }

    def update_transaction_status(self, trans_id: int, status: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            old = conn.execute("SELECT * FROM transactions WHERE id=?", (trans_id,)).fetchone()
            if not old or old["status"] != "pending":
                return None
            cur = conn.execute(
                "UPDATE transactions SET status=?, approved_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                (status, trans_id),
            )
            row = conn.execute("SELECT * FROM transactions WHERE id=?", (trans_id,)).fetchone()
            if cur.rowcount == 0:
                return None
            if status == "approved":
                conn.execute("UPDATE users SET spent_total=COALESCE(spent_total, 0)+? WHERE user_id=?", (row["amount"], row["buyer_id"]))
                conn.execute("UPDATE shops SET earned_total=COALESCE(earned_total, 0)+? WHERE id=?", (row["amount"], row["shop_id"]))
                conn.execute("UPDATE teams SET earned_total=COALESCE(earned_total, 0)+? WHERE id=?", (row["amount"], row["team_id"]))
            return dict(row) if row else None

    def get_pending_transactions(self, assigned_admin_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if assigned_admin_id:
                rows = conn.execute(
                    "SELECT * FROM transactions WHERE status='pending' AND assigned_admin_id=? ORDER BY id",
                    (assigned_admin_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM transactions WHERE status='pending' ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def get_all_transactions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            ]

    def get_shop_balance(self, shop_id: int) -> float:
        with self._get_conn() as conn:
            income = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE shop_id=? AND status='approved'",
                (shop_id,),
            ).fetchone()[0]
            fines = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM fines WHERE shop_id=?", (shop_id,)
            ).fetchone()[0]
            return float(income - fines)

    def get_team_balance(self, team_id: int) -> float:
        with self._get_conn() as conn:
            income = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE team_id=? AND status='approved'",
                (team_id,),
            ).fetchone()[0]
            fines = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM fines WHERE team_id=?", (team_id,)
            ).fetchone()[0]
            return float(income - fines)

    def get_user_spent(self, user_id: int) -> float:
        with self._get_conn() as conn:
            row = conn.execute("SELECT COALESCE(spent_total, 0) FROM users WHERE user_id=?", (user_id,)).fetchone()
            return float(row[0]) if row else 0.0

    def get_fair_statistics(self) -> List[Dict[str, Any]]:
        teams = self.list_teams(False)
        result: List[Dict[str, Any]] = []
        for team in teams:
            structure = self.get_team_structure(team["id"])
            if not structure:
                continue
            structure["balance"] = self.get_team_balance(team["id"])
            structure["earned_total"] = float(team.get("earned_total") or 0)
            for member in structure["members"]:
                linked_user_id = member.get("linked_user_id")
                member["spent_total"] = self.get_user_spent(linked_user_id) if linked_user_id else 0.0
            result.append(structure)
        return sorted(result, key=lambda item: item["balance"], reverse=True)

    def add_fine(self, team_id: int, amount: float, reason: str, admin_id: int, shop_id: Optional[int] = None):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO fines (team_id, shop_id, amount, reason, created_by) VALUES (?, ?, ?, ?, ?)",
                (team_id, shop_id, abs(amount), reason.strip(), admin_id),
            )

    def toggle_ban(self, team_id: int, is_banned: bool) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE teams SET is_banned=? WHERE id=?", (1 if is_banned else 0, team_id))
            return cur.rowcount > 0

    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
