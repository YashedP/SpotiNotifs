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

Use Dokploy Schedule Jobs instead of systemd timers.

Create two Compose Jobs against this Compose resource:

| Schedule | Service | Command |
| --- | --- | --- |
| `3 0 * * *` | `server` | `python spotify.py` |
| `30 23 * * *` | `server` | `python spotify.py` |

These jobs reuse the same image, environment, and `spotinotifs_data` volume as the web service.

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
```

Useful legacy systemd commands are still available while the old deployment exists:

```bash
make status
make logs
make restart
```
