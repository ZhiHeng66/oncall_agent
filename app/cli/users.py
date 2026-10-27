"""Manage local users without placing passwords in shell history or source files."""

import argparse
import asyncio
from getpass import getpass
from uuid import UUID

from app.auth.contracts import UserRole
from app.auth.service import AuthenticationService
from app.config import Settings
from app.services.user_store import UserAlreadyExistsError


def _read_password() -> str:
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("password confirmation does not match")
    return password


async def _create_user(args: argparse.Namespace, role: UserRole) -> None:
    service = AuthenticationService(Settings())
    await service.startup()
    try:
        user = await service.create_user(args.username, _read_password(), role=role)
    finally:
        await service.shutdown()
    print(f"Created {user.role} user: {user.username} ({user.user_id})")


async def _set_active(args: argparse.Namespace, is_active: bool) -> None:
    service = AuthenticationService(Settings())
    await service.startup()
    try:
        await service.set_active(UUID(args.user_id), is_active)
    finally:
        await service.shutdown()
    print(f"Updated user state: {args.user_id}")


async def async_main(args: argparse.Namespace) -> None:
    try:
        if args.command == "create-admin":
            await _create_user(args, "admin")
        elif args.command == "create-user":
            await _create_user(args, "user")
        elif args.command == "disable-user":
            await _set_active(args, False)
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except UserAlreadyExistsError as exc:
        raise SystemExit("Username already exists") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create-admin", "create-user"):
        command = commands.add_parser(name)
        command.add_argument("username")
    disable = commands.add_parser("disable-user")
    disable.add_argument("user_id")
    return parser


def main() -> None:
    asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    main()
