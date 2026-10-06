import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from tests.conftest import FakeClock
from tests.integration.conftest import MONGO_URI
from url_shortener.errors import LinkExpiredError, LinkNotFoundError
from url_shortener.repository import CODE_INDEX, TTL_INDEX, MongoLinkRepository
from url_shortener.service import ShortenerService, utc_now

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MONGO_URI, reason="SHORTENER_TEST_MONGO_URI is not set"),
]

URL = "https://www.example.com/path?q=search"


def test_indexes_are_created_on_first_write(real_repository, real_settings, links):
    ShortenerService(real_repository, real_settings).minify(URL)
    indexes = links.index_information()
    assert indexes[CODE_INDEX]["unique"] is True
    assert indexes[TTL_INDEX]["expireAfterSeconds"] == real_settings.expired_retention_seconds


def test_changing_retention_updates_the_ttl_index_in_place(real_settings, links):
    for retention in (100, 200):
        repository = MongoLinkRepository(links, expired_retention_seconds=retention)
        repository.ensure_indexes()
        assert links.index_information()[TTL_INDEX]["expireAfterSeconds"] == retention


def test_round_trip_and_expiry(real_repository, real_settings):
    clock = FakeClock()
    service = ShortenerService(real_repository, real_settings, clock=clock)
    first = service.minify(URL)
    assert service.minify(URL) == first
    assert service.expand(first) == URL

    clock.advance(60)
    with pytest.raises(LinkExpiredError):
        service.expand(first)

    second = service.minify(URL)
    assert second != first
    with pytest.raises(LinkNotFoundError):
        service.expand(first)


def test_stored_expiry_matches_computed_expiry_to_the_millisecond(real_repository, real_settings):
    now = utc_now()
    service = ShortenerService(real_repository, real_settings, clock=lambda: now)
    short = service.minify(URL)
    link = real_repository.find_by_code(short.rsplit("/", 1)[1])
    assert link.expires_at == now + timedelta(seconds=real_settings.ttl_seconds)


@pytest.mark.parametrize("expired_first", [False, True])
def test_concurrent_minify_of_same_url_yields_one_link(
    real_repository, real_settings, links, expired_first
):
    clock = FakeClock()
    if expired_first:
        ShortenerService(real_repository, real_settings, clock=clock).minify(URL)
        clock.advance(120)

    workers = 32
    barrier = threading.Barrier(workers)

    def minify(_):
        service = ShortenerService(real_repository, real_settings, clock=clock)
        barrier.wait()
        return service.minify(URL)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = set(pool.map(minify, range(workers)))

    assert len(results) == 1
    assert links.count_documents({}) == 1
    assert links.find_one()["code"] == results.pop().rsplit("/", 1)[1]


def test_cli_end_to_end(real_settings):
    env = {
        # Not the developer's SHORTENER_* settings: the assertions below assume the defaults.
        **{name: value for name, value in os.environ.items() if not name.startswith("SHORTENER_")},
        "SHORTENER_MONGO_URI": real_settings.mongo_uri,
        "SHORTENER_MONGO_DATABASE": real_settings.mongo_database,
    }

    def run(*args):
        return subprocess.run(  # noqa: S603
            [sys.executable, "-m", "url_shortener", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    minified = run(f"--minify={URL}")
    assert minified.returncode == 0, minified.stderr
    short = minified.stdout.strip()

    assert run(f"--minify={URL}").stdout.strip() == short
    expanded = run(f"--expand={short}")
    assert (expanded.returncode, expanded.stdout) == (0, URL + "\n")

    missing = run("--expand=https://myurlshortener.com/zzzzzzz")
    assert missing.returncode == 1
    assert "not found" in missing.stderr
