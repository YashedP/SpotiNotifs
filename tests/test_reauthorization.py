import sqlite3
import tempfile
import unittest
from contextlib import closing
from os import environ
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

import sql
from anchor_credentials import decrypt_api_key


class ReauthorizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_db = sql.USERS_DB
        sql.USERS_DB = Path(cls.temp_dir.name) / "users.db"

        global add_user
        import add_user

    @classmethod
    def tearDownClass(cls) -> None:
        sql.USERS_DB = cls.original_db
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.environment = patch.dict(
            environ,
            {"SPOTINOTIFS_CREDENTIAL_KEY": Fernet.generate_key().decode("ascii")},
        )
        self.environment.start()
        sql.init_db()
        with closing(sqlite3.connect(sql.USERS_DB)) as conn:
            conn.execute("DELETE FROM users")
            conn.commit()
        add_user.users.clear()

    def tearDown(self) -> None:
        self.environment.stop()

    def add_existing_user(self) -> sql.User:
        existing_user = sql.User(
            "existing-user-id",
            "existing-user",
            "existing-discord-user",
            "old-refresh-token",
            playlist_id="existing-playlist-id",
            discord_id="existing-discord-id",
            user_items={"existing-album-id"},
            anchor_api_key_ciphertext="existing-anchor-ciphertext",
        )
        self.assertTrue(sql.add_user(existing_user))
        return existing_user

    def test_existing_username_reauthorizes_without_replacing_user_state(self) -> None:
        self.add_existing_user()

        with (
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(add_user.OAuth2, "get_access_token", return_value={"refresh_token": "new-refresh-token"}),
            patch.object(add_user.OAuth2, "refresh_access_token") as refresh_access_token,
            patch.object(add_user.spotify, "create_playlist") as create_playlist,
        ):
            client = add_user.app.test_client()
            auth_response = client.post(
                "/reauth",
                data={"username": "existing-user"},
            )
            state = next(iter(add_user.users))
            self.assertEqual(add_user.users[state]["flow"], "reauth")
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(auth_response.status_code, 302)
        self.assertEqual(callback_response.status_code, 200)
        self.assertIn("Your existing settings were preserved", callback_response.get_data(as_text=True))

        updated_user = sql.get_user_by_uuid("existing-user-id")
        self.assertIsNotNone(updated_user)
        self.assertEqual(updated_user.refresh_token, "new-refresh-token")
        self.assertEqual(updated_user.playlist_id, "existing-playlist-id")
        self.assertEqual(updated_user.discord_id, "existing-discord-id")
        self.assertEqual(updated_user.get_items(), {"existing-album-id"})
        self.assertEqual(updated_user.anchor_api_key_ciphertext, "existing-anchor-ciphertext")
        self.assertEqual(len(sql.get_all_users()), 1)
        refresh_access_token.assert_not_called()
        create_playlist.assert_not_called()

    def test_unknown_username_cannot_start_reauthorization(self) -> None:
        with patch.object(add_user.OAuth2, "create_authorization_url") as create_authorization_url:
            response = add_user.app.test_client().post("/reauth", data={"username": "missing-user"})

        self.assertEqual(response.status_code, 404)
        self.assertIn("Connect a new user instead", response.get_data(as_text=True))
        self.assertEqual(add_user.users, {})
        create_authorization_url.assert_not_called()

    def test_signup_without_playlist_creates_user_without_playlist(self) -> None:
        with (
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(add_user.OAuth2, "get_access_token", return_value={"refresh_token": "new-refresh-token"}),
            patch.object(add_user.OAuth2, "refresh_access_token") as refresh_access_token,
            patch.object(add_user.spotify, "create_playlist", new_callable=AsyncMock) as create_playlist,
        ):
            client = add_user.app.test_client()
            auth_response = client.post(
                "/auth",
                data={"username": "new-user", "discord_username": "new-discord-user"},
            )
            state = next(iter(add_user.users))
            self.assertEqual(add_user.users[state]["flow"], "signup")
            self.assertFalse(add_user.users[state]["want_playlist"])
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(auth_response.status_code, 302)
        self.assertEqual(callback_response.status_code, 200)
        created_user = sql.get_user_by_username("new-user")
        self.assertIsNotNone(created_user)
        self.assertIsNone(created_user.playlist_id)
        refresh_access_token.assert_not_called()
        create_playlist.assert_not_called()

    def test_signup_with_playlist_creates_playlist(self) -> None:
        with (
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(add_user.OAuth2, "get_access_token", return_value={"refresh_token": "new-refresh-token"}),
            patch.object(add_user.OAuth2, "refresh_access_token", return_value={"access_token": "new-access-token"}) as refresh_access_token,
            patch.object(add_user.spotify, "create_playlist", new_callable=AsyncMock, return_value="new-playlist-id") as create_playlist,
        ):
            client = add_user.app.test_client()
            auth_response = client.post(
                "/auth",
                data={"username": "new-user", "discord_username": "new-discord-user", "want_playlist": "on"},
            )
            state = next(iter(add_user.users))
            self.assertTrue(add_user.users[state]["want_playlist"])
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(auth_response.status_code, 302)
        self.assertEqual(callback_response.status_code, 200)
        created_user = sql.get_user_by_username("new-user")
        self.assertIsNotNone(created_user)
        self.assertEqual(created_user.playlist_id, "new-playlist-id")
        refresh_access_token.assert_called_once_with("new-refresh-token")
        create_playlist.assert_awaited_once()

    def test_home_page_separates_signup_and_reauthorization_forms(self) -> None:
        response = add_user.app.test_client().get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/auth"', body)
        self.assertIn('action="/reauth"', body)
        self.assertIn("Connect a new user", body)
        self.assertIn("Reconnect an existing user", body)
        self.assertIn('id="signup-want-playlist" name="want_playlist">', body)
        self.assertIn('action="/anchor-settings"', body)
        self.assertIn('type="password" id="anchor-settings-api-key"', body)

    def test_duplicate_signup_does_not_start_reauthorization(self) -> None:
        self.add_existing_user()

        with patch.object(add_user.OAuth2, "create_authorization_url") as create_authorization_url:
            response = add_user.app.test_client().post(
                "/auth",
                data={"username": "existing-user", "discord_username": "existing-discord-user"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("Use Reconnect Spotify instead", response.get_data(as_text=True))
        self.assertEqual(add_user.users, {})
        create_authorization_url.assert_not_called()

    def test_matching_spotify_account_can_set_encrypted_anchor_key(self) -> None:
        self.add_existing_user()
        with (
            patch.object(add_user.OAuth2, "refresh_access_token", return_value={"access_token": "existing-access-token"}),
            patch.object(add_user.OAuth2, "get_spotify_user_id", return_value="spotify-account-id"),
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(
                add_user.OAuth2,
                "get_access_token",
                return_value={"access_token": "new-access-token", "refresh_token": "new-refresh-token"},
            ),
        ):
            client = add_user.app.test_client()
            settings_response = client.post(
                "/anchor-settings",
                data={
                    "username": "existing-user",
                    "action": "set",
                    "api_key": "anchor_api_key_secret",
                },
            )
            state = next(iter(add_user.users))
            pending = add_user.users[state]
            self.assertNotIn("anchor_api_key_secret", pending["anchor_api_key_ciphertext"])
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(settings_response.status_code, 302)
        self.assertEqual(callback_response.status_code, 200)
        updated_user = sql.get_user_by_username("existing-user")
        self.assertEqual(updated_user.refresh_token, "new-refresh-token")
        self.assertEqual(decrypt_api_key(updated_user.anchor_api_key_ciphertext), "anchor_api_key_secret")

    def test_mismatched_spotify_account_cannot_replace_anchor_key(self) -> None:
        self.add_existing_user()
        with (
            patch.object(add_user.OAuth2, "refresh_access_token", return_value={"access_token": "existing-access-token"}),
            patch.object(add_user.OAuth2, "get_spotify_user_id", side_effect=["expected-account", "different-account"]),
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(
                add_user.OAuth2,
                "get_access_token",
                return_value={"access_token": "new-access-token", "refresh_token": "new-refresh-token"},
            ),
        ):
            client = add_user.app.test_client()
            client.post(
                "/anchor-settings",
                data={"username": "existing-user", "action": "set", "api_key": "replacement-key"},
            )
            state = next(iter(add_user.users))
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(callback_response.status_code, 403)
        unchanged_user = sql.get_user_by_username("existing-user")
        self.assertEqual(unchanged_user.refresh_token, "old-refresh-token")
        self.assertEqual(unchanged_user.anchor_api_key_ciphertext, "existing-anchor-ciphertext")

    def test_matching_spotify_account_can_disable_anchor(self) -> None:
        self.add_existing_user()
        with (
            patch.object(add_user.OAuth2, "refresh_access_token", return_value={"access_token": "existing-access-token"}),
            patch.object(add_user.OAuth2, "get_spotify_user_id", return_value="spotify-account-id"),
            patch.object(add_user.OAuth2, "create_authorization_url", return_value="https://accounts.spotify.test/authorize"),
            patch.object(
                add_user.OAuth2,
                "get_access_token",
                return_value={"access_token": "new-access-token", "refresh_token": "new-refresh-token"},
            ),
        ):
            client = add_user.app.test_client()
            client.post(
                "/anchor-settings",
                data={"username": "existing-user", "action": "disable"},
            )
            state = next(iter(add_user.users))
            callback_response = client.get(f"/callback?code=authorization-code&state={state}")

        self.assertEqual(callback_response.status_code, 200)
        self.assertIsNone(sql.get_user_by_username("existing-user").anchor_api_key_ciphertext)


if __name__ == "__main__":
    unittest.main()
