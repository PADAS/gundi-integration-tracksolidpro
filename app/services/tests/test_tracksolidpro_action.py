"""Tests for TrackSolidPro action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.actions.configurations import (
    TrackSolidProAuthConfig,
    PullObservationsConfig,
    get_auth_config,
)
from app.actions.handlers import action_auth, action_pull_observations
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
