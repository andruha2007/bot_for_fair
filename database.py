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
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
                    is_active BOOLEAN DEFAULT 1,
                    is_verified BOOLEAN DEFAULT 0,
                    is_banned BOOLEAN DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    is_verified BOOLEAN DEFAULT 0
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

                CREATE TABLE IF NOT EXISTS role_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('commander', 'phantom')),
                    code TEXT NOT NULL,
                    is_used BOOLEAN DEFAULT 0,
                    created_by INTEGER REFERENCES users(user_id),
                    used_by INTEGER REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_at TIMESTAMP,
                    UNIQUE(role, code)
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
                CREATE INDEX IF NOT EXISTS idx_role_keys_lookup ON role_keys(role, code, is_used);
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
        if "is_verified" not in shop_cols:
            conn.execute("ALTER TABLE shops ADD COLUMN is_verified BOOLEAN DEFAULT 1")
        if "is_banned" not in shop_cols:
            conn.execute("ALTER TABLE shops ADD COLUMN is_banned BOOLEAN DEFAULT 0")
        if "earned_total" not in shop_cols:
            conn.execute("ALTER TABLE shops ADD COLUMN earned_total REAL DEFAULT 0")

        item_cols = columns("items")
        if "is_active" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN is_active BOOLEAN DEFAULT 1")
        if "is_verified" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN is_verified BOOLEAN DEFAULT 1")

        fine_cols = columns("fines")
        if "shop_id" not in fine_cols:
            conn.execute("ALTER TABLE fines ADD COLUMN shop_id INTEGER REFERENCES shops(id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK(role IN ('commander', 'phantom')),
                code TEXT NOT NULL,
                is_used BOOLEAN DEFAULT 0,
                created_by INTEGER REFERENCES users(user_id),
                used_by INTEGER REFERENCES users(user_id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP,
                UNIQUE(role, code)
            )
            """
        )
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
        tag = str(tag or "").strip().lower()
        if tag.startswith("[") and "|" in tag:
            tag = tag[1:].split("|", 1)[0]
        tag = tag.split("?", 1)[0].rstrip("/")
        for prefix in ("https://vk.com/", "http://vk.com/", "vk.com/"):
            if tag.startswith(prefix):
                tag = tag[len(prefix):]
                break
        tag = tag.lstrip("@")
        if tag.isdigit():
            tag = f"id{tag}"
        return tag

    @staticmethod
    def tag_to_user_id(norm_tag: str) -> Optional[int]:
        return int(norm_tag[2:]) if norm_tag.startswith("id") and norm_tag[2:].isdigit() else None

    def ensure_user(self, user_id: int, tag: Optional[str] = None, display_name: Optional[str] = None) -> Dict[str, Any]:
        with self._get_conn() as conn:
            norm_tag = self.normalize_tag(tag or f"id{user_id}")
            conn.execute(
                """
                INSERT INTO users (user_id, role, tag, display_name)
                VALUES (?, 'unregistered', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    tag=CASE
                        WHEN excluded.tag IS NOT NULL
                         AND (users.tag IS NULL OR users.tag='' OR users.tag='id' || users.user_id)
                        THEN excluded.tag
                        ELSE users.tag
                    END,
                    display_name=COALESCE(excluded.display_name, users.display_name)
                """,
                (user_id, norm_tag, display_name.strip() if display_name else None),
            )
            return dict(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def user_label(self, user_id: int) -> str:
        user = self.get_user(user_id)
        if not user:
            return f"id{user_id}"
        return user.get("display_name") or (f"@{user.get('tag')}" if user.get("tag") else f"id{user_id}")

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

    @staticmethod
    def normalize_code(code: str) -> str:
        return "".join(ch for ch in str(code).strip() if ch.isdigit())

    def add_role_key(self, role: str, code: str, created_by: Optional[int] = None) -> Tuple[bool, str]:
        if role not in ("commander", "phantom"):
            raise ValueError("Unsupported role key")
        norm_code = self.normalize_code(code)
        if not (4 <= len(norm_code) <= 12):
            return False, "Код должен быть числовым, от 4 до 12 цифр."
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO role_keys (role, code, created_by) VALUES (?, ?, ?)",
                    (role, norm_code, created_by),
                )
            except sqlite3.IntegrityError:
                active = conn.execute(
                    "SELECT 1 FROM role_keys WHERE role=? AND code=? AND is_used=0",
                    (role, norm_code),
                ).fetchone()
                if active:
                    return False, "Такой активный код уже есть. Введите другой."
                conn.execute(
                    """
                    UPDATE role_keys
                    SET is_used=0, created_by=?, used_by=NULL, used_at=NULL, created_at=CURRENT_TIMESTAMP
                    WHERE role=? AND code=?
                    """,
                    (created_by, role, norm_code),
                )
            return True, norm_code

    def consume_role_key(self, user_id: int, role: str, code: str) -> Tuple[bool, str]:
        if role not in ("commander", "phantom"):
            raise ValueError("Unsupported role key")
        norm_code = self.normalize_code(code)
        if not norm_code:
            return False, "Введите числовой код."
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, role, tag)
                VALUES (?, 'unregistered', ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, f"id{user_id}"),
            )
            cur = conn.execute(
                """
                UPDATE role_keys
                SET is_used=1, used_by=?, used_at=CURRENT_TIMESTAMP
                WHERE role=? AND code=? AND is_used=0
                """,
                (user_id, role, norm_code),
            )
            if cur.rowcount == 0:
                return False, "Неверный или уже использованный код."

            conn.execute(
                """
                INSERT INTO users (user_id, role, team_id, tag, is_pending)
                VALUES (?, ?, NULL, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    role=excluded.role,
                    team_id=NULL,
                    tag=COALESCE(users.tag, excluded.tag),
                    is_pending=0
                """,
                (user_id, role, f"id{user_id}"),
            )
            return True, "ok"

    def leave_phantom(self, user_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE users SET role='unregistered', team_id=NULL WHERE user_id=? AND role='phantom'",
                (user_id,),
            )
            return cur.rowcount > 0

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
        query += " ORDER BY id"
        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def add_allowed_member(self, team_id: int, tag: str, name: str = "") -> Tuple[bool, str]:
        norm_tag = self.normalize_tag(tag)
        if not norm_tag:
            return False, "Введите тег или id участника."
        explicit_user_id = self.tag_to_user_id(norm_tag)
        if (norm_tag.startswith("id") and explicit_user_id is None) or explicit_user_id == 0:
            return False, "Ошибка ввода id. Используйте id123456 или 123456."
        with self._get_conn() as conn:
            team = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            if not team:
                return False, "Команда не найдена."

            if explicit_user_id:
                conn.execute(
                    """
                    INSERT INTO users (user_id, role, tag)
                    VALUES (?, 'unregistered', ?)
                    ON CONFLICT(user_id) DO UPDATE SET tag=COALESCE(users.tag, excluded.tag)
                    """,
                    (explicit_user_id, norm_tag),
                )

            user = conn.execute(
                "SELECT * FROM users WHERE tag=? OR user_id=?",
                (norm_tag, explicit_user_id if explicit_user_id is not None else -1),
            ).fetchone()
            linked_user_id = user["user_id"] if user else None

            if user and user["role"] in ("admin", "commander", "phantom"):
                return False, f"Нельзя добавить пользователя с ролью {user['role']}."
            if user and user["team_id"] and user["team_id"] != team_id:
                busy_team = conn.execute("SELECT name FROM teams WHERE id=?", (user["team_id"],)).fetchone()
                return False, f"Этот человек занят командой {busy_team['name'] if busy_team else user['team_id']}."

            busy = conn.execute(
                """
                SELECT t.name
                FROM team_allowed_members m
                JOIN teams t ON t.id=m.team_id
                WHERE m.team_id<>? AND (m.tag=? OR (m.linked_user_id IS NOT NULL AND m.linked_user_id=?))
                LIMIT 1
                """,
                (team_id, norm_tag, linked_user_id if linked_user_id is not None else -1),
            ).fetchone()
            if busy:
                return False, f"Этот человек занят командой {busy['name']}."

            conn.execute(
                """
                INSERT INTO team_allowed_members (team_id, tag, name, linked_user_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(team_id, tag) DO UPDATE SET name=excluded.name, linked_user_id=excluded.linked_user_id
                """,
                (team_id, norm_tag, name.strip(), linked_user_id),
            )
            return True, f"Участник @{norm_tag} добавлен в анкету."

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

            if existing and existing["role"] in ("admin", "commander", "phantom"):
                return False, f"Пользователь с ролью {existing['role']} не может войти в команду."

            existing_tag = conn.execute("SELECT tag FROM users WHERE user_id=?", (user_id,)).fetchone()
            tags = {norm_tag, f"id{user_id}"}
            if existing_tag and existing_tag["tag"]:
                tags.add(self.normalize_tag(existing_tag["tag"]))

            placeholders = ",".join("?" for _ in tags)
            matches = conn.execute(
                f"""
                SELECT * FROM team_allowed_members
                WHERE tag IN ({placeholders}) OR linked_user_id=?
                """,
                (*tags, user_id),
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
            cur = conn.execute(
                "INSERT INTO shops (team_id, name, is_verified, is_active, is_banned) VALUES (?, ?, 0, 1, 0)",
                (team_id, name.strip()),
            )
            return cur.lastrowid

    def list_shops(self, team_id: int, verified_only: bool = True, include_inactive: bool = False) -> List[Dict[str, Any]]:
        where = ["team_id=?"]
        params: list[Any] = [team_id]
        if not include_inactive:
            where.append("is_active=1")
        if verified_only:
            where.append("is_verified=1")
            where.append("is_banned=0")
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM shops WHERE {' AND '.join(where)} ORDER BY name", tuple(params)
                ).fetchall()
            ]

    def add_item(self, shop_id: int, name: str, price: float) -> int:
        if price <= 0 or int(price) != price:
            raise ValueError("Цена должна быть целым числом больше нуля")
        price = int(price)
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO items (shop_id, name, price, is_verified, is_active) VALUES (?, ?, ?, 0, 1)",
                (shop_id, name.strip(), price),
            )
            return cur.lastrowid

    def list_items(self, shop_id: int, verified_only: bool = True, include_inactive: bool = False) -> List[Dict[str, Any]]:
        where = ["shop_id=?"]
        params: list[Any] = [shop_id]
        if not include_inactive:
            where.append("is_active=1")
        if verified_only:
            where.append("is_verified=1")
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM items WHERE {' AND '.join(where)} ORDER BY name", tuple(params)
                ).fetchall()
            ]

    def get_shop(self, shop_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
            return dict(row) if row else None

    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return dict(row) if row else None

    def verify_shop(self, shop_id: int, is_verified: bool = True) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE shops SET is_verified=? WHERE id=? AND is_active=1", (1 if is_verified else 0, shop_id))
            return cur.rowcount > 0

    def verify_item(self, item_id: int, is_verified: bool = True) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE items SET is_verified=? WHERE id=? AND is_active=1", (1 if is_verified else 0, item_id))
            return cur.rowcount > 0

    def toggle_shop_ban(self, shop_id: int, is_banned: bool) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE shops SET is_banned=? WHERE id=? AND is_active=1", (1 if is_banned else 0, shop_id))
            return cur.rowcount > 0

    def delete_shop(self, shop_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE shops SET is_active=0 WHERE id=?", (shop_id,))
            conn.execute("UPDATE items SET is_active=0 WHERE shop_id=?", (shop_id,))
            return cur.rowcount > 0

    def delete_item(self, item_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE items SET is_active=0 WHERE id=?", (item_id,))
            return cur.rowcount > 0

    def get_item_for_purchase(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT i.id, i.name, i.price, i.shop_id, s.team_id, s.name AS shop_name, t.name AS team_name,
                       t.is_banned, t.is_verified, s.is_banned AS shop_is_banned,
                       s.is_verified AS shop_is_verified, i.is_verified AS item_is_verified
                FROM items i
                JOIN shops s ON s.id=i.shop_id
                JOIN teams t ON t.id=s.team_id
                WHERE i.id=? AND i.is_active=1 AND i.is_verified=1
                  AND s.is_active=1 AND s.is_verified=1 AND s.is_banned=0
                """,
                (item_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_team_structure(self, team_id: int, include_pending: bool = False) -> Optional[Dict[str, Any]]:
        team = self.get_team(team_id)
        if not team:
            return None
        team["members"] = self.list_allowed_members(team_id)
        team["shops"] = []
        for shop in self.list_shops(team_id, verified_only=not include_pending):
            shop["items"] = self.list_items(shop["id"], verified_only=not include_pending)
            shop["balance"] = self.get_shop_balance(shop["id"])
            team["shops"].append(shop)
        return team

    def get_admins(self) -> List[int]:
        with self._get_conn() as conn:
            return [
                row["user_id"]
                for row in conn.execute("SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id").fetchall()
            ]

    def get_available_admins(self) -> List[int]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id").fetchall()
            result = []
            for row in rows:
                lunch = conn.execute("SELECT value FROM settings WHERE key=?", (f"admin_lunch_{row['user_id']}",)).fetchone()
                if not lunch or lunch["value"] != "1":
                    result.append(row["user_id"])
            return result

    def set_admin_lunch(self, user_id: int, on_lunch: bool):
        self.set_setting(f"admin_lunch_{user_id}", "1" if on_lunch else "0")

    def is_admin_on_lunch(self, user_id: int) -> bool:
        return self.get_setting(f"admin_lunch_{user_id}", "0") == "1"

    def next_available_admin(self) -> Optional[int]:
        with self._get_conn() as conn:
            admins = []
            for row in conn.execute("SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id").fetchall():
                lunch = conn.execute("SELECT value FROM settings WHERE key=?", (f"admin_lunch_{row['user_id']}",)).fetchone()
                if not lunch or lunch["value"] != "1":
                    admins.append(row["user_id"])
            if not admins:
                return None
            row = conn.execute("SELECT value FROM settings WHERE key='admin_assignment_counter'").fetchone()
            counter = int(row["value"]) if row else int(conn.execute("SELECT value FROM settings WHERE key='transaction_counter'").fetchone()["value"])
            admin_id = admins[counter % len(admins)]
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_assignment_counter', ?)", (str(counter + 1),))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('transaction_counter', ?)", (str(counter + 1),))
            return admin_id

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
            admins = []
            for row in conn.execute("SELECT user_id FROM users WHERE role='admin' AND user_id > 0 ORDER BY user_id").fetchall():
                lunch = conn.execute("SELECT value FROM settings WHERE key=?", (f"admin_lunch_{row['user_id']}",)).fetchone()
                if not lunch or lunch["value"] != "1":
                    admins.append(row["user_id"])
            if not admins:
                return None
            row = conn.execute("SELECT value FROM settings WHERE key='admin_assignment_counter'").fetchone()
            fallback = conn.execute("SELECT value FROM settings WHERE key='transaction_counter'").fetchone()
            counter = int(row["value"]) if row else int(fallback["value"])
            assigned_admin = admins[counter % len(admins)]
            cur = conn.execute(
                """
                INSERT INTO transactions (team_id, shop_id, item_id, buyer_id, amount, assigned_admin_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (team_id, shop_id, item_id, buyer_id, amount, assigned_admin),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_assignment_counter', ?)", (str(counter + 1),))
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
