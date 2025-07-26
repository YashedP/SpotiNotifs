from sqlite3 import connect
import sys
from typing import Generator
from pathlib import Path

USERS_DB = Path(__file__).resolve().parent / "users.db"

import json

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
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, refresh_token={self.refresh_token}, access_token={self.access_token}, discord_id={self.discord_id}, playlist_id={self.playlist_id}, user_items={self.user_items})"

    def safe_str(self):
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, discord_id={self.discord_id}, playlist_id={self.playlist_id})"
    
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
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS users (user_UUID TEXT, username TEXT, discord_username TEXT, refresh_token TEXT, playlist_id TEXT, discord_id TEXT, user_items TEXT)")
        conn.commit()
        conn.close()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error initializing database: {e}")
        raise

def add_user(user: User) -> bool:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (user.username,))
        if cursor.fetchone():
            print(f"User {user.username} already exists")
            return False
        cursor.execute("INSERT INTO users (user_UUID, username, discord_username, refresh_token, playlist_id, discord_id, user_items) VALUES (?, ?, ?, ?, ?, ?, ?)", (user.user_UUID, user.username, user.discord_username, user.refresh_token, user.playlist_id, user.discord_id, user.get_items_json()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error adding user: {e}")
        raise

def get_all_users() -> list[User]:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        return [User(user[0], user[1], user[2], user[3], user[4], user[5], user[6]) for user in users]
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error getting all users: {e}")
        raise

def iterate_users_one_by_one() -> Generator[User, None, None]:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        conn.close()
        for user in users:
            yield User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error iterating users: {e}")
        raise

def get_user_by_uuid(user_UUID: str) -> User:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_UUID = ?", (user_UUID,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error getting user by UUID: {e}")
        raise

def delete_user_by_uuid(user_UUID: str) -> bool:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_UUID = ?", (user_UUID,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error deleting user by UUID: {e}")
        raise

def update_user_refresh_token(user: User, refresh_token: str) -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET refresh_token = ? WHERE user_UUID = ?", (refresh_token, user.user_UUID))
        conn.commit()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error updating user refresh token: {e}")
        raise

def update_user_discord_id(user: User, discord_id: str) -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET discord_id = ? WHERE user_UUID = ?", (discord_id, user.user_UUID))
        conn.commit()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error updating user discord ID: {e}")
        raise

def update_user_playlist_id(user: User, playlist_id: str) -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET playlist_id = ? WHERE user_UUID = ?", (playlist_id, user.user_UUID))
        conn.commit()
        conn.close()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error updating user playlist ID: {e}")
        raise

def update_user_items(user: User) -> None:
    """Update the user's items in the database"""
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET user_items = ? WHERE user_UUID = ?", (user.get_items_json(), user.user_UUID))
        conn.commit()
        conn.close()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error updating user items: {e}")
        raise
    
def get_user_by_discord_username(discord_username: str) -> User:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE discord_username = ?", (discord_username,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error getting user by discord username: {e}")
        raise

def get_user_by_username(username: str) -> User:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5], user[6])
        return None
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error getting user by username: {e}")
        raise
    
def scan_users() -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        conn.close()
        for user in users:
            print(User(user[0], user[1], user[2], user[3], user[4], user[5], user[6]))
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error scanning users: {e}")
        raise

def data_migration():
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'user_items' not in columns:
            print("Adding user_items column to users table...")
            cursor.execute("ALTER TABLE users ADD COLUMN user_items TEXT DEFAULT '[]'")
            conn.commit()
            print("Successfully added user_items column")
        else:
            print("user_items column already exists")
            
        conn.close()
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error during data migration: {e}")
        raise

if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        if sys.argv[1] == "scan":
            scan_users()
        elif sys.argv[1] == "data_migration":
            data_migration()
    else:
        print("Usage: python sql.py [scan | data_migration]")
