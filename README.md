# SpotiNotifs

Create a Spotify developer application and configure its redirect URI for the
host serving this application, ending in `/callback`.

The Flask server and scheduled notifier share one container image. Systemd owns
the Compose lifecycle and replaces the previous user service and crontab jobs.

```bash
make setup
make install
```

The notifier runs daily at `00:03` and `23:30`.

Useful commands:

```bash
make status
make logs
make restart
```

The ignored `.env` and `data/users.db` files remain on the host and are mounted
into the containers at runtime. Existing root-level `users.db` files are backed
up and migrated into `data/` by `make setup`.
