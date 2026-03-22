"""Tests for TrackSolidPro (JIMI) API client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.tracksolidpro_client import (
    _build_sign,
    _ensure_request_url,
    _normalize_params,
    location_to_observation,
    get_token,
    get_cached_token,
    list_devices,
    get_locations_by_account,
)


def test_build_sign():
    """Sign is MD5(app_secret + sorted key-value concat + app_secret), uppercase."""
    params = {"app_key": "K", "method": "m", "v": "1.0"}
    secret = "secret"
    sign = _build_sign(params, secret)
    assert len(sign) == 32
    assert sign.isupper()
    assert sign.isalnum()
    # Deterministic
    assert _build_sign(params, secret) == sign


def test_build_sign_excludes_sign_key():
    """Parameter 'sign' is excluded from the signed string."""
    params1 = {"a": "1", "sign": "x"}
    params2 = {"a": "1"}
    assert _build_sign(params1, "s") == _build_sign(params2, "s")


def test_normalize_params():
    """All values become strings; None entries are dropped."""
    assert _normalize_params({"a": 1, "b": "x", "c": None}) == {"a": "1", "b": "x"}
    assert _normalize_params({"expires_in": 7200}) == {"expires_in": "7200"}


def test_ensure_request_url():
    """Adds /route/rest when base URL has no path; leaves path when present."""
    assert _ensure_request_url("https://hk-open.tracksolidpro.com") == "https://hk-open.tracksolidpro.com/route/rest"
    assert _ensure_request_url("https://hk-open.tracksolidpro.com/") == "https://hk-open.tracksolidpro.com/route/rest"
    assert _ensure_request_url("https://hk-open.tracksolidpro.com/route/rest") == "https://hk-open.tracksolidpro.com/route/rest"
    assert _ensure_request_url("hk-open.tracksolidpro.com") == "https://hk-open.tracksolidpro.com/route/rest"


def test_location_to_observation():
    """Maps JIMI location dict to Gundi observation dict."""
    loc = {
        "imei": "868120145233604",
        "deviceName": "Device-1",
        "lat": 22.577282,
        "lng": 113.916604,
        "gpsTime": "2017-04-26 09:17:46",
        "speed": "10",
        "accStatus": "0",
        "posType": "GPS",
    }
    obs = location_to_observation(loc, device_name="Device-1", subject_type="vehicle")
    assert obs["source"] == "868120145233604"
    assert obs["source_name"] == "Device-1"
    assert obs["type"] == "tracking-device"
    assert obs["subject_type"] == "vehicle"
    assert obs["location"] == {"lat": 22.577282, "lon": 113.916604}
    assert "recorded_at" in obs
    assert obs["additional"]["speed_kmph"] == 10.0
    assert obs["additional"]["acc_status"] == "0"
    assert obs["additional"]["pos_type"] == "GPS"


def test_location_to_observation_missing_lat_lng_raises():
    """Raises ValueError when lat or lng is missing."""
    with pytest.raises(ValueError, match="Missing lat/lng"):
        location_to_observation({"imei": "1", "deviceName": "D"}, "D")


@pytest.mark.asyncio
async def test_get_token_success(mocker):
    """get_token returns access_token and expires_in from API result."""
    mock_data = {
        "code": 0,
        "result": {
            "accessToken": "tok-123",
            "refreshToken": "ref-456",
            "expiresIn": 7200,
        },
    }
    mocker.patch(
        "app.actions.tracksolidpro_client._request_async",
        AsyncMock(return_value=mock_data),
    )
    result = await get_token(
        user_id="u",
        password="p",
        app_key="k",
        app_secret="s",
        base_url="https://api.example.com",
    )
    assert result["access_token"] == "tok-123"
    assert result["refresh_token"] == "ref-456"
    assert result["expires_in"] == 7200


@pytest.mark.asyncio
async def test_get_token_api_error_raises(mocker):
    """get_token raises when API returns code != 0 (raised inside _request_async)."""
    mocker.patch(
        "app.actions.tracksolidpro_client._request_async",
        side_effect=RuntimeError("TrackSolidPro API error (code=1): Invalid credentials"),
    )
    with pytest.raises(RuntimeError, match="TrackSolidPro API error"):
        await get_token(
            user_id="u",
            password="p",
            app_key="k",
            app_secret="s",
            base_url="https://api.example.com",
        )


@pytest.mark.asyncio
async def test_get_cached_token_caches(mocker):
    """get_cached_token caches token and returns it on the second call without re-fetching."""
    mock_data = {
        "code": 0,
        "result": {
            "accessToken": "cached-tok",
            "refreshToken": "ref",
            "expiresIn": 7200,
        },
    }
    # First call: cache miss; second call: cache hit with the stored token
    mock_state = mocker.MagicMock()
    mock_state.get_state = AsyncMock(side_effect=[{}, {"access_token": "cached-tok"}])
    mock_state.set_state = AsyncMock(return_value=None)
    mock_state.delete_state = AsyncMock(return_value=None)
    mocker.patch("app.actions.tracksolidpro_client._state_manager", mock_state)

    mock_request = mocker.patch(
        "app.actions.tracksolidpro_client._request_async",
        AsyncMock(return_value=mock_data),
    )
    token1 = await get_cached_token(
        "int-1", "u", "p", "k", "s", "https://api.example.com", min_ttl_seconds=60
    )
    assert token1 == "cached-tok"
    token2 = await get_cached_token(
        "int-1", "u", "p", "k", "s", "https://api.example.com", min_ttl_seconds=60
    )
    assert token2 == "cached-tok"
    # _request_async should have been called only once (second call uses cache)
    assert mock_request.call_count == 1


@pytest.mark.asyncio
async def test_list_devices_returns_list(mocker):
    """list_devices returns list of device dicts."""
    mocker.patch(
        "app.actions.tracksolidpro_client._request_async",
        AsyncMock(
            return_value={
                "code": 0,
                "result": [
                    {"imei": "imei1", "deviceName": "D1"},
                    {"imei": "imei2", "deviceName": "D2"},
                ],
            }
        ),
    )
    devices = await list_devices(
        access_token="tok",
        target="account",
        app_key="k",
        app_secret="s",
        base_url="https://api.example.com",
    )
    assert len(devices) == 2
    assert devices[0]["imei"] == "imei1"


@pytest.mark.asyncio
async def test_get_locations_by_account_returns_list(mocker):
    """get_locations_by_account returns list of location dicts."""
    mocker.patch(
        "app.actions.tracksolidpro_client._request_async",
        AsyncMock(
            return_value={
                "code": 0,
                "result": [
                    {"imei": "i1", "lat": 1.0, "lng": 2.0, "gpsTime": "2024-01-01 12:00:00"},
                ],
            }
        ),
    )
    locations = await get_locations_by_account(
        access_token="tok",
        target="account",
        app_key="k",
        app_secret="s",
        base_url="https://api.example.com",
    )
    assert len(locations) == 1
    assert locations[0]["imei"] == "i1"
    assert locations[0]["lat"] == 1.0
