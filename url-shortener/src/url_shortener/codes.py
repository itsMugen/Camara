"""Short code generation, written from scratch (no shortening libraries).

Codes are drawn uniformly at random from the 62 characters ``[0-9a-zA-Z]``
with the operating system's CSPRNG (``secrets``); 7 characters give 62**7,
about 3.5 trillion, possible codes. Random codes cannot be enumerated and
need no shared counter; collisions are caught by the unique index on ``code``
and retried by ``ShortenerService.minify``. See README, decision 1.
"""

import re
import secrets
import string
from collections.abc import Callable

ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase
MIN_CODE_LENGTH = 4
MAX_CODE_LENGTH = 32

# Accepts any length in the allowed range, not just the configured one, so
# links created before a change of SHORTENER_CODE_LENGTH keep working.
CODE_PATTERN = re.compile(rf"[{re.escape(ALPHABET)}]{{{MIN_CODE_LENGTH},{MAX_CODE_LENGTH}}}")

CodeGenerator = Callable[[], str]


def generate_code(length: int) -> str:
    """Return a random code of ``length`` characters from ``ALPHABET``.

    ``length`` is validated with the rest of the configuration (``Settings``).
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def is_valid_code(code: str) -> bool:
    """Return True if ``code`` could have been produced by ``generate_code``."""
    return CODE_PATTERN.fullmatch(code) is not None
