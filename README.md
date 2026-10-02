# gundi-integration-tracksolidpro

Gundi v2 integration for [TrackSolidPro](https://www.tracksolidpro.com/) (JIMI IoT) GPS tracking devices.

Pulls latest device locations from the JIMI REST API and forwards them to Gundi as observations.

## Authentication

Configure the **Auth** action in the Gundi portal with the following fields:

| Field | Description |
|---|---|
| `user_id` | TrackSolidPro account username |
| `password` | Account password (MD5-hashed before sending to the API) |
| `app_key` | JIMI application key |
| `app_secret` | JIMI application secret (used for request signing) |
| `base_url` | Regional API endpoint (see below) |
| `expires_in` | Token lifetime in seconds (60–7200, default 7200) |

### Regional base URLs

TrackSolidPro operates regional endpoints. Use the one that matches your account:

| Region | URL |
|---|---|
| HK / APAC | `https://hk-open.tracksolidpro.com/route/rest` |
| EU | `https://eu-open.tracksolidpro.com/route/rest` |
| US | `https://us-open.tracksolidpro.com/route/rest` |
| CN | `https://open.tracksolidpro.com/route/rest` |
| 10000track | `https://open.10000track.com/route/rest` |

If you are unsure which region to use, run the probe script to test all of them:

```bash
export TRACKSOLIDPRO_USER_ID="your_user_id"
export TRACKSOLIDPRO_PASSWORD="your_password"
export TRACKSOLIDPRO_APP_KEY="your_app_key"
export TRACKSOLIDPRO_APP_SECRET="your_app_secret"
python scripts/probe_tracksolidpro_regions.py
```

The script prints a result table and identifies which region returns a valid token.

## Actions

### `pull_observations` (runs every 2 minutes)

Fetches the latest GPS location for all devices under the account and sends them to Gundi as observations.

Configuration:

| Field | Default | Description |
|---|---|---|
| `subject_type` | `truck` | Subject type applied to all observations (e.g. `truck`, `vehicle`) |

### `pull_track_history` (runs daily at 00:00 UTC)

Fans out one `pull_device_track_history` run per device. It lists the account's devices, computes a single time window (`now - lookback_minutes` to `now`), and publishes one `RunIntegrationAction` command per IMEI in one batch. It does not fetch or send track points itself: doing that serially across every device for a 24 h window exceeded the runner's 9-minute execution cap (`MAX_ACTION_EXECUTION_TIME`).

Configuration:

| Field | Default | Description |
|---|---|---|
| `subject_type` | `truck` | Subject type applied to all observations |
| `lookback_minutes` | `1440` | How far back to query track history on each run (10 to 10080) |

Requires `INTEGRATION_COMMANDS_TOPIC` (derived from `INTEGRATION_TYPE_SLUG` by default) so the per-device commands can be published. Set `TRIGGER_ACTIONS_ALWAYS_SYNC=true` locally to run the per-device actions inline instead.

A per-device run that fails for a transient reason (provider unreachable or 5xx, rate limited, Gundi 5xx, the runner's execution cap) answers PubSub with a non-2xx so the command is redelivered with backoff; failures a retry cannot fix (bad configuration, rejected credentials, provider 4xx) are acked. This needs `PROCESS_PUBSUB_MESSAGES_IN_BACKGROUND` left off, since background mode acks on receipt.

### `pull_device_track_history` (internal)

Pulls GPS track history for one device via `jimi.device.track.list` for the window it was given and sends the points to Gundi in batches of 200. Triggered only by `pull_track_history`; not shown in the portal and not registered in Gundi. A failure here affects that device's run only.

### `pull_devices`

Lists all devices registered to the account. Useful for verifying connectivity and discovering device IMEIs.

Configuration:

| Field | Default | Description |
|---|---|---|
| `target` | *(auth user_id)* | Account to query; leave empty to use the auth `user_id` |

## Token caching

Access tokens are cached in Redis via `IntegrationStateManager`. The cache TTL is set to `expires_in - 60` seconds so the token is refreshed proactively before it expires. On authentication failure the cache is cleared and a fresh token is obtained on the next run.

## Activity-log redaction

The configuration attached to every activity-log event is redacted before publishing (`app/services/redaction.py`): values under secret-looking keys (`password`, `token`, `api_key`, `secret`, ...) and fields a config model declares as `SecretStr`, `Field(format="password")` or `UIOptions(widget="password")` are replaced with `**********`, matched by field name or alias and at any depth of nested models. Declare secrets that way and they never reach the portal's activity log in clear, whatever their name.

## Development utilities

**Single-URL token test** — mirrors exactly what the integration sends:
```bash
export TRACKSOLIDPRO_BASE_URL="https://eu-open.tracksolidpro.com/route/rest"
export TRACKSOLIDPRO_USER_ID="..."
export TRACKSOLIDPRO_PASSWORD="..."
export TRACKSOLIDPRO_APP_KEY="..."
export TRACKSOLIDPRO_APP_SECRET="..."
python scripts/request_tracksolidpro_token.py
```

**Region probe** — tests all five regional endpoints:
```bash
python scripts/probe_tracksolidpro_regions.py
```
