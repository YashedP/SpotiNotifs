from sqlite3 import connect
from typing import Generator

class User:
    def __init__(self, user_UUID, username, discord_username, refresh_token):
        self.user_UUID = user_UUID
        self.username = username
        self.discord_username = discord_username
        self.refresh_token = refresh_token

def init_db() -> None:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_UUID TEXT, username TEXT, discord_username TEXT, access_token TEXT, refresh_token TEXT)")
    conn.commit()
    conn.close()

def add_user(user_UUID: str, username: str, discord_username: str, refresh_token: str) -> None:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_UUID, username, discord_username, refresh_token) VALUES (?, ?, ?, ?)", (user_UUID, username, discord_username, refresh_token))
    conn.commit()
    conn.close()

def get_all_users() -> list[User]:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    return [User(user[0], user[1], user[2], user[3]) for user in users]

def iterate_users_one_by_one() -> Generator[User, None, None]:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    
    while True:
        user = cursor.fetchone()
        if user is None:
            conn.close()
            return
        yield User(user[0], user[1], user[2], user[3])

def get_user_by_uuid(user_UUID: str) -> User:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_UUID = ?", (user_UUID,))
    user = cursor.fetchone()
    conn.close()
    return User(user[0], user[1], user[2], user[3])

def update_user_refresh_token(user: User, refresh_token: str) -> None:
    conn = connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET refresh_token = ? WHERE user_UUID = ?", (refresh_token, user.user_UUID))
    conn.commit()
    conn.close()