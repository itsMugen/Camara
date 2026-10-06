"""The behaviour every ``LinkRepository`` must have, written once.

Subclass ``LinkRepositoryContract`` in a ``Test*`` class and provide a
``repository`` fixture to run these tests against an implementation. They run
against the in-memory repository and MongoDB on mongomock
(``tests/unit/test_repository_contract.py``) and against a real MongoDB
(``tests/integration/test_repository_contract.py``).

The service relies on exactly these rules, so an implementation that passes
them can be swapped in without touching the service.
"""

from datetime import timedelta

import pytest

from tests.conftest import T0
from url_shortener.errors import ClaimConflictError
from url_shortener.models import Link

KEY = "https://www.example.com/path?q=search"
OTHER_KEY = "https://other.example/"
TTL = timedelta(seconds=60)


def claim(repository, *, key=KEY, url=None, code="aaaaaaa", now=T0):
    repository.claim(key=key, url=url or key, code=code, now=now, expires_at=now + TTL)


class LinkRepositoryContract:
    def test_lookups_on_an_empty_store_find_nothing(self, repository):
        assert repository.find_by_key(KEY) is None
        assert repository.find_by_code("aaaaaaa") is None

    def test_claimed_link_is_found_by_key_and_by_code(self, repository):
        claim(repository, url="https://WWW.example.com/path?q=search", code="aaaaaaa")
        expected = Link(
            code="aaaaaaa",
            url="https://WWW.example.com/path?q=search",
            key=KEY,
            created_at=T0,
            expires_at=T0 + TTL,
        )
        assert repository.find_by_key(KEY) == expected
        assert repository.find_by_code("aaaaaaa") == expected

    def test_code_lookup_is_case_sensitive(self, repository):
        claim(repository, code="aaaaaaa")
        assert repository.find_by_code("AAAAAAA") is None

    def test_claim_conflicts_while_the_key_has_an_active_link(self, repository):
        claim(repository, code="aaaaaaa")
        with pytest.raises(ClaimConflictError):
            claim(repository, code="bbbbbbb", now=T0 + TTL - timedelta(milliseconds=1))
        assert repository.find_by_key(KEY).code == "aaaaaaa"
        assert repository.find_by_code("bbbbbbb") is None

    def test_claim_replaces_a_link_that_expired_and_retires_its_code(self, repository):
        claim(repository, code="aaaaaaa")
        later = T0 + TTL  # expired exactly now: a link is valid strictly before expires_at
        claim(repository, code="bbbbbbb", now=later)
        link = repository.find_by_key(KEY)
        assert (link.code, link.created_at, link.expires_at) == ("bbbbbbb", later, later + TTL)
        assert repository.find_by_code("aaaaaaa") is None

    def test_expired_link_can_be_reclaimed_with_its_own_code(self, repository):
        claim(repository, code="aaaaaaa")
        claim(repository, code="aaaaaaa", now=T0 + TTL)
        assert repository.find_by_key(KEY).expires_at == T0 + 2 * TTL

    def test_claim_conflicts_when_the_code_belongs_to_another_key(self, repository):
        claim(repository, key=OTHER_KEY, code="aaaaaaa")
        with pytest.raises(ClaimConflictError):
            claim(repository, code="aaaaaaa")
        assert repository.find_by_key(KEY) is None
        assert repository.find_by_code("aaaaaaa").key == OTHER_KEY

    def test_expired_links_keep_their_code(self, repository):
        claim(repository, key=OTHER_KEY, code="aaaaaaa")
        with pytest.raises(ClaimConflictError):
            claim(repository, code="aaaaaaa", now=T0 + 2 * TTL)
        assert repository.find_by_code("aaaaaaa").key == OTHER_KEY

    def test_links_for_different_keys_are_independent(self, repository):
        claim(repository, key=KEY, code="aaaaaaa")
        claim(repository, key=OTHER_KEY, code="bbbbbbb")
        assert repository.find_by_key(KEY).code == "aaaaaaa"
        assert repository.find_by_key(OTHER_KEY).code == "bbbbbbb"
