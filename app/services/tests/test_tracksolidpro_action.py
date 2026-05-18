"""Tests for TrackSolidPro action handlers."""

import httpx
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.actions.configurations import (
    TrackSolidProAuthConfig,
    PullObservationsConfig,
    PullTrackHistoryConfig,
    get_auth_config,
)
from app.actions.handlers import action_auth, action_pull_observations, action_pull_track_history
from app.services.errors import ConfigurationNotFound


@pytest.fixture
def integration_with_auth():
    """Integration with auth and pull_observations config."""
    integration = MagicMock()
    integration.id = "779ff3ab-5589-4f4c-9e0a-ae8d6c9edff0"
    auth_config_data = {
        "user_id": "testuser",
        "password": "secret",
        "app_key": "appkey",
        "app_secret": "appsecret",
        "base_url": "https://api.jimicloud.com",
        "expires_in": 7200,
    }
    pull_config_data = {"subject_type": "vehicle"}
    auth_action = MagicMock()
    auth_action.value = "auth"
    pull_action = MagicMock()
    pull_action.value = "pull_observations"
    integration.configurations = [
        MagicMock(action=auth_action, data=auth_config_data),
        MagicMock(action=pull_action, data=pull_config_data),
    ]
    return integration


@pytest.mark.asyncio
async def test_action_auth_success(mocker, integration_with_auth):
    """action_auth returns valid_credentials True when get_token succeeds."""
    mocker.patch(
        "app.actions.handlers.get_token",
        AsyncMock(
            return_value={"access_token": "tok", "refresh_token": "ref", "expires_in": 7200}
        ),
    )
    config = TrackSolidProAuthConfig(
        user_id="u",
        password="p",
        app_key="k",
        app_secret="s",
        base_url="https://api.example.com",
    )
    result = await action_auth(integration_with_auth, config)
    assert result["valid_credentials"] is True
    assert result["expires_in"] == 7200


@pytest.mark.asyncio
async def test_action_auth_failure(mocker, integration_with_auth):
    """action_auth returns valid_credentials False when get_token raises."""
    mocker.patch(
        "app.actions.handlers.get_token",
        AsyncMock(side_effect=RuntimeError("Invalid credentials")),
    )
    config = TrackSolidProAuthConfig(
        user_id="u",
        password="p",
        app_key="k",
        app_secret="s",
        base_url="https://api.example.com",
    )
    result = await action_auth(integration_with_auth, config)
    assert result["valid_credentials"] is False
    assert "message" in result


def test_get_auth_config_raises_when_missing():
    """get_auth_config raises ConfigurationNotFound when auth config is missing."""
    integration = MagicMock()
    integration.id = "some-id"
    integration.configurations = []  # no auth
    with pytest.raises(ConfigurationNotFound):
        get_auth_config(integration)


def test_get_auth_config_returns_parsed_config(integration_with_auth):
    """get_auth_config returns TrackSolidProAuthConfig from integration."""
    config = get_auth_config(integration_with_auth)
    assert isinstance(config, TrackSolidProAuthConfig)
    assert config.user_id == "testuser"
    assert config.base_url == "https://api.jimicloud.com"


@pytest.mark.asyncio
async def test_action_pull_observations_sends_to_gundi(mocker, integration_with_auth):
    """action_pull_observations fetches locations, maps to observations, sends batches to Gundi."""
    mocker.patch(
        "app.actions.handlers.get_cached_token",
        AsyncMock(return_value="access-tok"),
    )
    mocker.patch(
        "app.actions.handlers.get_locations_by_account",
        AsyncMock(
            return_value=[
                {
                    "imei": "imei1",
                    "deviceName": "D1",
                    "lat": 22.5,
                    "lng": 113.9,
                    "gpsTime": "2024-01-15 10:00:00",
                },
            ]
        ),
    )
    mock_send = AsyncMock(return_value=[])
    mocker.patch("app.actions.handlers.send_observations_to_gundi", mock_send)
    mocker.patch("app.actions.handlers.log_action_activity", AsyncMock())
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullObservationsConfig(subject_type="vehicle")
    result = await action_pull_observations(integration_with_auth, config)

    assert result["locations_fetched"] == 1
    assert result["observations_sent"] == 1
    assert mock_send.call_count == 1
    call_args = mock_send.call_args
    assert call_args.kwargs["integration_id"] == integration_with_auth.id
    observations = call_args.kwargs["observations"]
    assert len(observations) == 1
    assert observations[0]["source"] == "imei1"
    assert observations[0]["type"] == "tracking-device"
    assert observations[0]["location"] == {"lat": 22.5, "lon": 113.9}


@pytest.mark.asyncio
async def test_action_pull_track_history_sends_to_gundi(mocker, integration_with_auth):
    """action_pull_track_history lists devices, fetches tracks per IMEI, sends all points to Gundi."""
    mocker.patch(
        "app.actions.handlers.get_cached_token",
        AsyncMock(return_value="access-tok"),
    )
    mocker.patch(
        "app.actions.handlers.list_devices",
        AsyncMock(
            return_value=[
                {"imei": "imei1", "deviceName": "D1"},
                {"imei": "imei2", "deviceName": "D2"},
            ]
        ),
    )
    mock_get_tracks = mocker.patch(
        "app.actions.handlers.get_device_tracks",
        AsyncMock(
            return_value=[
                {
                    "lat": 22.5,
                    "lng": 113.9,
                    "gpsTime": "2024-01-15 10:00:00",
                    "gpsSpeed": "60",
                    "direction": "90",
                    "posType": "1",
                    "ignition": "ON",
                    "accStatus": "ON",
                },
                {
                    "lat": 22.6,
                    "lng": 114.0,
                    "gpsTime": "2024-01-15 10:05:00",
                    "gpsSpeed": "0",
                    "direction": "0",
                    "posType": "1",
                    "ignition": "OFF",
                    "accStatus": "OFF",
                },
            ]
        ),
    )
    mock_send = AsyncMock(return_value=[])
    mocker.patch("app.actions.handlers.send_observations_to_gundi", mock_send)
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullTrackHistoryConfig(subject_type="vehicle", lookback_minutes=45)
    result = await action_pull_track_history(integration_with_auth, config)

    # 2 devices × 2 points each = 4 observations, sent per-device (2 send calls)
    assert result["devices_queried"] == 2
    assert result["observations_sent"] == 4
    assert mock_send.call_count == 2
    all_observations = [obs for call in mock_send.call_args_list for obs in call.kwargs["observations"]]
    assert len(all_observations) == 4
    sources = {obs["source"] for obs in all_observations}
    assert sources == {"imei1", "imei2"}
    # Verify gpsSpeed is mapped to gps_speed_kmph
    imei1_obs = [o for o in all_observations if o["source"] == "imei1"]
    assert imei1_obs[0]["additional"]["gps_speed_kmph"] == 60.0
    assert imei1_obs[0]["additional"]["ignition"] == "ON"
    # Verify lookback_minutes is respected: window between begin and end should be ~45 min
    assert mock_get_tracks.call_count == 2
    begin = datetime.strptime(mock_get_tracks.call_args_list[0].kwargs["begin_time"], "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(mock_get_tracks.call_args_list[0].kwargs["end_time"], "%Y-%m-%d %H:%M:%S")
    assert abs((end - begin).total_seconds() - 45 * 60) < 5


@pytest.mark.asyncio
async def test_action_pull_track_history_skips_points_without_coords(mocker, integration_with_auth):
    """action_pull_track_history skips track points that are missing lat or lng."""
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="tok"))
    mocker.patch(
        "app.actions.handlers.list_devices",
        AsyncMock(return_value=[{"imei": "imei1", "deviceName": "D1"}]),
    )
    mocker.patch(
        "app.actions.handlers.get_device_tracks",
        AsyncMock(
            return_value=[
                {"lat": None, "lng": 113.9, "gpsTime": "2024-01-15 10:00:00"},
                {"lat": 22.5, "lng": 114.0, "gpsTime": "2024-01-15 10:05:00"},
            ]
        ),
    )
    mock_send = AsyncMock(return_value=[])
    mocker.patch("app.actions.handlers.send_observations_to_gundi", mock_send)
    mocker.patch("app.actions.handlers.log_action_activity", AsyncMock())
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullTrackHistoryConfig(subject_type="vehicle", lookback_minutes=45)
    result = await action_pull_track_history(integration_with_auth, config)

    assert result["observations_sent"] == 1


@pytest.mark.asyncio
async def test_action_pull_track_history_retries_list_devices_on_401(mocker, integration_with_auth):
    """A 401 from list_devices clears the token cache and retries; already-sent observations are NOT re-sent."""
    response_401 = MagicMock(spec=httpx.Response)
    response_401.status_code = 401
    error_401 = httpx.HTTPStatusError("401", request=MagicMock(), response=response_401)

    mock_get_token = mocker.patch(
        "app.actions.handlers.get_cached_token",
        AsyncMock(return_value="access-tok"),
    )
    mock_clear_cache = mocker.patch("app.actions.handlers.clear_token_cache", AsyncMock())
    mock_list_devices = mocker.patch(
        "app.actions.handlers.list_devices",
        AsyncMock(side_effect=[error_401, [{"imei": "imei1", "deviceName": "D1"}]]),
    )
    mocker.patch(
        "app.actions.handlers.get_device_tracks",
        AsyncMock(return_value=[{"lat": 1.0, "lng": 2.0, "gpsTime": "2024-01-15 10:00:00"}]),
    )
    mock_send = AsyncMock(return_value=[])
    mocker.patch("app.actions.handlers.send_observations_to_gundi", mock_send)
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullTrackHistoryConfig(subject_type="vehicle", lookback_minutes=45)
    result = await action_pull_track_history(integration_with_auth, config)

    # Token was refreshed and list_devices retried once
    assert mock_get_token.call_count == 2
    assert mock_clear_cache.call_count == 1
    assert mock_list_devices.call_count == 2

    # Observations sent exactly once — no duplicate sends from the retry
    assert mock_send.call_count == 1
    assert result["observations_sent"] == 1
