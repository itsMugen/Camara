"""Command line interface.

stdout carries only the resulting URL, and nothing on failure, so the tool composes
with other commands: ``url=$(url-shortener --expand ...) && curl -L "$url"``.
Errors go to stderr, prefixed with "error:", with a distinct exit code each.
Log records (``-v``, ``-vv``) go to stderr too.
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from enum import IntEnum
from importlib.metadata import PackageNotFoundError, version
from typing import TextIO

from url_shortener.config import Settings
from url_shortener.errors import (
    ConfigurationError,
    DatabaseUnavailableError,
    InvalidUrlError,
    LinkExpiredError,
    LinkNotFoundError,
    ShortenerError,
    StorageError,
)
from url_shortener.repository import MongoLinkRepository
from url_shortener.service import ShortenerService
from url_shortener.urls import to_ascii_uri


class ExitCode(IntEnum):
    """The process exit status, part of the documented interface (README, "Exit codes")."""

    OK = 0
    LINK_UNAVAILABLE = 1  # short URL not found or expired
    USAGE = 2  # bad arguments, invalid URL or invalid configuration (argparse uses 2 too)
    STORAGE = 3  # MongoDB unreachable or failing
    INTERNAL = 4  # could not allocate a short code
    INTERRUPTED = 130  # Ctrl-C: 128 + SIGINT, as shells report it


# Looked up along the exception's class hierarchy, so a subclass (such as
# DatabaseUnavailableError, a StorageError) gets its parent's exit code.
EXIT_CODES: dict[type[ShortenerError], ExitCode] = {
    LinkNotFoundError: ExitCode.LINK_UNAVAILABLE,
    LinkExpiredError: ExitCode.LINK_UNAVAILABLE,
    InvalidUrlError: ExitCode.USAGE,
    ConfigurationError: ExitCode.USAGE,
    StorageError: ExitCode.STORAGE,
    ShortenerError: ExitCode.INTERNAL,
}

DATABASE_HINT = (
    "hint: start MongoDB with `docker compose up -d mongo` from the project directory, "
    "or set SHORTENER_MONGO_URI to a running MongoDB."
)

# UTC, like every timestamp the tool stores or prints.
LOG_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s"
# Indexed by the number of -v flags: warnings only, then progress, then detail.
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)

ServiceFactory = Callable[[Settings], AbstractContextManager[ShortenerService]]


@contextmanager
def open_service(settings: Settings) -> Iterator[ShortenerService]:
    """A service backed by MongoDB; the connection is closed on exit."""
    repository = MongoLinkRepository.connect(
        settings.mongo_uri,
        settings.mongo_database,
        timeout_ms=settings.mongo_timeout_ms,
        expired_retention_seconds=settings.expired_retention_seconds,
    )
    try:
        yield ShortenerService(repository, settings)
    finally:
        repository.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="url-shortener",
        description="Shorten URLs and expand short URLs. Short URLs expire after "
        "SHORTENER_TTL_SECONDS seconds (default 3600).",
        epilog="Exit codes: 0 success, 1 short URL not found or expired, 2 invalid input or "
        "configuration, 3 storage unavailable, 4 internal error, 130 interrupted.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--minify", metavar="URL", help="return a short URL for URL")
    action.add_argument("--expand", metavar="SHORT_URL", help="return the URL behind SHORT_URL")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="log what the tool does to stderr; repeat (-vv) for more detail",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    return parser


def _version() -> str:
    """The installed version, or "unknown" when run from a checkout that was never installed.

    Looked up while the parser is built, so it must not fail: a missing
    distribution would otherwise break every command, not just ``--version``.
    """
    try:
        return version("url-shortener")
    except PackageNotFoundError:
        return "unknown"


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: ServiceFactory = open_service,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> ExitCode:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = build_parser().parse_args(argv)  # exits with 2 on usage errors

    try:
        with (
            _log_to(stderr, LOG_LEVELS[min(args.verbose, len(LOG_LEVELS) - 1)]),
            service_factory(Settings.from_env(env)) as service,
        ):
            if args.minify is not None:
                output = service.minify(args.minify)
            else:
                output = service.expand(args.expand)
    except ShortenerError as exc:
        print(_encodable(f"error: {exc}", stderr), file=stderr)
        if isinstance(exc, DatabaseUnavailableError):
            print(DATABASE_HINT, file=stderr)
        return _exit_code(exc)
    except KeyboardInterrupt:
        print("error: interrupted", file=stderr)
        return ExitCode.INTERRUPTED

    # A terminal that cannot show Unicode (Windows code pages,
    # PYTHONIOENCODING=ascii) still gets a working URL rather than a traceback.
    try:
        print(output if _can_encode(output, stdout) else to_ascii_uri(output), file=stdout)
        stdout.flush()
    except BrokenPipeError:
        # The reader went away (``url-shortener ... | head -c1``). The work is
        # done, so this is not an error; point fd 1 at /dev/null so the
        # interpreter's final flush of stdout does not fail again at exit.
        _silence(stdout)
    return ExitCode.OK


def _silence(stream: TextIO) -> None:
    with suppress(OSError, ValueError):  # not a real file (StringIO in tests)
        os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())


@contextmanager
def _log_to(stream: TextIO, level: int) -> Iterator[None]:
    """Send this package's log records at ``level`` and above to ``stream`` during one run.

    Only the package's own logger is configured, never the root logger, and
    the change is undone on exit, so ``main`` can run many times in one
    process (as the tests do) without stacking handlers.
    """
    package_logger = logging.getLogger("url_shortener")
    handler = _EncodingSafeHandler(stream)
    formatter = logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    previous_level = package_logger.level
    package_logger.addHandler(handler)
    package_logger.setLevel(level)
    try:
        yield
    finally:
        package_logger.removeHandler(handler)
        package_logger.setLevel(previous_level)


class _EncodingSafeHandler(logging.StreamHandler[TextIO]):
    """Escapes what the stream cannot encode, as error messages do, instead of failing."""

    def format(self, record: logging.LogRecord) -> str:
        return _encodable(super().format(record), self.stream)


def _exit_code(exc: ShortenerError) -> ExitCode:
    return next(EXIT_CODES[cls] for cls in type(exc).__mro__ if cls in EXIT_CODES)


def _encodable(text: str, stream: TextIO) -> str:
    """``text``, with backslash escapes for characters ``stream`` cannot encode."""
    if _can_encode(text, stream):
        return text
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _can_encode(text: str, stream: TextIO) -> bool:
    encoding = getattr(stream, "encoding", None)
    if not encoding:  # e.g. io.StringIO, which holds str
        return True
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True
