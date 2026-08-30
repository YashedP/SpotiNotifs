import asyncio
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import discord
import requests

import spotify
import sql

TODAY = date(2026, 8, 30)
CATCHUP = ["catchup", "2026-08-20", "2026-08-24"]
USER_A = "00000000-0000-0000-0000-000000000001"
USER_B = "00000000-0000-0000-0000-000000000002"


def album(album_id, released):
    return {
        "id": album_id,
        "name": album_id,
        "album_type": "album",
        "release_date": released,
        "external_urls": {"spotify": f"https://open.spotify.test/album/{album_id}"},
    }


class NotifierFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(sql, "USERS_DB", Path(self.temp.name) / "users.db"))
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(setattr, root, "handlers", old_handlers)
        root.handlers = [logging.NullHandler()]
        sql.init_db()
        self.users = [
            sql.User(USER_A, "alice", "alice-discord", "secret-refresh-a", "playlist-a", "101", {"daily-a"}),
            sql.User(USER_B, "bob", "bob-discord", "secret-refresh-b", "playlist-b", "102", {"daily-b"}),
        ]
        for user in reversed(self.users):
            sql.add_user(user)
        self.enterContext(patch.dict(os.environ, {"discord_token": "fake-discord-token"}, clear=True))
        self.enterContext(patch.object(spotify, "load_dotenv", return_value=False))
        self.clock = self.enterContext(patch.object(spotify, "datetime", wraps=datetime))
        self.set_hour(8)
        self.refresh = self.enterContext(patch.object(
            spotify.OAuth2, "refresh_access_token", return_value={"access_token": "fake-access"}
        ))
        self.http_get = self.enterContext(patch.object(requests, "get", side_effect=AssertionError("Unexpected HTTP GET")))
        self.http_post = self.enterContext(patch.object(requests, "post", side_effect=AssertionError("Unexpected HTTP POST")))
        self.bot = Mock()
        self.bot.close = AsyncMock()
        self.bot.guilds = []
        self.deliveries = []

        async def fetch_user(discord_id):
            async def send(message):
                self.deliveries.append((discord_id, message))
            return SimpleNamespace(send=send)

        self.bot.fetch_user = AsyncMock(side_effect=fetch_user)
        self.bot.event.side_effect = lambda callback: setattr(self.bot, callback.__name__, callback) or callback
        self.bot.run.side_effect = lambda *args, **kwargs: asyncio.run(self.bot.on_ready())
        self.client = self.enterContext(patch.object(discord, "Client", return_value=self.bot))

    def set_hour(self, hour):
        self.clock.now.return_value = Mock()
        self.clock.now.return_value.astimezone.return_value = datetime(2026, 8, 30, hour, tzinfo=UTC)

    def invoke(self, args, stdin=""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(sys, "stdin", io.StringIO(stdin)):
            try:
                code = spotify.main(args)
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def configure_api(self, releases=None):
        releases = releases if releases is not None else [album("recovered", "2026-08-22")]
        self.api_calls = []

        def sync_request(user, url, params=None, body=None, method="GET"):
            self.api_calls.append((user.user_UUID, url, method, body))
            if url == spotify.FOLLOWING_ARTISTS_URL:
                return {"artists": {"items": [{"id": "artist", "name": "Artist"}], "cursors": {"after": None}}}
            if url == spotify.ME_PLAYLISTS_URL:
                return {"items": [{"id": user.playlist_id}], "next": None}
            if "/albums/" in url:
                return {"tracks": {"items": [{"uri": "spotify:track:recovered"}], "next": None}}
            if method == "POST":
                return {"snapshot_id": "snapshot"}
            raise AssertionError(f"Unexpected endpoint: {url}")

        self.sync_request = self.enterContext(patch.object(spotify, "spotify_request_sync", side_effect=sync_request))
        self.async_request = self.enterContext(patch.object(
            spotify, "spotify_request", new=AsyncMock(return_value={"items": releases, "next": None})
        ))

class CliTest(NotifierFixture):
    def test_listing_formats_are_sorted_safe_and_read_only(self):
        before = sql.USERS_DB.read_bytes()
        with patch.object(sql, "init_db") as initialize:
            for output_format in ("table", "json", "ids"):
                with self.subTest(output_format=output_format):
                    code, output, errors = self.invoke(["users", "list", "--format", output_format])
                    self.assertEqual(code, 0)
                    self.assertEqual(errors, "")
                    self.assertLess(output.index(USER_A), output.index(USER_B))
                    self.assertNotIn("secret-refresh", output)
                    self.assertNotIn("daily-a", output)
                    if output_format == "json":
                        records = json.loads(output)
                        self.assertEqual(set(records[0]), {"user_uuid", "username", "discord_username", "discord_id", "playlist_id"})
                    elif output_format == "ids":
                        self.assertEqual(output, f"{USER_A}\n{USER_B}\n")
                    else:
                        self.assertIn("DISCORD USERNAME", output)
            initialize.assert_not_called()
        self.assertEqual(sql.USERS_DB.read_bytes(), before)
        self.client.assert_not_called()
        self.refresh.assert_not_called()

    def test_help_and_listing_need_no_credentials(self):
        os.environ.clear()
        for args in (["--help"], ["catchup", "--help"], ["users", "list"]):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(args)[0], 0)
        self.client.assert_not_called()

    def test_missing_database_is_not_created(self):
        sql.USERS_DB.unlink()
        code, output, errors = self.invoke(["users", "list", "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("Unable to list users", errors)
        self.assertFalse(sql.USERS_DB.exists())
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.assertFalse(sql.USERS_DB.exists())
        self.client.assert_not_called()

    def test_unusable_database_fails_without_changes(self):
        sql.USERS_DB.write_bytes(b"not a sqlite database")
        code, output, errors = self.invoke(["users", "list", "--format", "ids"])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("Unable to list users", errors)
        self.assertEqual(sql.USERS_DB.read_bytes(), b"not a sqlite database")

    def test_empty_database_listing_succeeds_but_catchup_fails(self):
        with closing(sqlite3.connect(sql.USERS_DB)) as conn, conn:
            conn.execute("DELETE FROM users")
        self.assertEqual(self.invoke(["users", "list", "--format", "json"]), (0, "[]\n", ""))
        self.assertEqual(self.invoke(["users", "list", "--format", "ids"]), (0, "", ""))
        self.assertEqual(self.invoke(CATCHUP + ["--all-users"])[0], 2)
        self.client.assert_not_called()

    def test_invalid_selection_has_no_side_effects(self):
        before = sql.USERS_DB.read_bytes()
        invalid = [
            (CATCHUP, ""),
            (CATCHUP + ["--user", USER_A, "--all-users"], ""),
            (CATCHUP + ["--user", USER_A, "--users-from-stdin"], ""),
            (CATCHUP + ["--user", USER_A, "--user", "missing"], ""),
            (CATCHUP + ["--users-from-stdin"], "\n  \n"),
            (CATCHUP + ["--users-from-stdin"], f"{USER_A}\nmissing\n"),
            (CATCHUP + ["--user", ""], ""),
        ]
        for args, stdin in invalid:
            with self.subTest(args=args, stdin=stdin):
                code, _, errors = self.invoke(args, stdin)
                self.assertEqual(code, 2)
                self.assertIn("error:", errors)
        self.assertEqual(sql.USERS_DB.read_bytes(), before)
        self.client.assert_not_called()
        self.refresh.assert_not_called()

    def test_multiple_selectors_and_stdin_are_deduplicated(self):
        cases = [
            (["--user", USER_B, "--user", USER_A, "--user", USER_A], ""),
            (["--users-from-stdin"], f" {USER_B} \n\n{USER_A}\n{USER_A}\n"),
            (["--all-users"], "ignored input"),
        ]
        with patch.object(spotify, "run_notifier", return_value=0) as run:
            for selection, stdin in cases:
                with self.subTest(selection=selection):
                    self.assertEqual(self.invoke(CATCHUP + selection, stdin)[0], 0)
                    self.assertEqual([u.user_UUID for u in run.call_args.args[0]], [USER_A, USER_B])

    def test_dates_validate_before_user_loading(self):
        invalid_ranges = [
            ("2026-08-24", "2026-08-20"),
            ("2026-02-29", "2026-03-01"),
            ("2026-08", "2026-08-24"),
            ("20260820", "2026-08-24"),
            ("2026-08-20", "2026-08-31"),
        ]
        with patch.object(sql, "get_all_users") as load:
            for start, end in invalid_ranges:
                with self.subTest(start=start, end=end):
                    self.assertEqual(self.invoke(["catchup", start, end, "--user", USER_A])[0], 2)
            load.assert_not_called()
        self.client.assert_not_called()

    def test_legacy_leap_day_and_today_ranges(self):
        cases = [
            ("08-20-2026", "08-24-2026", date(2026, 8, 20), date(2026, 8, 24)),
            ("2024-02-29", "2024-02-29", date(2024, 2, 29), date(2024, 2, 29)),
            ("2026-08-30", "2026-08-30", TODAY, TODAY),
        ]
        with patch.object(spotify, "run_notifier", return_value=0) as run:
            for start, end, expected_start, expected_end in cases:
                with self.subTest(start=start):
                    self.assertEqual(self.invoke(["catchup", start, end, "--user", USER_A])[0], 0)
                    options = run.call_args.args[1]
                    self.assertEqual((options.start_date, options.end_date), (expected_start, expected_end))

    def test_missing_discord_token_is_operational_failure(self):
        os.environ.clear()
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.client.assert_not_called()

    def test_listing_from_real_process_pipes_into_selection(self):
        project = Path(spotify.__file__).parent
        for name in ("spotify.py", "sql.py", "OAuth2.py", "logging_config.py", "anchor.py", "anchor_credentials.py"):
            shutil.copyfile(project / name, Path(self.temp.name) / name)
        result = subprocess.run(
            [sys.executable, "spotify.py", "users", "list", "--format", "ids"],
            cwd=self.temp.name, env={}, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"{USER_A}\n{USER_B}\n")
        with patch.object(spotify, "run_notifier", return_value=0) as run:
            self.assertEqual(self.invoke(CATCHUP + ["--users-from-stdin"], result.stdout)[0], 0)
            self.assertEqual([u.user_UUID for u in run.call_args.args[0]], [USER_A, USER_B])


class RecoveryTest(NotifierFixture):
    def test_anchor_recovery_is_limited_to_selected_user(self):
        self.configure_api()
        for user in self.users:
            sql.update_user_anchor_api_key(user, "encrypted-anchor-key")
        before = sql.USERS_DB.read_bytes()
        with (
            patch.object(spotify, "decrypt_api_key", return_value="fake-anchor-key"),
            patch.object(spotify.anchor, "create_notification", new_callable=AsyncMock) as send_anchor,
        ):
            code, output, _ = self.invoke(CATCHUP + ["--user", USER_A])
        self.assertEqual(code, 0)
        send_anchor.assert_awaited_once()
        self.assertEqual(send_anchor.await_args.args[:2], (USER_A, "fake-anchor-key"))
        self.assertIn("2026-08-20 through 2026-08-24", send_anchor.await_args.args[2].title)
        summary = next(json.loads(line) for line in output.splitlines() if json.loads(line).get("event") == "notifier_user_loop_finished")
        self.assertEqual(summary["anchor_succeeded_user_count"], 1)
        self.assertEqual(sql.USERS_DB.read_bytes(), before)

    def test_selected_user_receives_inclusive_range_without_changing_daily_history(self):
        self.configure_api([
            album("before", "2026-08-19"), album("first", "2026-08-20"),
            album("last", "2026-08-24"), album("after", "2026-08-25"),
            album("month", "2026-08"), album("year", "2026"),
            album("invalid", "2026-02-30"), album("missing", None),
        ])
        before = sql.USERS_DB.read_bytes()
        code, output, errors = self.invoke(CATCHUP + ["--user", USER_A])
        self.assertEqual(code, 0, output + errors)
        self.assertEqual({call[0] for call in self.api_calls}, {USER_A})
        self.refresh.assert_called_once_with("secret-refresh-a")
        self.assertEqual({recipient for recipient, _ in self.deliveries}, {"101"})
        message = self.deliveries[-1][1]
        self.assertIn("2026-08-20 through 2026-08-24", message)
        self.assertIn("[first]", message)
        self.assertIn("[last]", message)
        for excluded in ("before", "after", "month", "year", "invalid", "missing"):
            self.assertNotIn(f"[{excluded}]", message)
        self.assertEqual(sql.USERS_DB.read_bytes(), before)
        self.assertEqual(len([call for call in self.api_calls if call[2] == "POST"]), 1)
        self.bot.close.assert_awaited_once()

    def test_catchup_replays_without_resetting_or_persisting_history(self):
        self.configure_api()
        with patch.object(sql.User, "reset_items") as reset, patch.object(sql, "update_user_items") as update:
            for _ in range(2):
                self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 0)
            reset.assert_not_called()
            update.assert_not_called()
        self.assertEqual(len([call for call in self.api_calls if call[2] == "POST"]), 2)
        self.assertEqual(len(self.deliveries), 4)

    def test_no_releases_reports_range_for_morning_and_evening(self):
        self.configure_api([])
        for hour in (8, 23):
            with self.subTest(hour=hour):
                self.set_hour(hour)
                self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 0)
                self.assertEqual(self.deliveries[-1][1], "No new releases from 2026-08-20 through 2026-08-24!\n\n")
        self.assertFalse(any(call[2] == "POST" for call in self.api_calls))

    def test_catchup_follows_album_pagination(self):
        self.configure_api()
        self.async_request.side_effect = [
            {"items": [album("first", "2026-08-20")], "next": "https://api.spotify.test/next"},
            {"items": [album("last", "2026-08-24")], "next": None},
        ]
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 0)
        self.assertEqual(self.async_request.await_count, 2)
        self.assertEqual(self.async_request.await_args_list[1].args[1], "https://api.spotify.test/next")
        self.assertIn("[last]", self.deliveries[-1][1])

    def test_daily_no_argument_run_keeps_all_users_and_daily_history_behavior(self):
        self.configure_api([album("daily-release", TODAY.isoformat())])
        self.assertEqual(self.invoke([])[0], 0)
        self.assertEqual({recipient for recipient, _ in self.deliveries}, {"101", "102"})
        self.assertEqual(sql.get_user_by_uuid(USER_A).get_items(), {"daily-release"})
        self.set_hour(23)
        self.assertEqual(self.invoke([])[0], 0)
        self.assertEqual(len([call for call in self.api_calls if call[2] == "POST"]), 2)
        self.assertIn("No strays today!", self.deliveries[-1][1])

    def test_failed_user_does_not_prevent_next_user_and_exit_is_nonzero(self):
        self.configure_api()
        self.refresh.side_effect = [RuntimeError("Expired authorization"), {"access_token": "fake-access"}]
        code, output, _ = self.invoke(CATCHUP + ["--all-users"])
        self.assertEqual(code, 1)
        records = [json.loads(line) for line in output.splitlines()]
        summary = next(record for record in records if record["event"] == "notifier_user_loop_finished")
        self.assertEqual(summary["failed_user_count"], 1)
        self.assertEqual(summary["successful_user_count"], 1)
        self.assertEqual({call[0] for call in self.api_calls}, {USER_B})

    def test_partial_artist_scan_fails_without_sending_partial_results(self):
        self.configure_api()
        with patch.object(spotify, "get_all_artists", return_value=[{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]):
            self.async_request.side_effect = [
                {"items": [album("recovered", "2026-08-22")], "next": None},
                RuntimeError("Album page unavailable"),
            ]
            self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.assertEqual(len(self.deliveries), 1)
        self.assertFalse(any(call[2] == "POST" for call in self.api_calls))
        self.assertEqual(sql.get_user_by_uuid(USER_A).get_items(), {"daily-a"})

    def test_playlist_failure_is_reported(self):
        self.configure_api()
        request = self.sync_request.side_effect

        def fail_playlist_write(user, url, params=None, body=None, method="GET"):
            if method == "POST":
                raise RuntimeError("Playlist write failed")
            return request(user, url, params=params, body=body, method=method)

        self.sync_request.side_effect = fail_playlist_write
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.assertEqual(len(self.deliveries), 1)

    def test_discord_start_or_result_failure_is_reported(self):
        self.configure_api()
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                sender = AsyncMock(side_effect=[None] * (fail_on - 1) + [RuntimeError("DM failed")])
                self.bot.fetch_user.side_effect = None
                self.bot.fetch_user.return_value = SimpleNamespace(send=sender)
                self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)

    def test_owner_receives_error_alert(self):
        self.configure_api()
        os.environ["owner_discord_username"] = "bob-discord"
        self.refresh.side_effect = RuntimeError("Token refresh failed")
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.assertEqual(self.deliveries[-1][0], "102")
        self.assertIn("Error processing user", self.deliveries[-1][1])

    def test_repeated_ready_events_only_process_once(self):
        self.configure_api()

        async def repeated_ready():
            await asyncio.gather(self.bot.on_ready(), self.bot.on_ready())
            await self.bot.on_ready()

        self.bot.run.side_effect = lambda *args, **kwargs: asyncio.run(repeated_ready())
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 0)
        self.refresh.assert_called_once()
        self.bot.close.assert_awaited_once()

    def test_bot_login_failure_returns_nonzero(self):
        self.bot.run.side_effect = RuntimeError("Login failed")
        self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)

    def test_unexpected_loop_failure_closes_bot(self):
        with patch.object(spotify, "process_user", new=AsyncMock(side_effect=RuntimeError("Unexpected failure"))):
            self.assertEqual(self.invoke(CATCHUP + ["--user", USER_A])[0], 1)
        self.bot.close.assert_awaited_once()


class DeliveryTest(NotifierFixture):
    def test_no_playlist_does_not_make_spotify_calls(self):
        self.users[0].playlist_id = None
        with patch.object(spotify, "spotify_request_sync") as request:
            asyncio.run(spotify.add_to_playlist(self.users[0], {"Artist": {"album": album("album", "2026-08-22")}}))
            request.assert_not_called()

    def test_playlist_batches_never_send_empty_requests(self):
        releases = {"Artist": {"album": album("album", "2026-08-22")}}
        for track_count, expected_batches in ((0, []), (99, [99]), (100, [100]), (101, [100, 1]), (200, [100, 100])):
            with self.subTest(track_count=track_count):
                def request(user, url, track_count=track_count, **kwargs):
                    return {"tracks": {"items": [{"uri": f"spotify:track:{i}"} for i in range(track_count)], "next": None}}
                with patch.object(spotify, "check_playlist_exists", new=AsyncMock(return_value=True)), patch.object(spotify, "spotify_request_sync", side_effect=request) as call:
                    asyncio.run(spotify.add_to_playlist(self.users[0], releases))
                    batches = [len(entry.kwargs["body"]["uris"]) for entry in call.call_args_list if entry.kwargs.get("method") == "POST"]
                    self.assertEqual(batches, expected_batches)

    def test_closed_dms_and_unknown_discord_ids_raise(self):
        response = SimpleNamespace(status=403, reason="Forbidden")
        for error in (discord.Forbidden(response, "Closed DMs"), discord.NotFound(response, "Unknown user")):
            with self.subTest(error=type(error).__name__):
                self.bot.fetch_user.side_effect = error
                with self.assertRaises(type(error)):
                    asyncio.run(spotify.send_message(self.users[0], "message", self.bot))

    def test_missing_guild_member_raises(self):
        self.users[0].discord_id = None
        with self.assertRaisesRegex(RuntimeError, "member was not found"):
            asyncio.run(spotify.send_message(self.users[0], "message", self.bot))

    def test_username_lookup_caches_id_and_sends(self):
        self.users[0].discord_id = None
        member = SimpleNamespace(name="alice-discord", id=101, send=AsyncMock())
        self.bot.guilds = [SimpleNamespace(members=[member])]
        asyncio.run(spotify.send_message(self.users[0], "message", self.bot))
        member.send.assert_awaited_once_with("message")
        self.assertEqual(self.users[0].discord_id, "101")
        self.assertEqual(sql.get_user_by_uuid(USER_A).discord_id, "101")


class RequestTest(NotifierFixture):
    def response(self, status, retry_after="0"):
        response = requests.Response()
        response.status_code = status
        response.url = "https://api.spotify.test/endpoint"
        response.headers["Retry-After"] = retry_after
        response._content = b'{"items": []}'
        return response

    def test_sync_server_errors_retry_then_raise(self):
        self.http_get.side_effect = None
        self.http_get.return_value = self.response(503)
        with patch.object(spotify.time, "sleep"), self.assertRaisesRegex(RuntimeError, "exhausted retries"):
            spotify.spotify_request_sync(self.users[0], "https://api.spotify.test/endpoint")
        self.assertEqual(self.http_get.call_count, 3)

    def test_sync_rate_limit_retries_and_can_succeed(self):
        self.http_get.side_effect = [self.response(429, "2"), self.response(200)]
        with patch.object(spotify.time, "sleep") as sleep:
            self.assertEqual(spotify.spotify_request_sync(self.users[0], "https://api.spotify.test/endpoint"), {"items": []})
            sleep.assert_called_once_with(2)

    def test_sync_forbidden_and_long_rate_limit_raise_inside_event_loop(self):
        async def request():
            return spotify.spotify_request_sync(self.users[0], "https://api.spotify.test/endpoint")

        for status, expected in ((403, requests.HTTPError), (429, RuntimeError)):
            with self.subTest(status=status):
                self.http_get.side_effect = None
                self.http_get.return_value = self.response(status, "61")
                with self.assertRaises(expected):
                    asyncio.run(request())

    def test_async_retries_and_terminal_errors(self):
        for status, retry_after, calls in ((503, "0", 3), (429, "0", 3), (403, "0", 1), (429, "61", 1)):
            with self.subTest(status=status, retry_after=retry_after):
                error = aiohttp.ClientResponseError(Mock(real_url="https://api.spotify.test/endpoint"), (), status=status, headers={"Retry-After": retry_after})
                session = Mock()
                context = Mock()
                context.__aenter__ = AsyncMock(side_effect=error)
                context.__aexit__ = AsyncMock(return_value=False)
                session.get.return_value = context
                expected = aiohttp.ClientResponseError if status == 403 else RuntimeError
                with patch.object(spotify.asyncio, "sleep", new=AsyncMock()), self.assertRaises(expected):
                    asyncio.run(spotify.spotify_request(self.users[0], "https://api.spotify.test/endpoint", session))
                self.assertEqual(session.get.call_count, calls)

    def test_partial_followed_artist_fetch_is_not_success(self):
        with patch.object(spotify, "spotify_request_sync", side_effect=[
            {"artists": {"items": [{"id": "first"}], "cursors": {"after": "next"}}},
            requests.ConnectionError("Page unavailable"),
        ]), self.assertRaises(requests.ConnectionError):
            spotify.get_all_artists(self.users[0])


if __name__ == "__main__":
    unittest.main()
