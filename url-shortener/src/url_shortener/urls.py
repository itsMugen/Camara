"""Validation and canonicalisation of the URLs the tool accepts.

Two concerns live here:

* ``parse_long_url`` validates a URL to be shortened and derives its
  *canonical key*, the value used to decide whether two inputs are "the same
  URL" for the "return the same short URL" rule.
* ``parse_short_url`` validates a short URL given to ``--expand`` and extracts
  its code.

Canonicalisation is deliberately conservative. Only transformations that cannot
change which resource a URL names are applied: scheme and host are
case-insensitive, default ports can be omitted, an empty path is "/",
internationalised host names are compared in punycode and IPv6 addresses in
compressed form. Paths, query strings and
fragments are kept byte for byte because servers are free to treat
``/Path`` and ``/path``, or ``?a=1&b=2`` and ``?b=2&a=1``, differently.
"""

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

from url_shortener.codes import MAX_CODE_LENGTH, is_valid_code
from url_shortener.errors import InvalidUrlError

# Most browsers, CDNs and proxies handle URLs up to roughly 2 KB reliably;
# accepting more would let one CLI call store arbitrarily large documents.
MAX_URL_LENGTH = 2048
# A short URL is "<base URL>/<code>" and must itself fit in MAX_URL_LENGTH,
# or --expand would refuse the tool's own output.
MAX_BASE_URL_LENGTH = MAX_URL_LENGTH - 1 - MAX_CODE_LENGTH
ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}

# Copying a URL from a web page or a document often drags along whitespace
# and invisible characters (byte order mark, zero-width spaces). They are
# removed from both ends; anywhere else they are an error.
_EDGE_JUNK = re.compile("^[\\s\ufeff\u200b-\u200d\u2060]+|[\\s\ufeff\u200b-\u200d\u2060]+$")
# Python's "idna" codec implements IDNA 2003, which maps these characters away
# ("faß.de" becomes "fass.de"). Browsers follow IDNA 2008 / UTS 46 and keep
# them, so "faß.de" and "fass.de" are two different sites. (IDNA 2008 also
# keeps the zero-width joiners U+200C and U+200D, but those are not printable
# and ``_split`` rejects them with the other control characters.)
_IDNA2003_DEVIATIONS = frozenset("\u00df\u03c2")
_LABEL_SEPARATORS = re.compile("[.\u3002\uff0e\uff61]")
_MAX_LABEL_LENGTH = 63
# What a host may contain once converted to ASCII. "_" is not valid in host
# names but appears in real DNS names, and browsers accept it.
_ASCII_HOST = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.?")
# A host whose last label is a number is an IPv4 address, not a domain name
# (the WHATWG URL standard's "ends in a number" rule), so it must be one.
_NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")
# Every printable ASCII character: ``quote`` leaves them (and existing
# percent-escapes) untouched and only encodes what is not ASCII.
_PRINTABLE_ASCII = "".join(chr(c) for c in range(0x21, 0x7F))


@dataclass(frozen=True)
class LongUrl:
    """A validated URL to shorten."""

    original: str
    """The URL as the user typed it (surrounding whitespace and invisible characters removed)."""

    key: str
    """Canonical form used to detect that two inputs are the same URL."""


def parse_long_url(raw: str, *, base_url: str | None = None) -> LongUrl:
    """Validate ``raw`` and return it with its canonical key.

    ``base_url`` is the prefix of the shortener's own short URLs: shortening
    one of them is refused, since expanding it would only return another
    short URL that may expire first.
    """
    url = _clean(raw)
    parts = _split(url, what="URL")
    host = _canonical_host(parts)

    if parts.username is not None or parts.password is not None:
        raise InvalidUrlError(
            "URLs with embedded credentials (user:password@host) are not accepted, "
            "they would be stored in plain text."
        )
    if base_url is not None and _is_under(parts, base_url):
        raise InvalidUrlError(f"{url} is already a short URL from this service.")

    scheme = parts.scheme.lower()
    port = _port(parts)
    netloc = host if port is None or port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    key = f"{scheme}://{netloc}{parts.path or '/'}"
    if parts.query:
        key += f"?{parts.query}"
    if parts.fragment:
        key += f"#{parts.fragment}"
    return LongUrl(original=url, key=key)


def parse_short_url(raw: str, *, base_url: str) -> str:
    """Return the code contained in the short URL ``raw``.

    The URL must lie under the configured ``base_url``: same host and port,
    and a path starting with the base URL's path. Either scheme is
    accepted, a trailing slash is tolerated and a query string or fragment
    (``?utm_source=...`` added by a chat app, say) is ignored.
    """
    url = _clean(raw)
    parts = _split(url, what="short URL")
    if not _is_under(parts, base_url):
        raise InvalidUrlError(f"{url} is not a short URL from {base_url}.")

    code = parts.path[len(_path_prefix(base_url)) :]
    if code.endswith("/"):
        code = code[:-1]
    if not is_valid_code(code):
        raise InvalidUrlError(f"{url} does not contain a valid short code.")
    return code


def validate_base_url(base_url: str) -> None:
    """Check that ``base_url`` can prefix short URLs; raises ``InvalidUrlError``."""
    if len(base_url) > MAX_BASE_URL_LENGTH:
        raise InvalidUrlError(
            f"The base URL is {len(base_url)} characters long, "
            f"the maximum is {MAX_BASE_URL_LENGTH} (so that short URLs fit in {MAX_URL_LENGTH})."
        )
    parts = _split(base_url, what="base URL")
    _canonical_host(parts)
    _port(parts)
    if parts.username is not None or parts.password is not None:
        raise InvalidUrlError("The base URL must not contain credentials.")
    # Checked on the text, not on urlsplit's result: a bare trailing "?" or "#"
    # splits to an empty query or fragment but would still end up inside every
    # short URL ("https://x.com/?/abcd123"), which --expand cannot read.
    if "?" in base_url or "#" in base_url:
        raise InvalidUrlError("The base URL must not contain a query string or fragment.")


def to_ascii_uri(url: str) -> str:
    """Return ``url`` written with ASCII characters only (RFC 3987 IRI to URI).

    The host is converted to punycode and other non-ASCII characters are
    percent-encoded as UTF-8. Browsers treat both spellings as the same
    address, so this is used to print a URL on a terminal that cannot
    display Unicode.
    """
    parts = urlsplit(url)
    port = parts.port
    netloc = _canonical_host(parts) + ("" if port is None else f":{port}")
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            quote(parts.path, safe=_PRINTABLE_ASCII),
            quote(parts.query, safe=_PRINTABLE_ASCII),
            quote(parts.fragment, safe=_PRINTABLE_ASCII),
        )
    )


def _clean(raw: str) -> str:
    return _EDGE_JUNK.sub("", raw)


def _split(url: str, *, what: str) -> SplitResult:
    if not url:
        raise InvalidUrlError(f"The {what} must not be empty.")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError(
            f"The {what} is {len(url)} characters long, the maximum is {MAX_URL_LENGTH}."
        )
    if any(ch.isspace() or not ch.isprintable() for ch in url):
        raise InvalidUrlError(
            f"The {what} contains whitespace or control characters; "
            "percent-encode them (a space is %20)."
        )
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # e.g. an unbalanced IPv6 bracket
        raise InvalidUrlError(f"The {what} is malformed: {exc}.") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrlError(f"The {what} must start with http:// or https:// (got {url!r}).")
    if not parts.hostname:
        raise InvalidUrlError(f"The {what} has no host name.")
    return parts


def _canonical_host(parts: SplitResult) -> str:
    """Lower-cased host, internationalised names converted to punycode."""
    host = parts.hostname or ""
    if parts.netloc.rpartition("@")[2].startswith("["):  # urlsplit removed the brackets
        return f"[{_canonical_ipv6(host)}]"
    if host.startswith(".") or ".." in host:
        raise InvalidUrlError(f"Invalid host name: {host!r}.")
    try:
        ascii_host = ".".join(_ascii_label(label) for label in _LABEL_SEPARATORS.split(host))
    except UnicodeError as exc:  # empty or over-long label, invalid code points
        raise InvalidUrlError(f"Invalid host name: {host!r}.") from exc
    ascii_host = ascii_host.lower()
    if not _ASCII_HOST.fullmatch(ascii_host):
        raise InvalidUrlError(f"Invalid host name: {host!r}.")
    if _NUMERIC_LABEL.fullmatch(ascii_host.rstrip(".").rpartition(".")[2]):
        return _canonical_ipv4(ascii_host, original=host)
    return ascii_host


def _canonical_ipv4(ascii_host: str, *, original: str) -> str:
    """The dotted-quad form of an IPv4 host, so ``1.2.3.4`` and ``1.2.3.4.`` match.

    Only the plain ``a.b.c.d`` spelling is accepted. Browsers also read
    shorthand (``127.1``), hex (``0x7f.0.0.1``) and octal octets, but each
    browser engine differs in the details, so those are rejected rather than
    guessed at. ``999.999.999.999`` and ``1.2.3.4.5`` are not addresses at all.
    """
    try:
        return str(ipaddress.IPv4Address(ascii_host.rstrip(".")))
    except ValueError as exc:
        raise InvalidUrlError(f"Invalid host name: {original!r}.") from exc


def _ascii_label(label: str) -> str:
    if label.isascii():
        encoded = label
    elif _IDNA2003_DEVIATIONS.intersection(label):
        # Encode as IDNA 2008 / UTS 46 does: compatibility-map (fullwidth
        # letters, ligatures), lower-case, then punycode. NFKC keeps ß and ς.
        normalised = unicodedata.normalize("NFKC", label).lower()
        encoded = "xn--" + normalised.encode("punycode").decode("ascii")
    else:
        encoded = label.encode("idna").decode("ascii")
    if len(encoded) > _MAX_LABEL_LENGTH:
        raise UnicodeError(f"label longer than {_MAX_LABEL_LENGTH} characters")
    return encoded


def _canonical_ipv6(host: str) -> str:
    """Compressed form of an IPv6 literal, so ``2001:0db8::1`` = ``2001:db8::1``."""
    address, sep, zone = host.partition("%")
    try:
        return ipaddress.IPv6Address(address).compressed + sep + zone
    except ValueError as exc:  # IPvFuture ("[v1.x]") or garbage between brackets
        raise InvalidUrlError(f"Invalid IPv6 address: {host!r}.") from exc


def _port(parts: SplitResult) -> int | None:
    try:
        port = parts.port
    except ValueError as exc:  # non numeric or outside 0-65535
        raise InvalidUrlError(f"Invalid port in {parts.geturl()!r}.") from exc
    if port == 0:  # reserved, nothing can listen on it
        raise InvalidUrlError(f"Invalid port in {parts.geturl()!r}.")
    return port


def _explicit_port(parts: SplitResult) -> int | None:
    """The port, or None when it is the scheme's default (so http and https match)."""
    port = _port(parts)
    return None if port == DEFAULT_PORTS[parts.scheme.lower()] else port


def _is_under(parts: SplitResult, base_url: str) -> bool:
    """True if the URL ``parts`` lies under ``base_url`` (same host and port, path prefix).

    The scheme is ignored and a trailing dot on the host (``example.com.``,
    the fully qualified form) does not matter.
    """
    base = urlsplit(base_url)
    return (
        _canonical_host(parts).rstrip(".") == _canonical_host(base).rstrip(".")
        and _explicit_port(parts) == _explicit_port(base)
        and (parts.path or "/").startswith(_path_prefix(base_url))
    )


def _path_prefix(base_url: str) -> str:
    return urlsplit(base_url).path.rstrip("/") + "/"
