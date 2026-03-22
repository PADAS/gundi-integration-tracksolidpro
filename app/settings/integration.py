# TrackSolidPro (JIMI) integration
from .base import env

INTEGRATION_TYPE_SLUG = env.str("INTEGRATION_TYPE_SLUG", "tracksolidpro")

# Default JIMI API base URL when not set in integration auth config
TRACKSOLIDPRO_BASE_URL = env.str(
    "TRACKSOLIDPRO_BASE_URL",
    "",
)
