import json
import sys
from pathlib import Path
from sqlite3 import connect
from typing import Generator

from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

USERS_DB = Path(__file__).resolve().parent / "users.db"


class User:
    def __init__(self, user_UUID, username, discord_username, refresh_token, playlist_id=None, discord_id=None, user_items=None, access_token=None):
        self.user_UUID = user_UUID
        self.username = username
        self.discord_username = discord_username
        self.refresh_token = refresh_token
        self.playlist_id = playlist_id
        self.discord_id = discord_id
        self.access_token = access_token
        if user_items is None:
            self.user_items = set()
        elif isinstance(user_items, str):
            try:
                self.user_items = set(json.loads(user_items))
            except (json.JSONDecodeError, TypeError):
                self.user_items = set()
        else:
            self.user_items = set(user_items)

    def __str__(self):
        return self.safe_str()

    def safe_str(self):
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, discord_id={self.discord_id}, playlist_id={self.playlist_id})"

    def log_context(self) -> dict[str, str | None]:
        return {
            "user_uuid": self.user_UUID,
            "username": self.username,
            "discord_username": self.discord_username,
            "discord_id": self.discord_id,
            "playlist_id": self.playlist_id,
        }

    def add_item(self, item):
        self.user_items.add(item)

    def remove_item(self, item):
        self.user_items.discard(item)

    def has_item(self, item):
        return item in self.user_items

    def get_items(self):
        return self.user_items.copy()

    def reset_items(self):
        self.user_items = set()

    def get_items_json(self):
        return json.dumps(list(self.user_items))


def init_db() -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS users (user_UUID TEXT, username TEXT, discord_username TEXT, refresh_token TEXT, playlist_id TEXT, discord_id TEXT, user_items TEXT)")
        logger.info("Database initialized", extra={"event": "db_initialized", "db_path": str(USERS_DB)})
    except Exception:
        logger.exception("Error initializing database", extra={"event": "db_init_failed", "db_path": str(USERS_DB)})
        raise


def add_user(user: User) -> bool:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
            if cursor.fetchone():
                logger.info("User already exists", extra={"event": "db_user_duplicate", **user.log_context()})
                return False
            cursor.execute(
                "INSERT INTO users (user_UUID, username, discord_username, refresh_token, playlist_id, discord_id, user_items) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user.user_UUID, user.username, user.discord_username, user.refresh_token, user.playlist_id, user.discord_id, user.get_items_json()),
            )
        logger.info("User added", extra={"event": "db_user_added", **user.log_context()})
        return True
    except Exception:
        logger.exception("Error adding user", extra={"event": "db_user_add_failed", **user.log_context()})
        raise


def get_all_users() -> list[User]:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            users = cursor.fetchall()
        return [User(user[0], user[1], user[2], user[3], user[4], user[5], user[6]) for user in users]
    except Exception:
        logger.exception("Error getting all users", extra={"event": "db_get_all_users_failed"})
        raise


def iterate_users_one_by_one() -> Generator[User, None, None]:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            users = cursor.fetchall()
        logger.info("Users loaded for iteration", extra={"event": "db_users_loaded", "user_count": len(users)})
        for user in users:
            yield User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
    except Exception:
        logger.exception("Error iterating users", extra={"event": "db_iterate_users_failed"})
        raise


def get_user_by_uuid(user_UUID: str) -> User | None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_UUID = ?", (user_UUID,))
            user = cursor.fetchone()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception:
        logger.exception("Error getting user by UUID", extra={"event": "db_get_user_by_uuid_failed", "user_uuid": user_UUID})
        raise


def delete_user_by_uuid(user_UUID: str) -> bool:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE user_UUID = ?", (user_UUID,))
            deleted_count = cursor.rowcount
        logger.info("User deleted", extra={"event": "db_user_deleted", "user_uuid": user_UUID, "deleted_count": deleted_count})
        return True
    except Exception:
        logger.exception("Error deleting user by UUID", extra={"event": "db_delete_user_failed", "user_uuid": user_UUID})
        raise


def update_user_refresh_token(user: User, refresh_token: str) -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET refresh_token = ? WHERE user_UUID = ?", (refresh_token, user.user_UUID))
            updated_count = cursor.rowcount
        logger.info("User refresh token updated", extra={"event": "db_refresh_token_updated", "updated_count": updated_count, **user.log_context()})
    except Exception:
        logger.exception("Error updating user refresh token", extra={"event": "db_refresh_token_update_failed", **user.log_context()})
        raise


def update_user_discord_id(user: User, discord_id: str) -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET discord_id = ? WHERE user_UUID = ?", (discord_id, user.user_UUID))
            updated_count = cursor.rowcount
        logger.info("User Discord ID updated", extra={"event": "db_discord_id_updated", "updated_count": updated_count, **user.log_context()})
    except Exception:
        logger.exception("Error updating user Discord ID", extra={"event": "db_discord_id_update_failed", **user.log_context()})
        raise


def update_user_playlist_id(user: User, playlist_id: str) -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET playlist_id = ? WHERE user_UUID = ?", (playlist_id, user.user_UUID))
            updated_count = cursor.rowcount
        logger.info("User playlist ID updated", extra={"event": "db_playlist_id_updated", "updated_count": updated_count, **user.log_context()})
    except Exception:
        logger.exception("Error updating user playlist ID", extra={"event": "db_playlist_id_update_failed", **user.log_context()})
        raise


def update_user_items(user: User) -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET user_items = ? WHERE user_UUID = ?", (user.get_items_json(), user.user_UUID))
            updated_count = cursor.rowcount
        logger.info(
            "User items updated",
            extra={"event": "db_user_items_updated", "updated_count": updated_count, "item_count": len(user.user_items), **user.log_context()},
        )
    except Exception:
        logger.exception("Error updating user items", extra={"event": "db_user_items_update_failed", **user.log_context()})
        raise


def get_user_by_discord_username(discord_username: str) -> User | None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE discord_username = ?", (discord_username,))
            user = cursor.fetchone()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception:
        logger.exception(
            "Error getting user by Discord username",
            extra={"event": "db_get_user_by_discord_username_failed", "discord_username": discord_username},
        )
        raise


def get_user_by_username(username: str) -> User | None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception:
        logger.exception("Error getting user by username", extra={"event": "db_get_user_by_username_failed", "username": username})
        raise


def scan_users() -> None:
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            users = cursor.fetchall()
        logger.info("Users scanned", extra={"event": "db_users_scanned", "user_count": len(users)})
        for user in users:
            scanned_user = User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
            logger.info("Scanned user", extra={"event": "db_user_scanned", **scanned_user.log_context()})
    except Exception:
        logger.exception("Error scanning users", extra={"event": "db_scan_users_failed"})
        raise


def data_migration():
    try:
        with connect(USERS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]

            if "user_items" not in columns:
                logger.info("Adding user_items column", extra={"event": "db_user_items_migration_started"})
                cursor.execute("ALTER TABLE users ADD COLUMN user_items TEXT DEFAULT '[]'")
                logger.info("Added user_items column", extra={"event": "db_user_items_migration_succeeded"})
            else:
                logger.info("user_items column already exists", extra={"event": "db_user_items_migration_skipped"})
    except Exception:
        logger.exception("Error during data migration", extra={"event": "db_data_migration_failed"})
        raise


if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        if sys.argv[1] == "scan":
            scan_users()
        elif sys.argv[1] == "data_migration":
            data_migration()
    else:
        logger.info("Usage: python sql.py [scan | data_migration]", extra={"event": "db_cli_usage"})
