"""TrackSolidPro action configurations and get_auth_config helper."""

import pydantic

from app.actions.core import (
    AuthActionConfiguration,
    ExecutableActionMixin,
    PullActionConfiguration,
)
from app.services.errors import ConfigurationNotFound
from app.services.utils import (
    FieldWithUIOptions,
    GlobalUISchemaOptions,
    UIOptions,
    find_config_for_action,
)


class TrackSolidProAuthConfig(AuthActionConfiguration, ExecutableActionMixin):
    """Credentials for TrackSolidPro (JIMI) API."""

    user_id: str = pydantic.Field(
        ...,
        title="User ID",
        description="TrackSolid account (user_id for API)",
    )
    password: pydantic.SecretStr = pydantic.Field(
        ...,
        title="Password",
        description="TrackSolid account password (stored securely; MD5 computed when calling API)",
        format="password",
    )
    app_key: str = pydantic.Field(
        ...,
        title="App Key",
        description="JIMI app key",
    )
    app_secret: pydantic.SecretStr = pydantic.Field(
        ...,
        title="App Secret",
        description="JIMI app secret for request signing",
        format="password",
    )
    base_url: str = pydantic.Field(
        ...,
        title="Base URL",
        description="JIMI API base URL (e.g. from your JIMI/TrackSolid documentation)",
    )
    expires_in: int = pydantic.Field(
        7200,
        ge=60,
        le=7200,
        title="Token expiry (seconds)",
        description="Access token validity, 60–7200",
    )

    ui_global_options = GlobalUISchemaOptions(
        order=["user_id", "password", "app_key", "app_secret", "base_url", "expires_in"],
    )


def get_auth_config(integration):
    """Get TrackSolidPro auth credentials from the integration's auth action config."""
    auth_config = find_config_for_action(
        configurations=integration.configurations,
        action_id="auth",
    )
    if not auth_config:
        raise ConfigurationNotFound(
            f"Authentication settings for integration {str(integration.id)} "
            "are missing. Please configure the Auth action in the portal."
        )
    return TrackSolidProAuthConfig.parse_obj(auth_config.data)


class PullDevicesConfig(ExecutableActionMixin, PullActionConfiguration):
    """Configuration for pull_devices action."""

    target: str = pydantic.Field(
        "",
        title="Target account",
        description="Account to query (leave empty to use auth user_id)",
    )

    ui_global_options = GlobalUISchemaOptions(
        order=["target"],
    )


class PullObservationsConfig(PullActionConfiguration):
    """Configuration for pull_observations action."""

    subject_type: str = FieldWithUIOptions(
        "truck",
        title="Subject type",
        description="Subject type to set on observations (e.g. truck)",
    )

    ui_global_options = GlobalUISchemaOptions(
        order=["subject_type"],
    )


class PullTrackHistoryConfig(ExecutableActionMixin, PullActionConfiguration):
    """Configuration for pull_track_history action."""

    subject_type: str = FieldWithUIOptions(
        "truck",
        title="Subject type",
        description="Subject type to set on observations (e.g. truck)",
    )
    lookback_minutes: int = pydantic.Field(
        1440,
        ge=10,
        le=10080,
        title="Lookback window (minutes)",
        description="How far back to query track history on each run (default 1440 = 24 h)",
    )

    ui_global_options = GlobalUISchemaOptions(
        order=["subject_type", "lookback_minutes"],
    )
