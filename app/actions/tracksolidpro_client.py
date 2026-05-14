"""
TrackSolidPro (JIMI) API client.

- Authentication: user_id + MD5(password) + app_key/app_secret → access_token via jimi.oauth.token.get
- All requests use common params (app_key, method, timestamp, format, sign_method, v) and MD5 sign
- Token cached in Redis per integration_id via IntegrationStateManager (get_cached_token / clear_token_cache)
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app import settings
from app.services.state import IntegrationStateManager

logger = logging.getLogger(__name__)

_ACTION_ID_TOKEN = "auth_token"
_state_manager = IntegrationStateManager()


def _normalize_params(params: Dict[str, Any]) -> Dict[str, str]:
    """Convert all values to strings so sign and request body match server expectations."""
    return {k: str(v) for k, v in params.items() if v is not None}


def _build_sign(params: Dict[str, Any], app_secret: str) -> str:
    """
    Build JIMI API sign: MD5(app_secret + sorted key-value concat + app_secret), 32-char uppercase.
    Params are concatenated in alphabetical key order, key immediately followed by value, no '=' or ','.
    All values must be strings (use _normalize_params first) so sign matches server.
    """
    excluded = {"sign"}
    ordered = sorted((k, v) for k, v in params.items() if k not in excluded and v is not None)
    concat = "".join(f"{k}{v}" for k, v in ordered)
    sign_str = f"{app_secret}{concat}{app_secret}"
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()


def _ensure_request_url(base_url: str) -> str:
    """
    Return full request URL. JIMI requires the path /route/rest.
    If base_url is only a host (no path), append /route/rest.
    """
    url = base_url.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        path = (parsed.path or "").strip("/")
        if not path:
            url = url.rstrip("/") + "/route/rest"
    except Exception:
        pass
    return url


async def _request_async(
    base_url: str,
    method: str,
    params: Dict[str, Any],
    app_key: str,
    app_secret: str,
    timeout: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    Async POST to JIMI API with common params and sign.
    Base URL must be the full endpoint including path, e.g.:
    https://hk-open.tracksolidpro.com/route/rest (TSP HK/SG)
    https://eu-open.tracksolidpro.com/route/rest (TSP EU)
    https://us-open.tracksolidpro.com/route/rest (TSP US)
    """
    from datetime import datetime, timezone

    timeout = timeout or getattr(settings, "DEFAULT_REQUESTS_TIMEOUT", (10, 20))
    common = {
        "app_key": app_key,
        "method": method,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "sign_method": "md5",
        "v": "1.0",
    }
    raw_params = {**common, **params}
    # Normalize to strings so sign and body match server (avoids 1001 / invalid sign)
    all_params = _normalize_params(raw_params)
    all_params["sign"] = _build_sign(all_params, app_secret)

    url = _ensure_request_url(base_url)

    logger.debug(
        "TrackSolidPro request: url=%s method=%s param_keys=%s",
        url,
        method,
        sorted(all_params.keys()),
    )

    # JIMI accepts both form and JSON; try form first (most common for REST token APIs)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout[0], read=timeout[1])) as client:
        response = await client.post(url, data=all_params)
        response.raise_for_status()
        data = response.json()

    code = data.get("code")
    if code is not None and code != 0:
        msg = data.get("message", "Unknown error")
        raise RuntimeError(f"TrackSolidPro API error (code={code}): {msg}")

    return data


def _user_pwd_md5(password: str) -> str:
    """Lowercase MD5 of password for user_pwd_md5 parameter."""
    return hashlib.md5(password.encode("utf-8")).hexdigest().lower()


def request_token_demo(
    user_id: str,
    password: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    expires_in: int = 7200,
    timeout: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    One-off token request for debugging / sharing with provider support.
    Uses the same request building as the real client (form POST, MD5 sign).
    Returns dict with:
      - request_url, request_method, request_param_keys
      - request_params_safe: param keys with values masked for sharing (lengths only)
      - response_status_code, response_body (parsed JSON or raw text)
    Does not raise on API error code; response_body contains the full API response.
    """
    from datetime import datetime, timezone

    timeout = timeout or (10, 20)
    method = "jimi.oauth.token.get"
    common = {
        "app_key": app_key,
        "method": method,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "sign_method": "md5",
        "v": "0.9",
    }
    params = {
        "user_id": user_id,
        "user_pwd_md5": password, #_user_pwd_md5(password),
        "expires_in": expires_in,
    }
    raw_params = {**common, **params}
    all_params = _normalize_params(raw_params)

    # Build sign and log the intermediate values for debugging
    excluded = {"sign"}
    ordered = sorted((k, v) for k, v in all_params.items() if k not in excluded and v is not None)
    concat = "".join(f"{k}{v}" for k, v in ordered)
    sign_str = f"{app_secret}{concat}{app_secret}"
    sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()
    all_params["sign"] = sign

    url = _ensure_request_url(base_url)

    logger.debug("[token_debug] url: %s", url)
    logger.debug("[token_debug] user_pwd_md5: %s", all_params.get("user_pwd_md5"))
    logger.debug("[token_debug] sign_input: %s", sign_str)
    logger.debug("[token_debug] sign: %s", sign)
    logger.debug("[token_debug] all_params: %s", all_params)

    # Safe view for sharing: param name -> description (no secrets)
    safe_keys = {"user_pwd_md5", "sign", "app_secret"}
    request_params_safe = {
        k: f"<{len(v)} chars>" if k in safe_keys or "secret" in k.lower() or "pwd" in k.lower() else v
        for k, v in all_params.items()
    }
    # Mask app_key if you want to share only structure
    request_params_safe["app_key"] = f"<{len(app_key)} chars>"

    with httpx.Client(timeout=httpx.Timeout(timeout[0], read=timeout[1])) as client:
        response = client.post(url, headers={"Content-Type": "application/x-www-form-urlencoded"}, data=all_params)

    try:
        response_body = response.json()
    except Exception:
        response_body = response.text

    return {
        "request_url": url,
        "request_method": "POST",
        "request_param_keys": sorted(all_params.keys()),
        "request_params_safe": request_params_safe,
        "response_status_code": response.status_code,
        "response_body": response_body,
    }


async def get_token(
    user_id: str,
    password: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    expires_in: int = 7200,
) -> Dict[str, Any]:
    """
    Obtain access_token via jimi.oauth.token.get.
    password is plain; we compute user_pwd_md5 internally.
    Returns dict with accessToken, refreshToken, expiresIn (seconds).
    """
    params = {
        "user_id": user_id,
        "user_pwd_md5": password, # _user_pwd_md5(password),
        "expires_in": expires_in,
    }
    data = await _request_async(base_url, "jimi.oauth.token.get", params, app_key, app_secret)
    result = data.get("result") or {}
    return {
        "access_token": result.get("accessToken"),
        "refresh_token": result.get("refreshToken"),
        "expires_in": int(result.get("expiresIn", expires_in)),
    }


async def refresh_token(
    access_token: str,
    refresh_token: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    expires_in: int = 7200,
) -> Dict[str, Any]:
    """Refresh access token via jimi.oauth.token.refresh."""
    params = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    }
    data = await _request_async(base_url, "jimi.oauth.token.refresh", params, app_key, app_secret)
    result = data.get("result") or {}
    return {
        "access_token": result.get("accessToken"),
        "refresh_token": result.get("refreshToken"),
        "expires_in": int(result.get("expiresIn", expires_in)),
    }


async def get_cached_token(
    integration_id: str,
    user_id: str,
    password: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    expires_in: int = 7200,
    min_ttl_seconds: int = 60,
) -> str:
    """
    Return a valid access_token, using Redis cache if still valid.
    The Redis TTL is set to (expires_in - min_ttl_seconds) so the key expires
    before the token does. On cache miss, a fresh token is obtained.
    On 401 from the API, caller should call clear_token_cache(integration_id) and retry.
    """
    cached = await _state_manager.get_state(integration_id, _ACTION_ID_TOKEN)
    if cached and cached.get("access_token"):
        return cached["access_token"]

    result = await get_token(
        user_id=user_id,
        password=password,
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        expires_in=expires_in,
    )
    token = result["access_token"]
    exp_sec = int(result.get("expires_in", expires_in))
    ttl = max(exp_sec - min_ttl_seconds, min_ttl_seconds)
    await _state_manager.set_state(
        integration_id,
        _ACTION_ID_TOKEN,
        {"access_token": token, "refresh_token": result.get("refresh_token") or ""},
        ex=ttl,
    )
    return token


async def clear_token_cache(integration_id: str) -> None:
    """Clear the cached token for integration_id."""
    await _state_manager.delete_state(integration_id, _ACTION_ID_TOKEN)


async def list_devices(
    access_token: str,
    target: str,
    app_key: str,
    app_secret: str,
    base_url: str,
) -> List[Dict[str, Any]]:
    """List all devices for account (jimi.user.device.list). Returns list of device dicts."""
    params = {"access_token": access_token, "target": target}
    data = await _request_async(base_url, "jimi.user.device.list", params, app_key, app_secret)
    result = data.get("result")
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


async def get_locations_by_account(
    access_token: str,
    target: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    map_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get latest location for all devices under account (jimi.user.device.location.list)."""
    params = {"access_token": access_token, "target": target}
    if map_type is not None:
        params["map_type"] = map_type
    data = await _request_async(
        base_url, "jimi.user.device.location.list", params, app_key, app_secret
    )
    result = data.get("result")
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


async def get_locations_by_imeis(
    access_token: str,
    imeis: List[str],
    app_key: str,
    app_secret: str,
    base_url: str,
    map_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get latest location for given IMEIs (jimi.device.location.get). Max 100 IMEIs."""
    imei_str = ",".join(str(i) for i in imeis[:100])
    params = {"access_token": access_token, "imeis": imei_str}
    if map_type is not None:
        params["map_type"] = map_type
    data = await _request_async(base_url, "jimi.device.location.get", params, app_key, app_secret)
    result = data.get("result")
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


async def get_device_tracks(
    access_token: str,
    imei: str,
    begin_time: str,
    end_time: str,
    app_key: str,
    app_secret: str,
    base_url: str,
    map_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get track history for a single device (jimi.device.track.list). Returns list of track points."""
    params = {
        "access_token": access_token,
        "imei": imei,
        "begin_time": begin_time,
        "end_time": end_time,
    }
    if map_type is not None:
        params["map_type"] = map_type
    data = await _request_async(base_url, "jimi.device.track.list", params, app_key, app_secret)
    result = data.get("result")
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


def location_to_observation(loc: Dict[str, Any], device_name: str, subject_type: str = "vehicle") -> Dict[str, Any]:
    """
    Map one JIMI location item to Gundi observation dict.
    Handles both location.list fields (speed) and track.list fields (gpsSpeed, ignition, satellite).
    """
    from datetime import datetime

    imei = loc.get("imei") or loc.get("deviceName") or "unknown"
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        raise ValueError(f"Missing lat/lng for imei={imei}")

    gps_time = loc.get("gpsTime")
    if gps_time:
        # JIMI format often "yyyy-MM-dd HH:mm:ss"
        try:
            if isinstance(gps_time, (int, float)):
                dt = datetime.utcfromtimestamp(gps_time)
            else:
                dt = datetime.strptime(str(gps_time).strip()[:19], "%Y-%m-%d %H:%M:%S")
            recorded_at = dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
        except Exception:
            recorded_at = str(gps_time)
    else:
        recorded_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S+00:00")

    additional = {}
    # location.list uses "speed"; track.list uses "gpsSpeed"
    speed_raw = loc.get("speed") if "speed" in loc else loc.get("gpsSpeed")
    if speed_raw is not None:
        try:
            additional["speed_kmph"] = float(speed_raw)
        except (TypeError, ValueError):
            additional["speed_kmph"] = speed_raw
    if loc.get("accStatus") is not None:
        additional["acc_status"] = str(loc["accStatus"])
    if loc.get("posType") is not None:
        additional["pos_type"] = str(loc["posType"])
    if loc.get("direction") is not None:
        additional["direction"] = str(loc["direction"])
    if loc.get("status") is not None:
        additional["status"] = str(loc["status"])
    if loc.get("ignition") is not None:
        additional["ignition"] = str(loc["ignition"])
    if loc.get("satellite") is not None:
        additional["satellite"] = str(loc["satellite"])

    return {
        "source": str(imei),
        "source_name": device_name or str(imei),
        "type": "tracking-device",
        "subject_type": subject_type,
        "recorded_at": recorded_at,
        "location": {"lat": float(lat), "lon": float(lng)},
        "additional": additional if additional else None,
    }
