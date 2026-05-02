#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# 允许从 hdhive 子目录直接运行：把仓库根目录加入 path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hdhive import HDHiveClient, HDHiveOpenAPIError
from hdhive.unlock_core import can_unlock_by_points, is_free_resource


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auto_unlock",
        description="Check share info and auto unlock by free/points rules.",
    )
    parser.add_argument("--base-url", default="https://hdhive.com", help="HDHive API base URL.")
    parser.add_argument("--api-key", required=True, help="OpenAPI API key.")
    parser.add_argument("--access-token", help="Optional user access token.")
    parser.add_argument("--slug", required=True, help="Share slug.")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty print output JSON.")
    parser.add_argument("--url-only", action="store_true", help="Print only unlocked URL text.")

    parser.add_argument(
        "--max-points",
        type=int,
        default=None,
        help="Points threshold for paid unlock, e.g. 4.",
    )
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="Enable paid unlock when unlock_points <= --max-points.",
    )
    return parser.parse_args(argv)


def print_result(result: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    client = HDHiveClient(
        base_url=args.base_url,
        api_key=args.api_key,
        access_token=args.access_token,
        timeout=args.timeout,
    )

    try:
        share_resp = client.share_detail(args.slug)
        share_data = share_resp.get("data") or {}
        unlock_points = share_data.get("unlock_points")

        result: dict[str, Any] = {
            "slug": args.slug,
            "title": share_data.get("title"),
            "unlock_message": share_data.get("unlock_message"),
            "unlock_points": unlock_points,
            "decision": "skip",
            "reason": "",
            "unlock_url": None,
            "unlock_full_url": None,
        }

        if is_free_resource(share_data):
            result["decision"] = "unlock"
            result["reason"] = 'free resource ("unlock_message=免费资源" and "unlock_points=null")'
        elif can_unlock_by_points(share_data, args.allow_paid, args.max_points):
            result["decision"] = "unlock"
            result["reason"] = f"paid resource but unlock_points <= {args.max_points}"
        else:
            if unlock_points is None:
                result["reason"] = "not free by rule, and paid unlock rule is not enabled"
            elif args.allow_paid and args.max_points is not None:
                result["reason"] = f"unlock_points={unlock_points} > max_points={args.max_points}"
            else:
                result["reason"] = "paid unlock disabled (use --allow-paid --max-points N)"

        if result["decision"] == "unlock":
            unlock_resp = client.unlock(args.slug)
            unlock_data = unlock_resp.get("data") or {}
            result["unlock_url"] = unlock_data.get("url")
            result["unlock_full_url"] = unlock_data.get("full_url") or unlock_data.get("url")
            result["unlock_response_message"] = unlock_resp.get("message")
            result["unlock_response_code"] = unlock_resp.get("code")

            if args.url_only:
                print(result["unlock_full_url"] or result["unlock_url"] or "")
                return 0
        else:
            if args.url_only:
                print("", file=sys.stderr)
                return 3

        print_result(result, args.pretty)
        return 0

    except HDHiveOpenAPIError as exc:
        err = {
            "success": False,
            "code": exc.code,
            "message": exc.message,
            "description": exc.description,
            "http_status": exc.http_status,
        }
        print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
