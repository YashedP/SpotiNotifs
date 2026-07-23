import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import sql


class ReauthorizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        sql.USERS_DB = Path(cls.temp_dir.name) / "users.db"

        global add_user
        import add_user

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        sql.init_db()
        with closing(sqlite3.connect(sql.USERS_DB)) as conn:
            conn.execute("DELETE FROM users")
            conn.commit()
        add_user.users.clear()

    def test_existing_username_reauthorizes_without_replacing_user_state(self) -> None:
        existing_user = sql.User(
            "existing-user-id",
            "existing-user",
            "existing-discord-user",
            "old-refresh-token",
            playlist_id="existing-playlist-id",
            discord_id="existing-discord-id",
            user_items={"existing-album-id"},
        )
        self.assertTrue(sql.add_user(existing_user))

        with (
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(add_user.OAuth2, "get_access_token", return_value={"refresh_token": "new-refresh-token"}),
            patch.object(add_user.OAuth2, "refresh_access_token") as refresh_access_token,
            patch.object(add_user.spotify, "create_playlist") as create_playlist,
        ):
            client = add_user.app.test_client()
            auth_response = client.post(
                "/auth",
                data={
                    "username": "existing-user",
                    "discord_username": "existing-discord-user",
                    "want_playlist": "on",
                },
            )
            state = next(iter(add_user.users))
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(auth_response.status_code, 302)
        self.assertEqual(callback_response.status_code, 200)
        self.assertIn("Successfully reauthenticated user", callback_response.get_data(as_text=True))

        updated_user = sql.get_user_by_uuid("existing-user-id")
        self.assertIsNotNone(updated_user)
        self.assertEqual(updated_user.refresh_token, "new-refresh-token")
        self.assertEqual(updated_user.playlist_id, "existing-playlist-id")
        self.assertEqual(updated_user.discord_id, "existing-discord-id")
        self.assertEqual(updated_user.get_items(), {"existing-album-id"})
        self.assertEqual(len(sql.get_all_users()), 1)
        refresh_access_token.assert_not_called()
        create_playlist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
