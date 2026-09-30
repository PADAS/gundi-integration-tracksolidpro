"""TrackSolidPro actions: auth, pull_devices, pull_observations, pull_track_history, pull_device_track_history."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from app.actions.configurations import (
    PullDeviceTrackHistoryConfig,
    PullDevicesConfig,
    PullObservationsConfig,
    PullTrackHistoryConfig,
    TrackSolidProAuthConfig,
    get_auth_config,
)
from app.actions.tracksolidpro_client import (
    clear_token_cache,
    get_cached_token,
    get_device_tracks,
    get_locations_by_account,
    get_token,
    list_devices,
    location_to_observation,
)
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.action_scheduler import crontab_schedule, trigger_actions
from app.services.gundi import send_observations_to_gundi
from app.services.utils import generate_batches
from gundi_core.events import LogLevel

logger = logging.getLogger(__name__)

OBSERVATION_BATCH_SIZE = 100
TRACK_HISTORY_BATCH_SIZE = 200


async def _with_token_retry(integration_id, auth_config, api_call):
    """Get a cached token, call api_call(token), and retry once on 401."""
    for attempt in range(2):
        token = await get_cached_token(
            integration_id=integration_id,
            user_id=auth_config.user_id,
            password=auth_config.password.get_secret_value(),
            app_key=auth_config.app_key,
            app_secret=auth_config.app_secret.get_secret_value(),
            base_url=auth_config.base_url,
            expires_in=auth_config.expires_in,
        )
        try:
            return token, await api_call(token)
        except (httpx.HTTPStatusError, RuntimeError) as e:
            is_token_error = (
                isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 401
            ) or (
                isinstance(e, RuntimeError) and ("token" in str(e).lower() or "401" in str(e))
            )
            if not is_token_error or attempt == 1:
                raise
            await clear_token_cache(integration_id)


async def action_auth(integration, action_config: TrackSolidProAuthConfig):
    """Verify TrackSolidPro credentials by obtaining an access token."""
    try:
        result = await get_token(
            user_id=action_config.user_id,
            password=action_config.password.get_secret_value(),
            app_key=action_config.app_key,
            app_secret=action_config.app_secret.get_secret_value(),
            base_url=action_config.base_url,
            expires_in=action_config.expires_in,
        )
        return {
            "valid_credentials": True,
            "expires_in": result.get("expires_in"),
        }
    except Exception as e:
        logger.exception("TrackSolidPro auth failed")
        return {"valid_credentials": False, "message": str(e)}


async def action_pull_devices(integration, action_config: PullDevicesConfig):
    """List all devices for the configured account."""
    auth_config = get_auth_config(integration)
    target = (action_config.target or "").strip() or auth_config.user_id

    token = await get_cached_token(
        integration_id=str(integration.id),
        user_id=auth_config.user_id,
        password=auth_config.password.get_secret_value(),
        app_key=auth_config.app_key,
        app_secret=auth_config.app_secret.get_secret_value(),
        base_url=auth_config.base_url,
        expires_in=auth_config.expires_in,
    )

    devices = await list_devices(
        access_token=token,
        target=target,
        app_key=auth_config.app_key,
        app_secret=auth_config.app_secret.get_secret_value(),
        base_url=auth_config.base_url,
    )

    return {
        "devices_count": len(devices),
        "devices": [
            {"imei": d.get("imei"), "deviceName": d.get("deviceName"), "enabledFlag": d.get("enabledFlag")}
            for d in devices
        ],
    }


@crontab_schedule("*/2 * * * *")
@activity_logger()
async def action_pull_observations(integration, action_config: PullObservationsConfig):
    """Pull latest GPS locations for all devices and send as observations to Gundi."""
    integration_id = str(integration.id)
    action_id = "pull_observations"

    auth_config = get_auth_config(integration)

    target = auth_config.user_id
    _, locations = await _with_token_retry(
        integration_id,
        auth_config,
        lambda tok: get_locations_by_account(
            access_token=tok,
            target=target,
            app_key=auth_config.app_key,
            app_secret=auth_config.app_secret.get_secret_value(),
            base_url=auth_config.base_url,
        ),
    )

    await log_action_activity(
        integration_id=integration_id,
        action_id=action_id,
        title="Fetched locations from TrackSolidPro",
        level=LogLevel.INFO,
        data={"locations_count": len(locations)},
    )

    observations: List[dict] = []
    subject_type = (action_config.subject_type or "vehicle").strip() or "vehicle"

    for loc in locations:
        imei = loc.get("imei") or loc.get("deviceName")
        device_name = loc.get("deviceName") or str(imei) or "unknown"
        if loc.get("lat") is None or loc.get("lng") is None:
            logger.debug("Skipping location missing lat/lng for imei=%s", imei)
            continue
        try:
            obs = location_to_observation(loc, device_name=device_name, subject_type=subject_type)
            observations.append(obs)
        except (ValueError, TypeError) as e:
            logger.debug("Skipping invalid location for imei=%s: %s", imei, e)
            continue

    sent_total = 0
    for batch in generate_batches(observations, OBSERVATION_BATCH_SIZE):
        await send_observations_to_gundi(
            observations=list(batch),
            integration_id=integration.id,
        )
        sent_total += len(batch)

    await log_action_activity(
        integration_id=integration_id,
        action_id=action_id,
        title="Sent observations to Gundi",
        level=LogLevel.INFO,
        data={"observations_sent": sent_total},
    )

    return {
        "locations_fetched": len(locations),
        "observations_sent": sent_total,
    }


@crontab_schedule("0 0 * * *")
@activity_logger()
async def action_pull_track_history(integration, action_config: PullTrackHistoryConfig):
    """Fan out one pull_device_track_history run per device.

    Lists the account's devices and publishes one RunIntegrationAction command
    per IMEI, all in one batch. The fetch-and-send work happens in the
    per-device runs so each stays well inside the runner's execution cap;
    doing it serially here for a 24 h window across every device overran it.

    Note: pull_track_history uses a different JIMI API endpoint (jimi.device.track.list) than pull_observations
    (jimi.device.location.list), so their data sets are not necessarily identical even for the same time window.
    The risk of processing the same observation twice has been reviewed and accepted — any actual duplicates
    would be deduplicated by Gundi and ER.
    """
    integration_id = str(integration.id)
    auth_config = get_auth_config(integration)
    subject_type = (action_config.subject_type or "truck").strip() or "truck"

    now = datetime.now(timezone.utc)
    # JIMI API expects UTC timestamps in "%Y-%m-%d %H:%M:%S" format
    begin_time = (now - timedelta(minutes=action_config.lookback_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    end_time = now.strftime("%Y-%m-%d %H:%M:%S")

    _, devices = await _with_token_retry(
        integration_id,
        auth_config,
        lambda tok: list_devices(
            access_token=tok,
            target=auth_config.user_id,
            app_key=auth_config.app_key,
            app_secret=auth_config.app_secret.get_secret_value(),
            base_url=auth_config.base_url,
        ),
    )

    device_configs = []
    for device in devices:
        imei = device.get("imei")
        if not imei:
            logger.debug("Skipping device without imei: %s", device)
            continue
        device_configs.append(
            PullDeviceTrackHistoryConfig(
                imei=str(imei),
                device_name=device.get("deviceName") or str(imei),
                subject_type=subject_type,
                begin_time=begin_time,
                end_time=end_time,
            )
        )

    await trigger_actions(
        integration_id=integration_id,
        action_id="pull_device_track_history",
        configs=device_configs,
    )

    return {
        "devices_queried": len(devices),
        "devices_triggered": len(device_configs),
    }


@activity_logger()
async def action_pull_device_track_history(integration, action_config: PullDeviceTrackHistoryConfig):
    """Pull GPS track history for one device (jimi.device.track.list) and send it to Gundi.

    Triggered by action_pull_track_history, one command per device. Errors
    other than an expired token propagate: the runner logs the failure for
    this device alone and the other devices' runs are unaffected.
    """
    integration_id = str(integration.id)
    auth_config = get_auth_config(integration)
    imei = action_config.imei
    device_name = action_config.device_name or imei
    subject_type = (action_config.subject_type or "truck").strip() or "truck"

    _, tracks = await _with_token_retry(
        integration_id,
        auth_config,
        lambda tok: get_device_tracks(
            access_token=tok,
            imei=imei,
            begin_time=action_config.begin_time,
            end_time=action_config.end_time,
            app_key=auth_config.app_key,
            app_secret=auth_config.app_secret.get_secret_value(),
            base_url=auth_config.base_url,
        ),
    )

    observations = []
    for point in tracks:
        if point.get("lat") is None or point.get("lng") is None:
            logger.debug("Skipping track point missing lat/lng for imei=%s", imei)
            continue
        try:
            obs = location_to_observation(
                {**point, "imei": point.get("imei") or imei, "deviceName": point.get("deviceName") or device_name},
                device_name=device_name,
                subject_type=subject_type,
            )
            observations.append(obs)
        except (ValueError, TypeError) as e:
            logger.debug("Skipping invalid track point for imei=%s: %s", imei, e)

    sent_total = 0
    for batch in generate_batches(observations, TRACK_HISTORY_BATCH_SIZE):
        await send_observations_to_gundi(
            observations=list(batch),
            integration_id=integration.id,
        )
        sent_total += len(batch)

    return {
        "imei": imei,
        "track_points_processed": len(observations),
        "observations_sent": sent_total,
    }
