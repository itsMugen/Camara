"""Integration tests run against a real MongoDB.

They are skipped unless SHORTENER_TEST_MONGO_URI is set, so ``pytest`` works
anywhere, while ``make test-all`` (MongoDB from ``make up``) and CI run everything.
Each test gets its own throwaway database.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from pymongo import MongoClient

from url_shortener.config import Settings
from url_shortener.repository import COLLECTION_NAME, MongoLinkRepository

MONGO_URI = os.environ.get("SHORTENER_TEST_MONGO_URI")


@pytest.fixture
def mongo_client() -> Iterator[MongoClient]:
    client: MongoClient = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture
def database_name(mongo_client: MongoClient) -> Iterator[str]:
    name = f"url_shortener_test_{uuid.uuid4().hex[:12]}"
    yield name
    mongo_client.drop_database(name)


@pytest.fixture
def real_settings(database_name: str) -> Settings:
    assert MONGO_URI is not None
    return Settings(
        mongo_uri=MONGO_URI, mongo_database=database_name, ttl_seconds=60, mongo_timeout_ms=3000
    )


@pytest.fixture
def real_repository(real_settings: Settings) -> Iterator[MongoLinkRepository]:
    repository = MongoLinkRepository.connect(
        real_settings.mongo_uri,
        real_settings.mongo_database,
        timeout_ms=real_settings.mongo_timeout_ms,
        expired_retention_seconds=real_settings.expired_retention_seconds,
    )
    yield repository
    repository.close()


@pytest.fixture
def links(mongo_client: MongoClient, database_name: str):
    return mongo_client[database_name][COLLECTION_NAME]
