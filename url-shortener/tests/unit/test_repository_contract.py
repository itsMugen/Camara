"""The repository contract, run against both implementations that need no server."""

import pytest

from tests.conftest import InMemoryLinkRepository
from tests.repository_contract import LinkRepositoryContract


class TestInMemoryRepository(LinkRepositoryContract):
    @pytest.fixture
    def repository(self):
        return InMemoryLinkRepository()


class TestMongoRepositoryOnMongomock(LinkRepositoryContract):
    @pytest.fixture
    def repository(self, mongo_repository):
        return mongo_repository
