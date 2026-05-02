#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_BASE_URL = "https://hdhive.com"


class HDHiveOpenAPIError(Exception):
    def __init__(self, code: str, message: str, description: str = "", http_status: Optional[int] = None) -> None:
        super().__init__(description or message or code)
        self.code = code
        self.message = message
        self.description = description
        self.http_status = http_status


@dataclass
class HDHiveClient:
    base_url: str
    api_key: str
    access_token: Optional[str] = None
    timeout: int = 30

    def with_access_token(self, token: Optional[str]) -> "HDHiveClient":
        self.access_token = token
        return self

    def ping(self) -> dict[str, Any]:
        return self._request("GET", "/api/open/ping")

    def quota(self) -> dict[str, Any]:
        return self._request("GET", "/api/open/quota")

    def usage(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict[str, Any]:
        query: dict[str, str] = {}
        if start_date:
            query["start_date"] = start_date
        if end_date:
            query["end_date"] = end_date
        return self._request("GET", "/api/open/usage", query=query)

    def usage_today(self) -> dict[str, Any]:
        return self._request("GET", "/api/open/usage/today")

    def resources(self, media_type: str, tmdb_id: str) -> dict[str, Any]:
        path = "/api/open/resources/{}/{}".format(
            urllib.parse.quote(media_type, safe=""),
            urllib.parse.quote(tmdb_id, safe=""),
        )
        return self._request("GET", path)

    def share_detail(self, slug: str) -> dict[str, Any]:
        path = "/api/open/shares/{}".format(urllib.parse.quote(slug, safe=""))
        return self._request("GET", path)

    def check_resource(self, url: str) -> dict[str, Any]:
        return self._request("POST", "/api/open/check/resource", body={"url": url})

    def unlock(self, slug: str) -> dict[str, Any]:
        return self._request("POST", "/api/open/resources/unlock", body={"slug": slug})

    def list_shares(self, page: Optional[int] = None, page_size: Optional[int] = None) -> dict[str, Any]:
        query: dict[str, str] = {}
        if page is not None:
            query["page"] = str(page)
        if page_size is not None:
            query["page_size"] = str(page_size)
        return self._request("GET", "/api/open/shares", query=query)

    def create_share(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/open/shares", body=payload)

    def patch_share(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = "/api/open/shares/{}".format(urllib.parse.quote(slug, safe=""))
        return self._request("PATCH", path, body=payload)

    def delete_share(self, slug: str) -> dict[str, Any]:
        path = "/api/open/shares/{}".format(urllib.parse.quote(slug, safe=""))
        return self._request("DELETE", path)

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/open/me")

    def checkin(self, is_gambler: Optional[bool] = None) -> dict[str, Any]:
        body = None if is_gambler is None else {"is_gambler": bool(is_gambler)}
        return self._request("POST", "/api/open/checkin", body=body)

    def weekly_free_quota(self) -> dict[str, Any]:
        return self._request("GET", "/api/open/vip/weekly-free-quota")

    def oauth_authorize_preview(
        self,
        client_id: str,
        redirect_uri: str,
        scope: Optional[str] = None,
        state: Optional[str] = None,
    ) -> dict[str, Any]:
        query = {"client_id": client_id, "redirect_uri": redirect_uri}
        if scope:
            query["scope"] = scope
        if state:
            query["state"] = state
        return self._request("GET", "/api/public/openapi/oauth/authorize", query=query)

    def oauth_exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/public/openapi/oauth/token",
            body={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        )

    def oauth_refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/public/openapi/oauth/token",
            body={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

    def oauth_revoke(self, refresh_token: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/public/openapi/oauth/revoke",
            body={"refresh_token": refresh_token},
        )

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        query: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")

        url = self.base_url.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        payload = None
        headers = {"X-API-Key": self.api_key, "Accept": "application/json", "User-Agent": "hdhive-cli/1.0"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise _to_openapi_error(exc.code, exc.reason, raw)
        except urllib.error.URLError as exc:
            raise HDHiveOpenAPIError("NETWORK_ERROR", "Network error", str(exc.reason))


def _to_openapi_error(http_status: int, reason: str, raw: str) -> HDHiveOpenAPIError:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return HDHiveOpenAPIError(str(http_status), reason, raw, http_status=http_status)
    return HDHiveOpenAPIError(
        str(data.get("code", http_status)),
        str(data.get("message", reason)),
        str(data.get("description", "")),
        http_status=http_status,
    )


def _load_json_arg(payload: Optional[str]) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid --data JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--data must be a JSON object")
    return parsed


def _bool_or_none(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False
    raise ValueError("--is-gambler must be true/false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hdhive", description="HDHive OpenAPI single-file CLI and SDK.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL.")
    parser.add_argument("--api-key", help="OpenAPI API Key (or app secret).")
    parser.add_argument("--access-token", help="OpenAPI user access token.")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON response.")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping", help="GET /api/open/ping")
    sub.add_parser("quota", help="GET /api/open/quota")

    p_usage = sub.add_parser("usage", help="GET /api/open/usage")
    p_usage.add_argument("--start-date", help="YYYY-MM-DD")
    p_usage.add_argument("--end-date", help="YYYY-MM-DD")
    sub.add_parser("usage-today", help="GET /api/open/usage/today")

    p_resources = sub.add_parser("resources", help="GET /api/open/resources/:type/:tmdb_id")
    p_resources.add_argument("--type", required=True, choices=["movie", "tv"])
    p_resources.add_argument("--tmdb-id", required=True)

    p_unlock = sub.add_parser("unlock", help="POST /api/open/resources/unlock")
    p_unlock.add_argument("--slug", required=True)
    p_check = sub.add_parser("check-resource", help="POST /api/open/check/resource")
    p_check.add_argument("--url", required=True)

    p_share = sub.add_parser("share", help="GET /api/open/shares/:slug")
    p_share.add_argument("--slug", required=True)
    p_list_shares = sub.add_parser("shares", help="GET /api/open/shares")
    p_list_shares.add_argument("--page", type=int)
    p_list_shares.add_argument("--page-size", type=int)

    p_create_share = sub.add_parser("share-create", help="POST /api/open/shares")
    p_create_share.add_argument("--data", required=True, help="JSON object")
    p_patch_share = sub.add_parser("share-patch", help="PATCH /api/open/shares/:slug")
    p_patch_share.add_argument("--slug", required=True)
    p_patch_share.add_argument("--data", required=True, help="JSON object")
    p_delete_share = sub.add_parser("share-delete", help="DELETE /api/open/shares/:slug")
    p_delete_share.add_argument("--slug", required=True)

    sub.add_parser("me", help="GET /api/open/me")
    p_checkin = sub.add_parser("checkin", help="POST /api/open/checkin")
    p_checkin.add_argument("--is-gambler", help="true/false")
    sub.add_parser("weekly-free-quota", help="GET /api/open/vip/weekly-free-quota")

    p_oauth_preview = sub.add_parser("oauth-authorize-preview", help="GET /api/public/openapi/oauth/authorize")
    p_oauth_preview.add_argument("--client-id", required=True)
    p_oauth_preview.add_argument("--redirect-uri", required=True)
    p_oauth_preview.add_argument("--scope")
    p_oauth_preview.add_argument("--state")

    p_oauth_exchange = sub.add_parser("oauth-exchange-code", help="POST /api/public/openapi/oauth/token")
    p_oauth_exchange.add_argument("--code", required=True)
    p_oauth_exchange.add_argument("--redirect-uri", required=True)
    p_oauth_refresh = sub.add_parser("oauth-refresh", help="POST /api/public/openapi/oauth/token")
    p_oauth_refresh.add_argument("--refresh-token", required=True)
    p_oauth_revoke = sub.add_parser("oauth-revoke", help="POST /api/public/openapi/oauth/revoke")
    p_oauth_revoke.add_argument("--refresh-token", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.api_key:
        raise ValueError("missing API key: pass --api-key")
    client = HDHiveClient(args.base_url, args.api_key, args.access_token, args.timeout)

    cmd = args.command
    if cmd == "ping":
        return client.ping()
    if cmd == "quota":
        return client.quota()
    if cmd == "usage":
        return client.usage(start_date=args.start_date, end_date=args.end_date)
    if cmd == "usage-today":
        return client.usage_today()
    if cmd == "resources":
        return client.resources(args.type, args.tmdb_id)
    if cmd == "unlock":
        return client.unlock(args.slug)
    if cmd == "check-resource":
        return client.check_resource(args.url)
    if cmd == "share":
        return client.share_detail(args.slug)
    if cmd == "shares":
        return client.list_shares(page=args.page, page_size=args.page_size)
    if cmd == "share-create":
        return client.create_share(_load_json_arg(args.data))
    if cmd == "share-patch":
        return client.patch_share(args.slug, _load_json_arg(args.data))
    if cmd == "share-delete":
        return client.delete_share(args.slug)
    if cmd == "me":
        return client.me()
    if cmd == "checkin":
        return client.checkin(_bool_or_none(args.is_gambler))
    if cmd == "weekly-free-quota":
        return client.weekly_free_quota()
    if cmd == "oauth-authorize-preview":
        return client.oauth_authorize_preview(args.client_id, args.redirect_uri, args.scope, args.state)
    if cmd == "oauth-exchange-code":
        return client.oauth_exchange_code(args.code, args.redirect_uri)
    if cmd == "oauth-refresh":
        return client.oauth_refresh(args.refresh_token)
    if cmd == "oauth-revoke":
        return client.oauth_revoke(args.refresh_token)
    raise ValueError(f"unknown command: {cmd}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    except HDHiveOpenAPIError as exc:
        payload = {
            "success": False,
            "code": exc.code,
            "message": exc.message,
            "description": exc.description,
            "http_status": exc.http_status,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
