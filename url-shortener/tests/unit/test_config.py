import pytest

from url_shortener.codes import MAX_CODE_LENGTH
from url_shortener.config import Settings
from url_shortener.errors import ConfigurationError
from url_shortener.urls import MAX_BASE_URL_LENGTH, parse_short_url


def test_defaults_when_environment_is_empty():
    assert Settings.from_env({}) == Settings()


def test_blank_values_fall_back_to_defaults():
    assert (
        Settings.from_env({"SHORTENER_TTL_SECONDS": "  ", "SHORTENER_BASE_URL": ""}) == Settings()
    )


def test_reads_every_variable():
    settings = Settings.from_env(
        {
            "SHORTENER_MONGO_URI": "mongodb://db:27017",
            "SHORTENER_MONGO_DATABASE": "links",
            "SHORTENER_BASE_URL": "https://sho.rt/",
            "SHORTENER_TTL_SECONDS": "30",
            "SHORTENER_CODE_LENGTH": "9",
            "SHORTENER_EXPIRED_RETENTION_SECONDS": "0",
            "SHORTENER_MONGO_TIMEOUT_MS": "500",
        }
    )
    assert settings == Settings(
        mongo_uri="mongodb://db:27017",
        mongo_database="links",
        base_url="https://sho.rt",  # trailing slash removed
        ttl_seconds=30,
        code_length=9,
        expired_retention_seconds=0,
        mongo_timeout_ms=500,
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("TTL_SECONDS", "abc", "must be an integer"),
        ("TTL_SECONDS", "1.5", "must be an integer"),
        ("TTL_SECONDS", "0", "between 1 and 2147483647"),
        ("TTL_SECONDS", "-10", "between 1 and 2147483647"),
        ("TTL_SECONDS", "2147483648", "between 1 and 2147483647"),  # would overflow datetime
        ("TTL_SECONDS", "1" + "0" * 20, "between 1 and 2147483647"),
        ("CODE_LENGTH", "3", "between 4 and 32"),
        ("CODE_LENGTH", "33", "between 4 and 32"),
        ("EXPIRED_RETENTION_SECONDS", "-1", "between 0 and 2147483647"),
        ("EXPIRED_RETENTION_SECONDS", "2147483648", "between 0 and 2147483647"),  # int32 in Mongo
        ("MONGO_TIMEOUT_MS", "0", "between 1 and 999999999"),
        ("MONGO_TIMEOUT_MS", "1000000000", "between 1 and 999999999"),  # pymongo's limit
        ("BASE_URL", "myurlshortener.com", "must start with http"),
        ("BASE_URL", "ftp://myurlshortener.com", "must start with http"),
        ("BASE_URL", "https://myurlshortener.com/?x=1", "query string or fragment"),
        ("BASE_URL", "https://myurlshortener.com/#x", "query string or fragment"),
        # A bare "?" or "#" splits to an empty query or fragment but would still
        # appear inside every short URL, which --expand could not read.
        ("BASE_URL", "https://myurlshortener.com/?", "query string or fragment"),
        ("BASE_URL", "https://myurlshortener.com/#", "query string or fragment"),
        ("BASE_URL", "https://myurlshortener.com/s?/", "query string or fragment"),
        ("BASE_URL", "https://myurlshortener.com/" + "a" * MAX_BASE_URL_LENGTH, "maximum"),
        ("BASE_URL", "https://myurlshortener.com:bad", "Invalid port"),
        ("BASE_URL", "https://myurlshortener.com:0", "Invalid port"),
        ("BASE_URL", "https://user:pass@myurlshortener.com", "credentials"),
        ("BASE_URL", "https://my_url<shortener>.com", "Invalid host"),
        ("BASE_URL", "https://my url.com", "whitespace"),
        ("MONGO_DATABASE", "url/shortener", "must not contain '/'"),
        ("MONGO_DATABASE", "url.shortener", "must not contain '.'"),
        ("MONGO_DATABASE", "url shortener", "must not contain ' '"),
        ("MONGO_DATABASE", "a$b", "must not contain '\\$'"),
        ("MONGO_DATABASE", "d" * 64, "at most 63 bytes"),
    ],
)
def test_rejects_invalid_values(name, value, message):
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env({f"SHORTENER_{name}": value})


def test_accepts_the_largest_values():
    settings = Settings.from_env(
        {
            "SHORTENER_TTL_SECONDS": "2147483647",
            "SHORTENER_EXPIRED_RETENTION_SECONDS": "2147483647",
            "SHORTENER_MONGO_TIMEOUT_MS": "999999999",
            "SHORTENER_BASE_URL": "https://bücher.de/s/",
        }
    )
    assert settings.ttl_seconds == settings.expired_retention_seconds == 2**31 - 1
    assert settings.mongo_timeout_ms == 10**9 - 1
    assert settings.base_url == "https://bücher.de/s"


def test_longest_base_url_still_yields_expandable_short_urls():
    prefix = "https://myurlshortener.com/"
    base_url = prefix + "a" * (MAX_BASE_URL_LENGTH - len(prefix))
    settings = Settings.from_env({"SHORTENER_BASE_URL": base_url})
    assert settings.base_url == base_url
    longest_code = "b" * MAX_CODE_LENGTH
    assert parse_short_url(f"{base_url}/{longest_code}", base_url=base_url) == longest_code
