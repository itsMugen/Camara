"""The repository contract, run against a real MongoDB."""

import pytest

from tests.integration.conftest import MONGO_URI
from tests.repository_contract import LinkRepositoryContract

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MONGO_URI, reason="SHORTENER_TEST_MONGO_URI is not set"),
]


class TestMongoRepository(LinkRepositoryContract):
    @pytest.fixture
    def repository(self, real_repository):
        return real_repository
