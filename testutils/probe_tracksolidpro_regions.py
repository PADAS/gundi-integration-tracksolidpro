#!/usr/bin/env python3
"""
Probe all TrackSolidPro regional API endpoints to find which one matches your account.

Tries jimi.oauth.token.get against each regional base URL and reports the outcome.
The region that returns code=0 (and an accessToken) is the one to configure.

Reads credentials from environment variables (or a .env file in the project root):
  TRACKSOLIDPRO_USER_ID
  TRACKSOLIDPRO_PASSWORD
  TRACKSOLIDPRO_APP_KEY
  TRACKSOLIDPRO_APP_SECRET

Usage:
  export TRACKSOLIDPRO_USER_ID="your_user_id"
  export TRACKSOLIDPRO_PASSWORD="your_password"
  export TRACKSOLIDPRO_APP_KEY="your_app_key"
  export TRACKSOLIDPRO_APP_SECRET="your_app_secret"
  python testutils/probe_tracksolidpro_regions.py
"""

import logging
import os
import sys

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

REGIONAL_URLS = [
    ("HK / APAC", "https://hk-open.tracksolidpro.com/route/rest"),
    ("EU",        "https://eu-open.tracksolidpro.com/route/rest"),
    ("US",        "https://us-open.tracksolidpro.com/route/rest"),
    ("CN",        "https://open.tracksolidpro.com/route/rest"),
    ("10000track", "https://open.10000track.com/route/rest"),
#    ("Tramigo", "https://dashcam.tramigovideo.com/route/rest")
]


def main() -> None:
    user_id    = os.environ.get("TRACKSOLIDPRO_USER_ID", "").strip()
    password   = os.environ.get("TRACKSOLIDPRO_PASSWORD", "").strip()
    app_key    = os.environ.get("TRACKSOLIDPRO_APP_KEY", "").strip()
    app_secret = os.environ.get("TRACKSOLIDPRO_APP_SECRET", "").strip()

    missing = [name for name, val in [
        ("TRACKSOLIDPRO_USER_ID",    user_id),
        ("TRACKSOLIDPRO_PASSWORD",   password),
        ("TRACKSOLIDPRO_APP_KEY",    app_key),
        ("TRACKSOLIDPRO_APP_SECRET", app_secret),
    ] if not val]

    if missing:
        print("Missing environment variables:", ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    from app.actions.tracksolidpro_client import request_token_demo

    print(f"Probing {len(REGIONAL_URLS)} regional endpoints for user_id={user_id!r}\n")
    print(f"{'Region':<14} {'URL':<52} {'HTTP':<6} {'API code':<10} Result")
    print("-" * 110)

    matched = []

    for label, url in REGIONAL_URLS:
        try:
            result = request_token_demo(
                user_id=user_id,
                password=password,
                app_key=app_key,
                app_secret=app_secret,
                base_url=url,
            )
            http_status = result["response_status_code"]
            body = result["response_body"]
            if isinstance(body, dict):
                api_code = body.get("code", "?")
                api_msg  = body.get("message", "")
                result_obj = body.get("result") or {}
                token = result_obj.get("accessToken") if isinstance(result_obj, dict) else None
                if api_code == 0 and token:
                    summary = f"OK — accessToken received ({len(token)} chars)"
                    matched.append((label, url))
                else:
                    summary = f"code={api_code} {api_msg}"
            else:
                api_code = "?"
                summary = f"Non-JSON response: {str(body)[:60]}"
        except Exception as exc:
            http_status = "ERR"
            api_code = "ERR"
            summary = str(exc)[:80]

        print(f"{label:<14} {url:<52} {str(http_status):<6} {str(api_code):<10} {summary}")

    print()
    if matched:
        print("Matched region(s):")
        for label, url in matched:
            print(f"  {label}: {url}")
        print()
        print(f"Set TRACKSOLIDPRO_BASE_URL={matched[0][1]!r}")
    else:
        print("No region returned a valid token. Check your credentials and app_key/app_secret.")


if __name__ == "__main__":
    main()
