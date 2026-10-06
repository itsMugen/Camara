import logging
from datetime import timedelta

import pytest

from tests.conftest import BASE_URL, T0, ScriptedCodes
from url_shortener.config import MAX_SECONDS, Settings
from url_shortener.errors import (
    CodeGenerationError,
    InvalidUrlError,
    LinkExpiredError,
    LinkNotFoundError,
)
from url_shortener.service import MAX_CLAIM_ATTEMPTS, ShortenerService, utc_now

URL = "https://www.example.com/path?q=search"


def stored(repository, short_url):
    """The stored link behind ``short_url``."""
    return repository.find_by_code(short_url.rsplit("/", 1)[1])


class TestMinify:
    def test_returns_short_url_on_configured_domain(self, service, repository):
        short = service.minify(URL)
        assert short.startswith("https://myurlshortener.com/")
        assert len(short.rsplit("/", 1)[1]) == 7
        link = stored(repository, short)
        assert link.url == URL
        assert link.created_at == T0
        assert link.expires_at == T0 + timedelta(seconds=60)

    def test_same_url_returns_same_short_url_while_valid(self, service, repository, clock):
        first = service.minify(URL)
        clock.advance(59)
        assert service.minify(URL) == first
        # Re-minifying does not extend the lifetime of the link.
        assert stored(repository, first).expires_at == T0 + timedelta(seconds=60)

    def test_equivalent_spelling_returns_same_short_url(self, service):
        first = service.minify("https://www.example.com/path?q=search")
        second = service.minify("HTTPS://WWW.EXAMPLE.COM:443/path?q=search")
        assert second == first
        # The first spelling is what expand returns.
        assert service.expand(second) == "https://www.example.com/path?q=search"

    def test_different_urls_get_different_short_urls(self, service):
        assert service.minify(URL) != service.minify(URL + "&page=2")

    def test_expired_url_can_be_minified_again_with_a_new_code(self, service, repository, clock):
        first = service.minify(URL)
        clock.advance(60)
        second = service.minify(URL)
        assert second != first
        assert stored(repository, second).expires_at == clock.now + timedelta(seconds=60)

    def test_old_code_is_retired_when_url_is_minified_again(self, service, clock):
        first = service.minify(URL)
        clock.advance(61)
        second = service.minify(URL)
        with pytest.raises(LinkNotFoundError):
            service.expand(first)
        assert service.expand(second) == URL

    def test_invalid_url_is_rejected_before_touching_storage(self, settings, clock):
        class ExplodingRepository:
            def __getattr__(self, name):
                raise AssertionError("storage must not be used")

        service = ShortenerService(ExplodingRepository(), settings, clock=clock)
        with pytest.raises(InvalidUrlError):
            service.minify("not a url")

    def test_refuses_own_short_urls(self, service):
        short = service.minify(URL)
        with pytest.raises(InvalidUrlError, match="already a short URL"):
            service.minify(short)

    def test_lookalike_internationalised_domains_get_separate_links(self, service):
        # Python's IDNA 2003 codec would turn "faß.de" into "fass.de"; browsers do not.
        german = service.minify("https://faß.de/")
        ascii_ = service.minify("https://fass.de/")
        assert german != ascii_
        assert service.expand(german) == "https://faß.de/"
        assert service.expand(ascii_) == "https://fass.de/"

    def test_unicode_url_round_trips_unchanged(self, service):
        url = "https://bücher.de/straße/日本語?q=café#📚"
        assert service.expand(service.minify(url)) == url

    def test_url_pasted_with_invisible_characters_reuses_the_link(self, service):
        first = service.minify(URL)
        second = service.minify(f"\ufeff{URL}\u200b\n")
        assert second == first

    def test_other_pages_of_a_shared_short_host_can_be_shortened(self, repository, clock):
        settings = Settings(base_url="https://example.com/s", ttl_seconds=60)
        service = ShortenerService(repository, settings, clock=clock)
        short = service.minify("https://example.com/blog")
        assert short.startswith("https://example.com/s/")
        with pytest.raises(InvalidUrlError, match="already a short URL"):
            service.minify(short)

    def test_longest_allowed_ttl_does_not_overflow(self, repository, clock):
        settings = Settings(base_url=BASE_URL, ttl_seconds=MAX_SECONDS)
        service = ShortenerService(repository, settings, clock=clock)
        short = service.minify(URL)
        assert stored(repository, short).expires_at == T0 + timedelta(seconds=MAX_SECONDS)
        assert service.expand(short) == URL

    def test_retries_when_random_code_is_taken(self, repository, settings, clock):
        service = ShortenerService(
            repository,
            settings,
            clock=clock,
            code_generator=ScriptedCodes("aaaaaaa", "aaaaaaa", "bbbbbbb"),
        )
        assert service.minify("https://one.example/").endswith("/aaaaaaa")
        assert service.minify("https://two.example/").endswith("/bbbbbbb")

    def test_logs_each_retry(self, repository, settings, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="url_shortener")
        service = ShortenerService(
            repository,
            settings,
            clock=clock,
            code_generator=ScriptedCodes("aaaaaaa", "aaaaaaa", "bbbbbbb"),
        )
        service.minify("https://one.example/")
        service.minify("https://two.example/")
        assert f"Claim of code aaaaaaa conflicted (attempt 1 of {MAX_CLAIM_ATTEMPTS})" in (
            caplog.text
        )

    def test_expired_code_is_not_reassigned_while_retained(self, repository, settings, clock):
        service = ShortenerService(
            repository,
            settings,
            clock=clock,
            code_generator=ScriptedCodes("aaaaaaa", "aaaaaaa", "ccccccc"),
        )
        service.minify("https://one.example/")
        clock.advance(120)
        # "aaaaaaa" still belongs to the expired document, so a new code is drawn.
        assert service.minify("https://two.example/").endswith("/ccccccc")
        with pytest.raises(LinkExpiredError):
            service.expand("https://myurlshortener.com/aaaaaaa")

    def test_gives_up_after_bounded_attempts(self, repository, settings, clock):
        codes = ["aaaaaaa"] * (MAX_CLAIM_ATTEMPTS + 1)
        service = ShortenerService(
            repository, settings, clock=clock, code_generator=ScriptedCodes(*codes)
        )
        service.minify("https://one.example/")
        with pytest.raises(CodeGenerationError, match="SHORTENER_CODE_LENGTH"):
            service.minify("https://two.example/")

    def test_concurrent_writer_wins_race_and_its_link_is_returned(
        self, repository, settings, clock
    ):
        """Simulate another process creating the link between our read and our write."""
        other = ShortenerService(
            repository, settings, clock=clock, code_generator=ScriptedCodes("otherxx")
        )
        real_find = repository.find_by_key
        calls = {"n": 0}

        def find_then_lose_race(key):
            calls["n"] += 1
            if calls["n"] == 1:
                link = real_find(key)  # sees nothing...
                other.minify(URL)  # ...then the other process writes
                return link
            return real_find(key)

        repository.find_by_key = find_then_lose_race
        mine = ShortenerService(
            repository, settings, clock=clock, code_generator=ScriptedCodes("minexxx")
        )
        assert mine.minify(URL).endswith("/otherxx")


class TestExpand:
    def test_round_trip(self, service):
        assert service.expand(service.minify(URL)) == URL

    def test_unknown_code(self, service):
        with pytest.raises(LinkNotFoundError, match="not found"):
            service.expand("https://myurlshortener.com/fstp4")

    def test_valid_until_the_last_millisecond(self, service, clock):
        short = service.minify(URL)
        clock.advance(59.999)
        assert service.expand(short) == URL

    def test_expired_exactly_at_expiry_time(self, service, clock):
        short = service.minify(URL)
        clock.advance(60)
        with pytest.raises(LinkExpiredError, match="expired at 2100-01-01T12:01:00") as info:
            service.expand(short)
        assert info.value.expired_at == T0 + timedelta(seconds=60)

    def test_expired_even_if_ttl_monitor_has_not_deleted_it_yet(self, service, clock, repository):
        short = service.minify(URL)
        clock.advance(3600)
        assert repository.find_by_code(short.rsplit("/", 1)[1]) is not None  # still stored
        with pytest.raises(LinkExpiredError):
            service.expand(short)

    def test_short_url_from_another_domain(self, service):
        with pytest.raises(InvalidUrlError, match="not a short URL"):
            service.expand("https://bit.ly/fstp4")

    def test_trailing_slash_and_tracking_params_are_tolerated(self, service):
        short = service.minify(URL)
        assert service.expand(short + "/") == URL
        assert service.expand(short + "?utm_source=mail") == URL

    def test_code_lookup_is_case_sensitive(self, repository, settings, clock):
        service = ShortenerService(
            repository, settings, clock=clock, code_generator=ScriptedCodes("AbCdEfG")
        )
        service.minify(URL)
        assert service.expand("https://myurlshortener.com/AbCdEfG") == URL
        with pytest.raises(LinkNotFoundError):
            service.expand("https://myurlshortener.com/abcdefg")

    def test_codes_survive_a_change_of_code_length(self, repository, settings, clock):
        short = ShortenerService(repository, settings, clock=clock).minify(URL)
        longer = Settings(base_url=BASE_URL, ttl_seconds=60, code_length=12)
        assert ShortenerService(repository, longer, clock=clock).expand(short) == URL


def test_utc_now_is_aware_and_millisecond_precise():
    now = utc_now()
    assert now.utcoffset() == timedelta(0)
    assert now.microsecond % 1000 == 0
