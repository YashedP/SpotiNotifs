# SpotiNotifs observability deployment runbook

## Merge and deploy

1. Stage the `home_infra` observability PR and confirm the collector is healthy.
2. Merge this PR into `main`, record the deployed commit and previous image, and set `SERVICE_VERSION` to that commit in Dokploy.
3. Preserve the current deployed branch unless its product changes have been merged or the observability commit has been integrated into it.
4. In Dokploy Advanced, copy the existing default command and add `-f compose.observability.yaml` after `-f compose.yaml`, preserving the existing project name and other flags.
5. Review Preview Compose and rebuild/redeploy, preserving all existing domains, volumes, credentials, and schedules.

The web service is `spotinotifs-server`; the scheduled job is `spotinotifs-notifier`.
Each notifier user operation receives a separate trace, with child Spotify, Discord-send, and Anchor operations.
Anchor notification requests propagate W3C trace context so an instrumented Anchor backend can join the same trace.
Traces contain operation names and status rather than request arguments, OAuth codes, API keys, message bodies, or exception text.
The JSON formatter attaches active trace/span IDs without an additional OTLP log exporter.

## Update the notifier schedule

Updating only the web service does not update commands in an existing Dokploy schedule or systemd unit.
Inspect the existing schedule and preserve its timing and Docker Compose project name.
Insert `-f compose.observability.yaml` after its existing `-f compose.yaml`, before `run --rm notifier`.
The resulting command shape is:

```sh
docker compose -p YOUR_EXISTING_PROJECT -f compose.yaml -f compose.observability.yaml run --rm notifier
```

Do not literally use the placeholder project name.
Set `SERVICE_VERSION` in the scheduler's environment or its existing `.env` file so Compose interpolation succeeds.
Keep the encrypted Anchor key, Spotify credentials, Discord credentials, timezone, job profile, and existing data-volume mount unchanged.
For the repository-managed systemd installation, update the unit's `ExecStart` to use the same overlay and set `Environment=SERVICE_VERSION=YOUR_DEPLOYED_COMMIT`.
Run `sudo systemctl daemon-reload` after editing the unit, and leave the timer's existing schedule intact.
Do not start an additional notifier or replay catch-up work merely to validate telemetry.

## Verify

1. Request the real web `/health` endpoint and confirm no health server span is generated.
2. Perform a read-only web request and find its completion log under `spotinotifs-server` in HyperDX.
3. Open the linked trace and verify resource version, status, and route template.
4. At the next normal scheduled notifier run, filter to `spotinotifs-notifier` and locate one user operation by its existing run ID.
5. Open its trace, inspect child operations, and follow a normal Anchor notification to Anchor's server span if notification delivery occurs.
6. Confirm unrelated concurrently processed users have separate traces and no secrets appear in attributes.

No production Spotify, Discord, or Anchor action is required by the automated tests.
Run `uv run python -m unittest discover -s tests` and `docker build -t spotinotifs:observability-check .` locally before deploying changes.

## Rollback

Set `OTEL_SDK_DISABLED=true` for both services and the scheduler, then redeploy the web service and update the job environment.
Existing JSON logs and run IDs remain available.
Restore the previous schedule command and image if rolling back fully; no observability-specific database migration is involved.
Follow the `home_infra/observability/README.md` for collector, UI, retention, backup, and legacy-backend rollback.
