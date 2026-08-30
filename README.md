# SpotiNotifs

Spotify new-release notifier with a Flask OAuth enrollment server and a scheduled Discord notifier.

## Configuration

Create a Spotify developer application and configure this redirect URI:

```text
https://spotify.yashjani.com/callback
```

Set these env vars in Dokploy's Compose environment UI. For local development, copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Note |
| --- | --- | --- |
| `clientId` | yes | Spotify developer app client ID |
| `clientSecret` | yes | Spotify developer app client secret |
| `redirectUri` | yes | `https://spotify.yashjani.com/callback` in production |
| `authorizationUrl` | yes | Spotify authorize URL |
| `tokenUrl` | yes | Spotify token URL |
| `discord_token` | yes | Discord bot token used by the notifier |
| `owner_discord_username` | yes | Discord username to receive notifier errors |
| `ANCHOR_API_BASE_URL` | no | Anchor API origin; defaults to `https://anchor-api.yashjani.com` |
| `SPOTINOTIFS_CREDENTIAL_KEY` | for Anchor | Fernet key used to encrypt per-user Anchor API keys |

Generate the credential encryption key once and keep the same value across redeploys:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not rotate or remove this key while users have Anchor configured unless their stored API keys are replaced afterward.

## Anchor notifications

Anchor delivery is optional and supplements Discord.
Create an Anchor API key with only the `notifications:create` action, then use the **Anchor notifications** form on the SpotiNotifs home page to add or replace it.
The form verifies ownership by comparing the existing Spotify account with a fresh Spotify authorization before saving the encrypted key.
The same form can disable Anchor without exposing the stored credential.

Each completed scan creates one Routine, inform-only Anchor notification containing the final release, stray, catch-up, or no-release result.
Long release lists are reduced to one summary within Anchor's message limit, while Discord retains the complete digest.
Anchor delivery failures are logged but do not block Discord.

Do not set `PORT` in Dokploy. Compose sets `PORT=80` so the container behaves like a standard HTTP service. Direct local runs still default to `5000`.

## Dokploy deployment

Dokploy runs this service from the Compose resource at `./compose.yaml`.

Production settings:

- Domain: `https://spotify.yashjani.com`
- Service: `server`
- Container port: `80`
- Persistent volume: `spotinotifs_data` mounted at `/app/data`

The Compose file uses `expose` instead of host `ports`, so Traefik routes to the container without binding a host port. The app still opens `/app/users.db`, which is a symlink to `/app/data/users.db`; the named volume keeps that database persistent across redeploys.

## Notifier schedule

Use Dokploy Server Jobs instead of Compose Jobs or systemd timers. Dokploy Compose Jobs execute commands inside an existing service container, which makes notifier output show up as Dokploy schedule logs. Server Jobs should launch the dedicated `notifier` Compose service as a one-off container so Docker, Vector, and VictoriaLogs see normal container stdout/stderr logs.

Create two Server Jobs:

| Schedule | Command |
| --- | --- |
| `3 0 * * *` | `cd /etc/dokploy/compose/spotinotifs-service-lx3tci/code && docker compose -f compose.yaml run --no-deps notifier` |
| `30 23 * * *` | `cd /etc/dokploy/compose/spotinotifs-service-lx3tci/code && docker compose -f compose.yaml run --no-deps notifier` |

Update the path if Dokploy shows a different Compose directory. These jobs reuse the same image, environment, and `spotinotifs_data` volume as the web service, but run with `SERVICE_NAME=notifier` for logs.

Notifier and server logs are emitted as newline-delimited JSON to stdout.
The user-listing CLI reserves stdout for user records and sends diagnostics to stderr.
Optional logging env vars:

| Variable | Default | Note |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SERVICE_NAME` | Compose-defined | `server` or `notifier` |

## One-time volume migration

Run these on the Linux server before the first Dokploy deploy.

Stop the old systemd deployment:

```bash
sudo systemctl stop spotinotifs.service spotinotifs-notifier.timer || true
sudo systemctl disable spotinotifs.service spotinotifs-notifier.timer || true
```

Inspect existing state by name and size only:

```bash
find /home/yash/SpotiNotifs/data -maxdepth 1 -type f -exec ls -lh {} +
```

Create the Docker volume:

```bash
docker volume create spotinotifs_data
```

Copy old data into the volume:

```bash
docker run --rm \
  -v spotinotifs_data:/target \
  -v /home/yash/SpotiNotifs/data:/source:ro \
  alpine:3.20 \
  sh -c 'cp -a /source/. /target/'
```

Fix ownership for the runtime UID/GID used by the container:

```bash
docker run --rm \
  -v spotinotifs_data:/data \
  alpine:3.20 \
  sh -c 'chown -R 1000:1000 /data'
```

Verify migrated files by name and size only:

```bash
docker run --rm \
  -v spotinotifs_data:/target:ro \
  alpine:3.20 \
  find /target -maxdepth 1 -type f -exec ls -lh {} +
```

Expected important file:

```text
users.db
```

## Local commands

```bash
docker compose build
docker compose up server
docker compose run --no-deps notifier
```

## List users and recover missed releases

Run these commands from the project directory with its Python environment active.
The CLI uses the existing `users.db` next to `spotify.py`.
In the notifier container, that path points to the persistent database volume.

List registered users and copy their UUIDs:

```bash
python spotify.py users list
python spotify.py users list --format json
python spotify.py users list --format ids
```

The default table shows UUID, username, and Discord username.
JSON additionally includes `discord_id` and `playlist_id`, using lower snake_case keys such as `user_uuid`.
IDs output contains one UUID per line without headers.
All formats sort by username, then UUID, and never include tokens or notification history.
Listing requires no Spotify or Discord credentials and opens the database read-only.
A missing or unusable database is an error; listing never creates one.
The legacy `python sql.py scan` command remains available with its existing JSON log output.

Replace `UUID_A` and `UUID_B` below with IDs from the listing:

```bash
python spotify.py catchup 2026-08-20 2026-08-24 --user UUID_A
python spotify.py catchup 2026-08-20 2026-08-24 --user UUID_A --user UUID_B
python spotify.py catchup 2026-08-20 2026-08-24 --all-users
```

Catch-up requires exactly one selection method: repeated `--user`, `--users-from-stdin`, or `--all-users`.
Running catch-up without a selection now fails instead of notifying everyone.
Duplicate UUIDs are processed once; unknown UUIDs and empty selections fail before processing any user.
Users with different outage periods need separate invocations.

Filter JSON records with `jq` and pipe UUIDs directly into catch-up:

```bash
set -o pipefail
python spotify.py users list --format json |
  jq -r '.[] | select(.username == "alice") | .user_uuid' |
  python spotify.py catchup 2026-08-20 2026-08-24 --users-from-stdin
```

Stdin is read only with `--users-from-stdin`; blank lines are ignored.
Both dates are inclusive, and a single-day range is valid.
ISO dates (`YYYY-MM-DD`) and the legacy `MM-DD-YYYY` format are accepted.
Invalid dates, reversed ranges, and future dates are rejected.
Today is included when requested, using the notifier's local timezone (`America/New_York` in Compose).

**Catch-up executes immediately and replays the entire range.**
It sends Discord messages and updates each selected user's playlist if one is configured.
Configured Anchor notifications continue to mirror the final digest for selected users, retaining their existing best-effort delivery and idempotency behavior.
Repeated or overlapping runs can duplicate messages and playlist tracks, including releases already handled by today's daily run.
Catch-up leaves daily notification history untouched.
Recovery uses currently followed artists and releases currently available through Spotify; it cannot reconstruct historical follows or removed releases.

If a user's scan, playlist update, or message delivery fails, the run reports failure and continues with other selected users.
Existing owner error alerts remain enabled, even when the owner is outside the selected recovery users.
Partial delivery is not rolled back, so retrying a failed range may replay successful actions.

| Exit code | Meaning |
| --- | --- |
| `0` | Successful listing or notifier run |
| `1` | Operational failure, including database, authorization, scan, or delivery errors |
| `2` | Invalid arguments, dates, or user selection |

### Compose commands

Use `-T` to disable terminal formatting for pipelines and `--rm` to remove the one-off container after execution:

```bash
docker compose run --rm --no-deps -T notifier python spotify.py users list
docker compose run --rm --no-deps -T notifier python spotify.py catchup 2026-08-20 2026-08-24 --user UUID_A

set -o pipefail
docker compose run --rm --no-deps -T notifier python spotify.py users list --format json |
  jq -r '.[] | select(.username == "alice") | .user_uuid' |
  docker compose run --rm --no-deps -T notifier python spotify.py catchup 2026-08-20 2026-08-24 --users-from-stdin
```

No-argument `python spotify.py` still runs the scheduled daily notifier for everyone.
Existing Compose jobs and systemd schedules need no changes.

## Verification

Run the test suite with the project Python environment:

```bash
python -m unittest discover -s tests -v
```

Tests use temporary databases, mocked service boundaries, and a local HTTP fixture for the Anchor client and do not send real notifications.

## Legacy service commands

Useful legacy systemd commands are still available while the old deployment exists:

```bash
make status
make logs
make restart
```
