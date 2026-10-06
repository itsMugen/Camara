"""Domain errors.

Every error the tool reports to a user, apart from argparse's usage errors and
Ctrl-C, is a ``ShortenerError`` carrying a message written for that user. The
CLI prints the message and maps the error's class to an exit code, so the
service layer never prints anything and never knows about exit codes.
"""

from datetime import datetime


class ShortenerError(Exception):
    """Base class for every error the tool reports to the user."""


class ConfigurationError(ShortenerError):
    """An environment variable holds an invalid value."""


class InvalidUrlError(ShortenerError):
    """The URL given on the command line is not acceptable."""


class LinkNotFoundError(ShortenerError):
    """No short link exists for the given code."""

    def __init__(self, short_url: str) -> None:
        super().__init__(f"Short URL not found: {short_url}")
        self.short_url = short_url


class LinkExpiredError(ShortenerError):
    """The short link exists but its expiration time has passed."""

    def __init__(self, short_url: str, expired_at: datetime) -> None:
        super().__init__(
            f"Short URL expired at {expired_at.isoformat(timespec='seconds')}: {short_url}"
        )
        self.short_url = short_url
        self.expired_at = expired_at


class CodeGenerationError(ShortenerError):
    """No free short code was found after the maximum number of attempts."""


class StorageError(ShortenerError):
    """MongoDB is unreachable or rejected an operation."""


class DatabaseUnavailableError(StorageError):
    """No MongoDB server answered within the configured timeout."""


class ClaimConflictError(Exception):
    """Internal: a write lost a uniqueness race (URL already linked, or code taken).

    Not a ``ShortenerError``: the service always handles it and it never
    reaches the user.
    """
