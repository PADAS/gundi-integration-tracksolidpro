"""Tests for TrackSolidPro action handlers."""

import httpx
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.actions.configurations import (
    TrackSolidProAuthConfig,
    PullObservationsConfig,
    PullDeviceTrackHistoryConfig,
    PullTrackHistoryConfig,
    get_auth_config,
)
from app.actions.handlers import (
    TRACK_HISTORY_BATCH_SIZE,
    action_auth,
    action_pull_device_track_history,
    action_pull_observations,
    action_pull_track_history,
)
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
def _http_401():
    response_401 = MagicMock(spec=httpx.Response)
    response_401.status_code = 401
    return httpx.HTTPStatusError("401", request=MagicMock(), response=response_401)


# --- pull_track_history: the daily fan-out ---------------------------------


@pytest.mark.asyncio
async def test_action_pull_track_history_fans_out_one_action_per_device(mocker, integration_with_auth):
    """The daily action lists devices and triggers one pull_device_track_history per IMEI.

    It must not fetch tracks or send observations itself: that work is what
    used to overrun the runner's execution cap when done serially.
    """
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="access-tok"))
    mocker.patch(
        "app.actions.handlers.list_devices",
        AsyncMock(return_value=[
            {"imei": "imei1", "deviceName": "D1"},
            {"imei": "imei2", "deviceName": "D2"},
            {"deviceName": "no-imei"},
        ]),
    )
    mock_get_tracks = mocker.patch("app.actions.handlers.get_device_tracks", AsyncMock())
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock())
    mock_trigger = mocker.patch("app.actions.handlers.trigger_actions", AsyncMock(return_value={"messageIds": ["1", "2"]}))
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullTrackHistoryConfig(subject_type="vehicle", lookback_minutes=45)
    result = await action_pull_track_history(integration_with_auth, config)

    assert result == {"devices_queried": 3, "devices_triggered": 2}
    assert not mock_get_tracks.called
    assert not mock_send.called

    mock_trigger.assert_called_once()
    assert mock_trigger.call_args.kwargs["integration_id"] == str(integration_with_auth.id)
    assert mock_trigger.call_args.kwargs["action_id"] == "pull_device_track_history"
    configs = mock_trigger.call_args.kwargs["configs"]
    assert [c.imei for c in configs] == ["imei1", "imei2"]
    assert [c.device_name for c in configs] == ["D1", "D2"]
    assert all(isinstance(c, PullDeviceTrackHistoryConfig) for c in configs)
    assert all(c.subject_type == "vehicle" for c in configs)
    # One window computed once by the parent and shared by every child, so
    # children delayed in the queue do not drift to different ranges.
    assert len({(c.begin_time, c.end_time) for c in configs}) == 1
    begin = datetime.strptime(configs[0].begin_time, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(configs[0].end_time, "%Y-%m-%d %H:%M:%S")
    assert abs((end - begin).total_seconds() - 45 * 60) < 5


@pytest.mark.asyncio
async def test_action_pull_track_history_retries_list_devices_on_401(mocker, integration_with_auth):
    """A 401 from list_devices clears the token cache and retries once."""
    mock_get_token = mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="access-tok"))
    mock_clear_cache = mocker.patch("app.actions.handlers.clear_token_cache", AsyncMock())
    mock_list_devices = mocker.patch(
        "app.actions.handlers.list_devices",
        AsyncMock(side_effect=[_http_401(), [{"imei": "imei1", "deviceName": "D1"}]]),
    )
    mock_trigger = mocker.patch("app.actions.handlers.trigger_actions", AsyncMock())
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    config = PullTrackHistoryConfig(subject_type="vehicle", lookback_minutes=45)
    result = await action_pull_track_history(integration_with_auth, config)

    assert mock_get_token.call_count == 2
    assert mock_clear_cache.call_count == 1
    assert mock_list_devices.call_count == 2
    assert mock_trigger.call_count == 1
    assert result["devices_triggered"] == 1


def test_pull_device_track_history_is_an_internal_action():
    """The per-device action is discovered by the runner but not registered in Gundi."""
    from app.actions import action_handlers
    from app.actions.core import InternalActionConfiguration

    handler, config_model, _ = action_handlers["pull_device_track_history"]
    assert handler is action_pull_device_track_history
    assert issubclass(config_model, InternalActionConfiguration)


# --- pull_device_track_history: one device per run --------------------------


def _device_config(**overrides):
    values = dict(
        imei="imei1",
        device_name="D1",
        subject_type="vehicle",
        begin_time="2024-01-15 09:15:00",
        end_time="2024-01-15 10:00:00",
    )
    values.update(overrides)
    return PullDeviceTrackHistoryConfig(**values)


@pytest.mark.asyncio
async def test_action_pull_device_track_history_sends_to_gundi_in_batches(mocker, integration_with_auth):
    """Fetches the device's tracks for the given window and sends them in batches of TRACK_HISTORY_BATCH_SIZE."""
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="access-tok"))
    points = [
        {
            "lat": 22.5,
            "lng": 113.9 + i * 0.001,
            "gpsTime": "2024-01-15 10:00:00",
            "gpsSpeed": "60",
            "direction": "90",
            "posType": "1",
            "ignition": "ON",
            "accStatus": "ON",
        }
        for i in range(TRACK_HISTORY_BATCH_SIZE + 50)
    ]
    mock_get_tracks = mocker.patch("app.actions.handlers.get_device_tracks", AsyncMock(return_value=points))
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock(return_value=[]))
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    result = await action_pull_device_track_history(integration_with_auth, _device_config())

    assert result == {
        "imei": "imei1",
        "track_points_processed": TRACK_HISTORY_BATCH_SIZE + 50,
        "observations_sent": TRACK_HISTORY_BATCH_SIZE + 50,
    }
    mock_get_tracks.assert_called_once()
    assert mock_get_tracks.call_args.kwargs["imei"] == "imei1"
    assert mock_get_tracks.call_args.kwargs["begin_time"] == "2024-01-15 09:15:00"
    assert mock_get_tracks.call_args.kwargs["end_time"] == "2024-01-15 10:00:00"

    assert mock_send.call_count == 2
    sizes = [len(call.kwargs["observations"]) for call in mock_send.call_args_list]
    assert sizes == [TRACK_HISTORY_BATCH_SIZE, 50]
    assert all(call.kwargs["integration_id"] == integration_with_auth.id for call in mock_send.call_args_list)
    first = mock_send.call_args_list[0].kwargs["observations"][0]
    assert first["source"] == "imei1"
    assert first["subject_type"] == "vehicle"
    assert first["additional"]["gps_speed_kmph"] == 60.0
    assert first["additional"]["ignition"] == "ON"


@pytest.mark.asyncio
async def test_action_pull_device_track_history_skips_points_without_coords(mocker, integration_with_auth):
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="tok"))
    mocker.patch(
        "app.actions.handlers.get_device_tracks",
        AsyncMock(return_value=[
            {"lat": None, "lng": 113.9, "gpsTime": "2024-01-15 10:00:00"},
            {"lat": 22.5, "lng": 114.0, "gpsTime": "2024-01-15 10:05:00"},
        ]),
    )
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock(return_value=[]))
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    result = await action_pull_device_track_history(integration_with_auth, _device_config())

    assert result["track_points_processed"] == 1
    assert result["observations_sent"] == 1
    assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_action_pull_device_track_history_with_no_points_sends_nothing(mocker, integration_with_auth):
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="tok"))
    mocker.patch("app.actions.handlers.get_device_tracks", AsyncMock(return_value=[]))
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock())
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    result = await action_pull_device_track_history(integration_with_auth, _device_config())

    assert result["observations_sent"] == 0
    assert not mock_send.called


@pytest.mark.asyncio
async def test_action_pull_device_track_history_retries_get_device_tracks_on_401(mocker, integration_with_auth):
    """A 401 from get_device_tracks refreshes the token and retries; observations are sent exactly once."""
    track_point = {"lat": 1.0, "lng": 2.0, "gpsTime": "2024-01-15 10:00:00"}
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="tok"))
    mock_clear_cache = mocker.patch("app.actions.handlers.clear_token_cache", AsyncMock())
    mock_get_tracks = mocker.patch(
        "app.actions.handlers.get_device_tracks",
        AsyncMock(side_effect=[_http_401(), [track_point]]),
    )
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock(return_value=[]))
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    result = await action_pull_device_track_history(integration_with_auth, _device_config())

    assert mock_get_tracks.call_count == 2
    assert mock_clear_cache.call_count == 1
    assert mock_send.call_count == 1
    assert result["observations_sent"] == 1


@pytest.mark.asyncio
async def test_action_pull_device_track_history_propagates_fetch_errors(mocker, integration_with_auth):
    """A non-auth failure fails this device's run (the runner logs it); it is not swallowed."""
    mocker.patch("app.actions.handlers.get_cached_token", AsyncMock(return_value="tok"))
    mocker.patch("app.actions.handlers.get_device_tracks", AsyncMock(side_effect=RuntimeError("JIMI code=9999")))
    mock_send = mocker.patch("app.actions.handlers.send_observations_to_gundi", AsyncMock())
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock())

    with pytest.raises(RuntimeError, match="9999"):
        await action_pull_device_track_history(integration_with_auth, _device_config())
    assert not mock_send.called
