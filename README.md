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

### `pull_devices`

Lists all devices registered to the account. Useful for verifying connectivity and discovering device IMEIs.

Configuration:

| Field | Default | Description |
|---|---|---|
| `target` | *(auth user_id)* | Account to query; leave empty to use the auth `user_id` |

## Token caching

Access tokens are cached in Redis via `IntegrationStateManager`. The cache TTL is set to `expires_in - 60` seconds so the token is refreshed proactively before it expires. On authentication failure the cache is cleared and a fresh token is obtained on the next run.

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
