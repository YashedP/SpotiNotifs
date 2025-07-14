from sqlite3 import connect
from typing import Generator
from pathlib import Path

USERS_DB = Path(__file__).resolve().parent / "users.db"

class User:
    def __init__(self, user_UUID, username, discord_username, refresh_token, playlist_id=None, discord_id=None, access_token=None):
        self.user_UUID = user_UUID
        self.username = username
        self.discord_username = discord_username
        self.refresh_token = refresh_token
        self.playlist_id = playlist_id
        self.discord_id = discord_id
        self.access_token = access_token
        
    def __str__(self):
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, refresh_token={self.refresh_token}, access_token={self.access_token}, discord_id={self.discord_id}, playlist_id={self.playlist_id})"

    def safe_str(self):
        return f"User(user_UUID={self.user_UUID}, username={self.username}, discord_username={self.discord_username}, discord_id={self.discord_id}, playlist_id={self.playlist_id})"

def init_db() -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS users (user_UUID TEXT, username TEXT, discord_username TEXT, refresh_token TEXT, playlist_id TEXT, discord_id TEXT)")
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
        cursor.execute("INSERT INTO users (user_UUID, username, discord_username, refresh_token, playlist_id, discord_id) VALUES (?, ?, ?, ?, ?, ?)", (user.user_UUID, user.username, user.discord_username, user.refresh_token, user.playlist_id, user.discord_id))
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
        return [User(user[0], user[1], user[2], user[3], user[4], user[5]) for user in users]
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
            yield User(user[0], user[1], user[2], user[3], user[4], user[5])
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
            return User(user[0], user[1], user[2], user[3], user[4], user[5])
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
    
def get_user_by_name(username: str) -> User:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE discord_username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user:
            return User(user[0], user[1], user[2], user[3], user[4], user[5])
        return None
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error getting user by name: {e}")
        raise

def scan_users() -> None:
    try:
        conn = connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall()
        conn.close()
        for user in users:
            print(User(user[0], user[1], user[2], user[3], user[4], user[5]))
    except Exception as e:
        if conn:
            conn.close()
        print(f"Error scanning users: {e}")
        raise
