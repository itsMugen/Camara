import importlib.metadata
import io
import logging
import re
from contextlib import nullcontext
from datetime import UTC, datetime

import dns.resolver
import pymongo.errors
import pytest

from url_shortener import cli
from url_shortener.errors import (
    CodeGenerationError,
    ConfigurationError,
    DatabaseUnavailableError,
    InvalidUrlError,
    LinkExpiredError,
    LinkNotFoundError,
    ShortenerError,
    StorageError,
)
from url_shortener.repository import MongoLinkRepository
from url_shortener.service import ShortenerService

URL = "https://www.example.com/path?q=search"
SHORT_URL = "https://myurlshortener.com/fstp4"


@pytest.fixture
def factory(repository, clock):
    """Builds the service against the in-memory repository and the fake clock."""
    return lambda settings: nullcontext(ShortenerService(repository, settings, clock=clock))


@pytest.fixture
def run(factory):
    """Run the CLI in-process; returns (exit code, stdout, stderr)."""

    def _run(*argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        env = {"SHORTENER_TTL_SECONDS": "60", **(env or {})}
        code = cli.main(list(argv), service_factory=factory, env=env, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    return _run


def test_minify_prints_only_the_short_url(run):
    code, out, err = run(f"--minify={URL}")
    assert code == cli.ExitCode.OK
    assert out.startswith("https://myurlshortener.com/")
    assert out.count("\n") == 1
    assert err == ""


def test_minify_accepts_space_separated_value(run):
    assert run("--minify", URL)[0] == cli.ExitCode.OK


def test_minify_then_expand(run):
    _, short, _ = run(f"--minify={URL}")
    code, out, _ = run(f"--expand={short.strip()}")
    assert code == cli.ExitCode.OK
    assert out == URL + "\n"


def test_expand_unknown(run):
    code, out, err = run("--expand=https://myurlshortener.com/fstp4")
    assert code == cli.ExitCode.LINK_UNAVAILABLE
    assert out == ""
    assert err == "error: Short URL not found: https://myurlshortener.com/fstp4\n"


def test_expand_expired(run, clock):
    _, short, _ = run(f"--minify={URL}")
    clock.advance(60)
    code, out, err = run(f"--expand={short.strip()}")
    assert code == cli.ExitCode.LINK_UNAVAILABLE
    assert out == ""
    assert "expired" in err


def test_invalid_url(run):
    code, out, err = run("--minify=www.example.com")
    assert code == cli.ExitCode.USAGE
    assert out == ""
    assert err.startswith("error: ")


def test_invalid_configuration(run):
    code, _, err = run(f"--minify={URL}", env={"SHORTENER_TTL_SECONDS": "soon"})
    assert code == cli.ExitCode.USAGE
    assert "SHORTENER_TTL_SECONDS" in err


def test_storage_failure_is_reported_without_traceback(clock):
    def factory(settings):
        class Down:
            def find_by_key(self, key):
                raise StorageError("MongoDB error: localhost:27017: Connection refused")

        return nullcontext(ShortenerService(Down(), settings, clock=clock))

    err = io.StringIO()
    code = cli.main([f"--minify={URL}"], service_factory=factory, env={}, stderr=err)
    assert code == cli.ExitCode.STORAGE
    assert err.getvalue().startswith("error: MongoDB error")
    assert "hint:" not in err.getvalue()


def test_unreachable_database_prints_how_to_start_it(clock):
    def factory(settings):
        class Unreachable:
            def find_by_code(self, code):
                raise DatabaseUnavailableError(
                    "MongoDB is unreachable: localhost:27017: [Errno 61] Connection refused"
                )

        return nullcontext(ShortenerService(Unreachable(), settings, clock=clock))

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ["--expand=https://myurlshortener.com/abcdefg"],
        service_factory=factory,
        env={},
        stdout=out,
        stderr=err,
    )
    assert code == cli.ExitCode.STORAGE
    assert out.getvalue() == ""
    first, hint = err.getvalue().splitlines()
    assert first == "error: MongoDB is unreachable: localhost:27017: [Errno 61] Connection refused"
    assert hint.startswith("hint: start MongoDB with `docker compose up -d mongo`")
    assert "SHORTENER_MONGO_URI" in hint


class FailingService:
    """Stands in for the service and fails every command with ``error``."""

    def __init__(self, error):
        self.error = error

    def minify(self, url):
        raise self.error

    def expand(self, short_url):
        raise self.error


@pytest.mark.parametrize(
    ("argv", "error", "exit_code"),
    [
        ([f"--expand={SHORT_URL}"], LinkNotFoundError(SHORT_URL), cli.ExitCode.LINK_UNAVAILABLE),
        (
            [f"--expand={SHORT_URL}"],
            LinkExpiredError(SHORT_URL, datetime(2100, 1, 1, tzinfo=UTC)),
            cli.ExitCode.LINK_UNAVAILABLE,
        ),
        (["--minify=www.example.com"], InvalidUrlError("no scheme"), cli.ExitCode.USAGE),
        ([f"--minify={URL}"], ConfigurationError("bad setting"), cli.ExitCode.USAGE),
        ([f"--minify={URL}"], StorageError("MongoDB error"), cli.ExitCode.STORAGE),
        ([f"--expand={SHORT_URL}"], DatabaseUnavailableError("down"), cli.ExitCode.STORAGE),
        ([f"--minify={URL}"], CodeGenerationError("no free code"), cli.ExitCode.INTERNAL),
        ([f"--minify={URL}"], KeyboardInterrupt(), cli.ExitCode.INTERRUPTED),
    ],
    ids=[
        "not-found",
        "expired",
        "invalid-url",
        "configuration",
        "storage",
        "database-unavailable",
        "code-generation",
        "interrupted",
    ],
)
def test_failures_leave_stdout_empty(argv, error, exit_code):
    """Whatever fails, ``curl "$(url-shortener ...)"`` never receives an error message as a URL."""

    def factory(settings):
        return nullcontext(FailingService(error))

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, service_factory=factory, env={}, stdout=out, stderr=err)
    assert code == exit_code
    assert out.getvalue() == ""
    assert err.getvalue().startswith("error: ")


def ascii_stream():
    """A text stream like a terminal that cannot display Unicode."""
    return io.TextIOWrapper(io.BytesIO(), encoding="ascii")


def test_unicode_url_is_printed_as_ascii_on_a_terminal_that_needs_it(run, factory):
    _, short, _ = run("--minify=https://bücher.de/straße")
    out = ascii_stream()
    code = cli.main(
        [f"--expand={short.strip()}"],
        service_factory=factory,
        env={},
        stdout=out,
    )
    out.seek(0)
    assert code == cli.ExitCode.OK
    assert out.read() == "https://xn--bcher-kva.de/stra%C3%9Fe\n"


def test_unicode_in_error_message_does_not_crash_an_ascii_terminal(factory):
    err = ascii_stream()
    code = cli.main(
        ["--expand=https://bücher.de/abcd123"],
        service_factory=factory,
        env={},
        stderr=err,
    )
    err.seek(0)
    assert code == cli.ExitCode.USAGE
    assert err.read().startswith("error: https://b\\xfccher.de/abcd123 is not a short URL")


def test_verbose_logs_go_to_stderr_and_stdout_stays_the_url(run):
    code, out, err = run("-v", f"--minify={URL}")
    assert code == cli.ExitCode.OK
    assert out.startswith("https://myurlshortener.com/")
    assert out.count("\n") == 1
    assert "INFO url_shortener.service: Created code" in err
    assert "DEBUG" not in err


def test_very_verbose_logs_debug_detail(run):
    run(f"--minify={URL}")
    code, _, err = run("-vv", f"--minify={URL}")
    assert code == cli.ExitCode.OK
    assert "DEBUG url_shortener.service: Reusing code" in err


def test_logging_is_undone_after_each_run(run):
    package_logger = logging.getLogger("url_shortener")
    run("-vv", "--expand=https://myurlshortener.com/fstp4")
    assert package_logger.handlers == []
    assert package_logger.level == logging.NOTSET


def test_unicode_in_log_records_does_not_crash_an_ascii_terminal(factory):
    err = ascii_stream()
    code = cli.main(
        ["-v", "--minify=https://bücher.de/straße"],
        service_factory=factory,
        env={},
        stdout=io.StringIO(),
        stderr=err,
    )
    err.seek(0)
    assert code == cli.ExitCode.OK
    assert "https://xn--bcher-kva.de/stra\\xdfe" in err.read()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"SHORTENER_MONGO_URI": "mongodb://"}, "SHORTENER_MONGO_URI is invalid"),
        ({"SHORTENER_MONGO_URI": "postgres://db"}, "SHORTENER_MONGO_URI is invalid"),
        ({"SHORTENER_MONGO_URI": "mongodb://db:99999"}, "SHORTENER_MONGO_URI is invalid"),
        ({"SHORTENER_MONGO_DATABASE": "my db"}, "SHORTENER_MONGO_DATABASE"),
    ],
)
def test_invalid_mongo_settings_are_configuration_errors(env, message):
    err = io.StringIO()
    code = cli.main([f"--minify={URL}"], env=env, stderr=err)
    assert code == cli.ExitCode.USAGE
    assert message in err.getvalue()


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://localhost/?authMechanism=SCRAM-SHA-256",  # requires a username
        "mongodb://a,b/?directConnection=true",  # one host only
    ],
)
def test_uri_options_the_driver_refuses_are_configuration_errors(uri):
    err = io.StringIO()
    code = cli.main([f"--minify={URL}"], env={"SHORTENER_MONGO_URI": uri}, stderr=err)
    assert code == cli.ExitCode.USAGE
    assert err.getvalue().startswith("error: SHORTENER_MONGO_URI is invalid: ")


@pytest.mark.filterwarnings("default::UserWarning")  # the code, not pytest, must escalate it
def test_uri_options_the_driver_would_ignore_are_configuration_errors():
    err = io.StringIO()
    env = {"SHORTENER_MONGO_URI": "mongodb://localhost/?readPreference=bogus"}
    assert cli.main([f"--minify={URL}"], env=env, stderr=err) == cli.ExitCode.USAGE
    assert err.getvalue().startswith("error: SHORTENER_MONGO_URI is invalid: bogus is not")
    assert "site-packages" not in err.getvalue()


def test_failed_srv_lookup_is_a_storage_error(monkeypatch):
    def no_dns(*args, **kwargs):
        raise pymongo.errors.ConfigurationError(
            "The DNS query name does not exist"
        ) from dns.resolver.NXDOMAIN()

    monkeypatch.setattr("url_shortener.repository.MongoClient", no_dns)
    err = io.StringIO()
    env = {"SHORTENER_MONGO_URI": "mongodb+srv://cluster.invalid"}
    assert cli.main([f"--minify={URL}"], env=env, stderr=err) == cli.ExitCode.STORAGE
    assert err.getvalue().startswith("error: MongoDB error: The DNS query name")


def test_connection_is_closed_even_when_the_command_fails(monkeypatch):
    closed = []
    real_close = MongoLinkRepository.close

    def close(self):
        closed.append(self)
        real_close(self)

    monkeypatch.setattr(MongoLinkRepository, "close", close)
    code = cli.main(["--minify=www.example.com"], env={}, stderr=io.StringIO())
    assert code == cli.ExitCode.USAGE
    assert len(closed) == 1


class ReadOnlyError(StorageError):
    pass


class UnexpectedError(ShortenerError):
    pass


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [(ReadOnlyError, cli.ExitCode.STORAGE), (UnexpectedError, cli.ExitCode.INTERNAL)],
)
def test_error_subclasses_get_their_parent_exit_code(error, exit_code):
    def factory(settings):
        raise error("boom")

    err = io.StringIO()
    assert cli.main([f"--minify={URL}"], service_factory=factory, env={}, stderr=err) == exit_code
    assert err.getvalue() == "error: boom\n"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [f"--minify={URL}", "--expand=https://myurlshortener.com/abcd"],
        ["--minify"],
        ["--shorten", URL],
    ],
)
def test_usage_errors(argv, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(argv, service_factory=lambda s: pytest.fail("must not build service"))
    assert info.value.code == cli.ExitCode.USAGE
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    installed = importlib.metadata.version("url-shortener")
    assert capsys.readouterr().out == f"url-shortener {installed}\n"


def test_runs_from_a_checkout_that_is_not_installed(monkeypatch, capsys, run):
    """The version lookup happens on every run, so a missing distribution must not break it."""

    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(cli, "version", not_installed)
    assert run(f"--minify={URL}")[0] == cli.ExitCode.OK
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out == "url-shortener unknown\n"


class ClosedPipe(io.StringIO):
    """stdout whose reader has gone away (``url-shortener ... | head -c1``)."""

    def write(self, text):
        raise BrokenPipeError


def test_reader_closing_stdout_early_is_not_an_error(factory):
    err = io.StringIO()
    code = cli.main(
        [f"--minify={URL}"], service_factory=factory, env={}, stdout=ClosedPipe(), stderr=err
    )
    assert code == cli.ExitCode.OK
    assert err.getvalue() == ""


def test_help_lists_every_exit_code():
    epilog = cli.build_parser().epilog
    for code in cli.ExitCode:
        assert re.search(rf"\b{code.value} \w", epilog), code
