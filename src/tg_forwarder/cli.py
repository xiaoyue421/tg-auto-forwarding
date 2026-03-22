from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
import multiprocessing as mp
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
import uvicorn

from tg_forwarder.config import ConfigError, TelegramSettings, load_config, load_telegram_settings
from tg_forwarder.env_utils import update_env_file
from tg_forwarder.logging_utils import configure_logging
from tg_forwarder.supervisor import ProcessSupervisor
from tg_forwarder.telegram_clients import build_telegram_client, connect_client_with_proxy_pool
from tg_forwarder.webapp import build_web_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram realtime channel forwarder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start the supervisor")
    run_parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Config file path. If missing, .env simple mode is used automatically.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate config or .env simple mode")
    validate_parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="Config file path. If missing, .env simple mode is used automatically.",
    )

    login_parser = subparsers.add_parser("login", help="Interactively create a session string")
    login_parser.add_argument("-c", "--config", help="Optional config or .env path for api_id/api_hash/proxy")
    login_parser.add_argument("--api-id", type=int, help="Telegram api_id")
    login_parser.add_argument("--api-hash", help="Telegram api_hash")
    login_parser.add_argument(
        "--output",
        help="Optional output file for the generated session_string",
    )
    login_parser.add_argument(
        "--save-env",
        nargs="?",
        const="TG_SESSION_STRING",
        help="Write session_string into .env. Default variable is TG_SESSION_STRING.",
    )
    login_parser.add_argument(
        "--env-file",
        default=".env",
        help="Target .env file for --save-env, default is .env",
    )

    web_parser = subparsers.add_parser("web", help="Start the web control panel")
    web_parser.add_argument(
        "-c",
        "--config",
        default=".env",
        help="Config path for the control panel, default is .env",
    )
    web_parser.add_argument("--host", default="127.0.0.1", help="Web host, default 127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8080, help="Web port, default 8080")

    return parser


def main() -> int:
    mp.freeze_support()
    parser = build_parser()
    args = parser.parse_args()
    configure_logging()

    try:
        if args.command == "run":
            return run_command(args.config)
        if args.command == "validate":
            return validate_command(args.config)
        if args.command == "login":
            return login_command(args)
        if args.command == "web":
            return web_command(args.config, args.host, args.port)
    except ConfigError as exc:
        parser.exit(status=2, message=f"Config error: {exc}\n")
    return 0


def run_command(config_path: str) -> int:
    config = load_config(config_path)
    config.build_runtime_workers()
    supervisor = ProcessSupervisor(config.config_path)
    supervisor.run_forever()
    return 0


def validate_command(config_path: str) -> int:
    config = load_config(config_path)
    runtime_workers = config.build_runtime_workers()
    print(f"Config OK. Active workers: {len(runtime_workers)}")
    print(f"Loaded from: {config.config_path}")
    for worker in runtime_workers:
        targets = ", ".join(str(target.chat) for target in worker.targets)
        print(f"- {worker.name}: {worker.source} -> {targets}")
    return 0


def login_command(args: argparse.Namespace) -> int:
    telegram = None
    if args.config:
        telegram = load_telegram_settings(args.config)
    api_id = args.api_id or (telegram.api_id if telegram else None)
    api_hash = args.api_hash or (telegram.api_hash if telegram else None)
    if not api_id or not api_hash:
        raise ConfigError("login needs --api-id and --api-hash, or use --config")
    login_settings = telegram or TelegramSettings(api_id=api_id, api_hash=api_hash)
    session_string = asyncio.run(
        interactive_login(
            settings=login_settings,
        )
    )
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(session_string, encoding="utf-8")
        print(f"session_string written to: {output_path}")
    if args.save_env:
        update_env_file(Path(args.env_file), {args.save_env: session_string})
        print(f"session_string saved to {args.env_file} as {args.save_env}")
    if not args.output and not args.save_env:
        print(session_string)
    return 0


def web_command(config_path: str, host: str, port: int) -> int:
    app = build_web_app(config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


async def interactive_login(
    settings: TelegramSettings,
) -> str:
    client = await connect_client_with_proxy_pool(
        settings=settings,
        client_builder=lambda proxy: build_telegram_client(
            session=StringSession(),
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            proxy=proxy,
            device_model="TGForwarderLogin",
            app_version="0.1.0",
            receive_updates=False,
        ),
        scope="interactive login client",
    )
    try:
        phone = input("Phone number in international format, e.g. +8613800000000: ").strip()
        if not phone:
            raise ConfigError("phone number is required")
        await client.send_code_request(phone)
        code = input("Telegram login code: ").strip()
        if not code:
            raise ConfigError("login code is required")
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = getpass("Two-step verification password: ").strip()
            if not password:
                raise ConfigError("two-step verification password is required")
            await client.sign_in(password=password)
        return client.session.save()
    finally:
        await client.disconnect()
