#!/usr/bin/env python3
"""
One-off TrackSolidPro token request for debugging and for sharing with provider support.

Reads credentials from environment variables, performs a single jimi.oauth.token.get
request (same as the integration), and prints a support-friendly summary.

Usage:
  export TRACKSOLIDPRO_BASE_URL="https://hk-open.tracksolidpro.com/route/rest"
  export TRACKSOLIDPRO_USER_ID="your_user_id"
  export TRACKSOLIDPRO_PASSWORD="your_password"
  export TRACKSOLIDPRO_APP_KEY="your_app_key"
  export TRACKSOLIDPRO_APP_SECRET="your_app_secret"
  python scripts/request_tracksolidpro_token.py

Or from project root with .env:
  python scripts/request_tracksolidpro_token.py
"""

import json
import os
import sys

# Load .env from project root if present (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main() -> None:
    base_url = os.environ.get("TRACKSOLIDPRO_BASE_URL", "").strip()
    user_id = os.environ.get("TRACKSOLIDPRO_USER_ID", "").strip()
    password = os.environ.get("TRACKSOLIDPRO_PASSWORD", "").strip()
    app_key = os.environ.get("TRACKSOLIDPRO_APP_KEY", "").strip()
    app_secret = os.environ.get("TRACKSOLIDPRO_APP_SECRET", "").strip()

    missing = []
    if not base_url:
        missing.append("TRACKSOLIDPRO_BASE_URL")
    if not user_id:
        missing.append("TRACKSOLIDPRO_USER_ID")
    if not password:
        missing.append("TRACKSOLIDPRO_PASSWORD")
    if not app_key:
        missing.append("TRACKSOLIDPRO_APP_KEY")
    if not app_secret:
        missing.append("TRACKSOLIDPRO_APP_SECRET")

    if missing:
        print("Missing environment variables:", ", ".join(missing), file=sys.stderr)
        print(
            "Set TRACKSOLIDPRO_BASE_URL, TRACKSOLIDPRO_USER_ID, TRACKSOLIDPRO_PASSWORD, "
            "TRACKSOLIDPRO_APP_KEY, TRACKSOLIDPRO_APP_SECRET",
            file=sys.stderr,
        )
        sys.exit(1)

    # Import after potential exit so we don't load app if env is missing
    from app.actions.tracksolidpro_client import request_token_demo

    result = request_token_demo(
        user_id=user_id,
        password=password,
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
    )

    # Summary safe to share with support (no secrets)
    print("--- Request (safe to share) ---")
    print("URL:", result["request_url"])
    print("Method:", result["request_method"])
    print("Param keys:", result["request_param_keys"])
    print("Params (sensitive values masked):", json.dumps(result["request_params_safe"], indent=2))
    print()
    print("--- Response ---")
    print("HTTP status:", result["response_status_code"])
    print("Body:", json.dumps(result["response_body"], indent=2))
    if isinstance(result["response_body"], dict):
        code = result["response_body"].get("code")
        msg = result["response_body"].get("message", "")
        if code is not None and code != 0:
            print()
            print("API error: code={} message={}".format(code, msg))


if __name__ == "__main__":
    main()
