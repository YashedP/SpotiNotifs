import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from cryptography.fernet import Fernet

import anchor
import anchor_credentials
import spotify
import sql


class AnchorStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = sql.USERS_DB
        sql.USERS_DB = Path(self.temp_dir.name) / "users.db"
        self.credential_key = Fernet.generate_key().decode("ascii")
        self.environment = patch.dict(
            os.environ,
            {"SPOTINOTIFS_CREDENTIAL_KEY": self.credential_key},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        sql.USERS_DB = self.original_db
        self.temp_dir.cleanup()

    def test_existing_database_is_migrated_and_key_is_encrypted(self) -> None:
        with closing(sqlite3.connect(sql.USERS_DB)) as connection:
            connection.execute(
                """
                CREATE TABLE users (
                    user_UUID TEXT,
                    username TEXT,
                    discord_username TEXT,
                    refresh_token TEXT,
                    playlist_id TEXT,
                    discord_id TEXT,
                    user_items TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("user-id", "user", "discord", "refresh", None, None, "[]"),
            )
            connection.commit()

        sql.init_db()
        user = sql.get_user_by_username("user")
        self.assertIsNotNone(user)
        ciphertext = anchor_credentials.encrypt_api_key("anchor_secret_value")
        sql.update_user_anchor_api_key(user, ciphertext)

        updated = sql.get_user_by_username("user")
        self.assertEqual(anchor_credentials.decrypt_api_key(updated.anchor_api_key_ciphertext), "anchor_secret_value")
        self.assertNotIn("anchor_secret_value", updated.anchor_api_key_ciphertext)
        self.assertNotIn("anchor_secret_value", updated.safe_str())

    def test_missing_credential_key_is_reported_without_exposing_ciphertext(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaises(anchor_credentials.CredentialConfigurationError) as raised,
        ):
            anchor_credentials.encrypt_api_key("anchor_secret_value")

        self.assertNotIn("anchor_secret_value", str(raised.exception))


class AnchorClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.statuses = [201] * 10

        async def notification_handler(request: web.Request) -> web.Response:
            self.requests.append(
                {
                    "authorization": request.headers.get("Authorization"),
                    "payload": await request.json(),
                }
            )
            return web.json_response({}, status=self.statuses.pop(0))

        application = web.Application()
        application.router.add_post("/v1/notifications", notification_handler)
        self.runner = web.AppRunner(application)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    async def test_creates_routine_notification_with_stable_idempotency(self) -> None:
        notification = anchor.AnchorNotification(
            title="Spotify New Releases",
            message="Artist - Album",
            source_url="https://open.spotify.com/playlist/example",
            source_label="Open SpotiNotif playlist",
        )

        await anchor.create_notification("user-id", "anchor_api_key", notification, base_url=self.base_url)
        await anchor.create_notification("user-id", "anchor_api_key", notification, base_url=self.base_url)

        first = self.requests[0]
        second = self.requests[1]
        self.assertEqual(first["authorization"], "Bearer anchor_api_key")
        self.assertEqual(first["payload"]["initial_urgency"], "routine")
        self.assertEqual(first["payload"]["maximum_urgency"], "routine")
        self.assertEqual(first["payload"]["response_requirement"], "inform")
        self.assertEqual(first["payload"]["idempotency_key"], second["payload"]["idempotency_key"])

    async def test_retries_transient_response_with_same_idempotency_key(self) -> None:
        self.statuses = [503, 201]
        notification = anchor.AnchorNotification(title="Title", message="Message")

        with patch.object(anchor.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await anchor.create_notification("user-id", "anchor_api_key", notification, base_url=self.base_url)

        self.assertEqual(len(self.requests), 2)
        self.assertEqual(
            self.requests[0]["payload"]["idempotency_key"],
            self.requests[1]["payload"]["idempotency_key"],
        )
        sleep.assert_awaited_once()

    async def test_does_not_retry_terminal_client_error(self) -> None:
        self.statuses = [401]

        with self.assertRaises(anchor.AnchorDeliveryError) as raised:
            await anchor.create_notification(
                "user-id",
                "anchor_api_key",
                anchor.AnchorNotification(title="Title", message="Message"),
                base_url=self.base_url,
            )

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(len(self.requests), 1)


class AnchorDigestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.user = sql.User(
            "user-id",
            "user",
            "discord",
            "refresh",
            playlist_id="playlist-id",
        )

    def test_short_digest_contains_all_release_names_and_links(self) -> None:
        releases = {
            "Artist": {
                "album-id": {
                    "name": "Album",
                    "external_urls": {"spotify": "https://open.spotify.com/album/album-id"},
                }
            }
        }

        notification = spotify.build_anchor_notification(self.user, releases, 1)

        self.assertIn("Artist", notification.message)
        self.assertIn("Album", notification.message)
        self.assertIn("https://open.spotify.com/album/album-id", notification.message)
        self.assertEqual(notification.source_url, "https://open.spotify.com/playlist/playlist-id")

    def test_long_digest_becomes_one_bounded_summary(self) -> None:
        releases = {
            "Artist": {
                f"album-{index}": {
                    "name": f"Album number {index} with a deliberately descriptive name",
                    "external_urls": {"spotify": f"https://open.spotify.com/album/{index}"},
                }
                for index in range(100)
            }
        }

        notification = spotify.build_anchor_notification(self.user, releases, 100)

        self.assertLessEqual(len(notification.message), anchor.MAX_ANCHOR_MESSAGE_CHARACTERS)
        self.assertIn("100 new releases from 1 artists.", notification.message)
        self.assertIn("more. Full list was sent on Discord.", notification.message)

    def test_idempotency_changes_with_notification_content(self) -> None:
        first = anchor.notification_payload(
            "user-id",
            anchor.AnchorNotification(title="Title", message="First"),
        )
        second = anchor.notification_payload(
            "user-id",
            anchor.AnchorNotification(title="Title", message="Second"),
        )

        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])


class AnchorNotifierIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_final_digest_is_mirrored_and_anchor_failure_is_best_effort(self) -> None:
        user = sql.User("user-id", "user", "discord", "refresh")
        notification = anchor.AnchorNotification(title="Title", message="Digest")

        with (
            patch.object(
                spotify,
                "new_releases",
                new_callable=AsyncMock,
                return_value=("Discord digest", 2, notification),
            ),
            patch.object(spotify, "send_message", new_callable=AsyncMock) as send_message,
            patch.object(
                spotify,
                "send_anchor_notification",
                new_callable=AsyncMock,
                return_value="failed",
            ) as send_anchor_notification,
        ):
            succeeded, release_count, anchor_status = await spotify.process_user(user)

        self.assertTrue(succeeded)
        self.assertEqual(release_count, 2)
        self.assertEqual(anchor_status, "failed")
        self.assertEqual(send_message.await_count, 2)
        send_anchor_notification.assert_awaited_once_with(user, notification)


if __name__ == "__main__":
    unittest.main()
