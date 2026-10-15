"""Dashboard users.

uatu-web set-password [--user NAME]   create a user or change its password (and sign it out everywhere);
                                      the password is asked for, or read from stdin when piped
uatu-web openapi                      the API's OpenAPI schema, as the dashboard's types are generated from
"""

import argparse
import asyncio
import getpass
import json
import sys

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from uatu import db
from uatu.web.auth import service as auth_service
from uatu.web.config import WebSettings

MIN_PASSWORD = 10


def read_password() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Again: ") != first:
        sys.exit("they differ")
    return first


async def set_password(settings: WebSettings, username: str, password: str) -> bool:
    engine = db.create_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await auth_service.set_password(session, username, password)
    finally:
        await engine.dispose()


def openapi() -> dict:
    """The API's schema, as any deployment serves it: it depends on no setting."""
    from uatu.web.main import create_app

    return create_app(WebSettings()).openapi()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uatu-web", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    set_cmd = commands.add_parser("set-password", help="create a user or change its password")
    set_cmd.add_argument("--user", default="oscar", help="(default: oscar)")
    commands.add_parser("openapi", help="print the API's OpenAPI schema (frontend/openapi.json)")
    args = parser.parse_args()
    if args.command == "openapi":
        print(json.dumps(openapi(), indent=2, ensure_ascii=False))
        return
    try:
        settings = WebSettings()
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        sys.exit(2)
    if not 1 <= len(args.user) <= 40:
        sys.exit("a user name has 1 to 40 characters")
    password = read_password()
    if not MIN_PASSWORD <= len(password) <= 128:
        sys.exit(f"a password has {MIN_PASSWORD} to 128 characters")
    created = asyncio.run(set_password(settings, args.user, password))
    print(f"{'created' if created else 'new password for'} {args.user}")


if __name__ == "__main__":
    main()
