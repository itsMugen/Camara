import pytest

from url_shortener.errors import InvalidUrlError
from url_shortener.urls import MAX_URL_LENGTH, parse_long_url, parse_short_url, to_ascii_uri

BASE = "https://myurlshortener.com"


class TestParseLongUrl:
    def test_keeps_original_and_strips_surrounding_whitespace(self):
        parsed = parse_long_url("  https://www.example.com/path?q=search \n")
        assert parsed.original == "https://www.example.com/path?q=search"
        assert parsed.key == "https://www.example.com/path?q=search"

    @pytest.mark.parametrize("junk", ["\ufeff", "\u200b", "\u2060", "\u00a0", "\t"])
    def test_strips_invisible_characters_copied_along_with_the_url(self, junk):
        parsed = parse_long_url(f"{junk}https://example.com/a{junk}")
        assert parsed.original == "https://example.com/a"

    @pytest.mark.parametrize(
        ("variant", "canonical"),
        [
            ("HTTPS://WWW.Example.COM/path", "https://www.example.com/path"),
            ("https://www.example.com:443/path", "https://www.example.com/path"),
            ("http://www.example.com:80/path", "http://www.example.com/path"),
            ("https://www.example.com", "https://www.example.com/"),
            ("https://www.example.com?", "https://www.example.com/"),
            ("https://bücher.de/x", "https://xn--bcher-kva.de/x"),
            ("http://[2001:DB8::1]:8080/a", "http://[2001:db8::1]:8080/a"),
            ("http://[2001:0db8:0000::0001]/", "http://[2001:db8::1]/"),
            ("http://[fe80::1%25eth0]/", "http://[fe80::1%25eth0]/"),  # zone id is kept
            ("https://BÜCHER.de/", "https://xn--bcher-kva.de/"),
            (
                "https://\uff45\uff58\uff41\uff4d\uff50\uff4c\uff45.com/",
                "https://example.com/",
            ),  # fullwidth, as browsers do
            ("https://bücher\u3002de/", "https://xn--bcher-kva.de/"),  # ideographic full stop
            ("https://faß.de/", "https://xn--fa-hia.de/"),  # IDNA 2008, as browsers do
            ("https://example.com:/x", "https://example.com/x"),  # empty port
            ("https://exa_mple.com/", "https://exa_mple.com/"),
            ("https://1.2.3.4/x", "https://1.2.3.4/x"),
            ("https://1.2.3.4./x", "https://1.2.3.4/x"),  # an address has no root label
            ("https://\uff46aß.de/", "https://xn--fa-hia.de/"),  # fullwidth f, IDNA 2008 label
        ],
    )
    def test_equivalent_forms_share_a_key(self, variant, canonical):
        assert parse_long_url(variant).key == canonical

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("https://example.com/Path", "https://example.com/path"),  # paths are case-sensitive
            ("https://example.com/?a=1&b=2", "https://example.com/?b=2&a=1"),  # order may matter
            ("https://example.com/#a", "https://example.com/#b"),  # SPA routes live in fragments
            ("http://example.com/", "https://example.com/"),  # different scheme, different resource
            ("https://example.com:8443/", "https://example.com/"),  # non-default port is kept
            ("https://example.com/a/", "https://example.com/a"),
            ("https://faß.de/", "https://fass.de/"),  # two different domains
            ("https://example.com./", "https://example.com/"),  # servers may not treat as one
            ("https://example.com/%7E", "https://example.com/~"),
        ],
    )
    def test_meaningful_differences_produce_different_keys(self, first, second):
        assert parse_long_url(first).key != parse_long_url(second).key

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("", "must not be empty"),
            ("   ", "must not be empty"),
            ("www.example.com", "must start with http:// or https://"),
            ("example.com/path", "must start with http:// or https://"),
            ("ftp://example.com/file", "must start with http:// or https://"),
            ("javascript:alert(1)", "must start with http:// or https://"),
            ("data:text/html,<script>alert(1)</script>", "must start with http:// or https://"),
            ("file:///etc/passwd", "must start with http:// or https://"),
            ("https://", "has no host"),
            ("https:///path", "has no host"),
            ("https://exa mple.com", "whitespace or control characters"),
            ("https://example.com/a b", "whitespace or control characters"),
            ("https://example.com/\x00", "whitespace or control characters"),
            ("https://exam\u200bple.com/", "whitespace or control characters"),
            ("https://exam\u200cple.com/", "whitespace or control characters"),  # ZWNJ
            ("https://example.com/a\u00adb", "whitespace or control characters"),
            ("https://user:secret@example.com/", "embedded credentials"),
            ("https://token@example.com/", "embedded credentials"),
            ("https://example.com:99999/", "Invalid port"),
            ("https://example.com:abc/", "Invalid port"),
            ("http://[::1/", "malformed"),
            ("https://example..com/", "Invalid host"),
            ("https://.example.com/", "Invalid host"),
            ("https://exa<mple>.com/", "Invalid host"),
            ("https://ex%41mple.com/", "Invalid host"),
            ("https://example.com!/", "Invalid host"),
            # A host ending in a number must be an IPv4 address, in dotted-quad form.
            ("https://999.999.999.999/", "Invalid host"),
            ("https://1.2.3.4.5/", "Invalid host"),
            ("https://127.1/", "Invalid host"),
            ("https://0x7f.0.0.1/", "Invalid host"),
            ("https://0177.0.0.1/", "Invalid host"),
            ("https://example.123/", "Invalid host"),
            ("https://" + "a" * 64 + ".com/", "Invalid host"),
            ("https://" + "ü" * 60 + ".com/", "Invalid host"),
            ("https://" + "ß" * 60 + ".com/", "Invalid host"),
            ("https://example.com:0/", "Invalid port"),
            ("https://example.com:+443/", "Invalid port"),
            ("https://[v1.fe]/", "Invalid IPv6"),
            ("https://[example.com]/", "malformed"),
            ("//example.com/", "must start with http"),
            ("https:example.com", "has no host"),
        ],
    )
    def test_rejects_invalid_urls(self, raw, message):
        with pytest.raises(InvalidUrlError, match=message):
            parse_long_url(raw)

    def test_rejects_urls_longer_than_the_limit(self):
        url = "https://example.com/" + "a" * MAX_URL_LENGTH
        with pytest.raises(InvalidUrlError, match="maximum"):
            parse_long_url(url)

    def test_accepts_url_exactly_at_the_limit(self):
        prefix = "https://example.com/"
        url = prefix + "a" * (MAX_URL_LENGTH - len(prefix))
        assert parse_long_url(url).original == url

    @pytest.mark.parametrize(
        "raw",
        [
            "https://myurlshortener.com/abc1234",
            "http://MyUrlShortener.com/x",
            "https://myurlshortener.com./abc1234",  # fully qualified spelling
            "https://myurlshortener.com:443/abc1234",
        ],
    )
    def test_refuses_to_shorten_its_own_short_urls(self, raw):
        with pytest.raises(InvalidUrlError, match="already a short URL"):
            parse_long_url(raw, base_url=BASE)

    def test_refuses_own_short_urls_on_an_internationalised_host(self):
        with pytest.raises(InvalidUrlError, match="already a short URL"):
            parse_long_url("https://xn--bcher-kva.de/abc1234", base_url="https://bücher.de")

    @pytest.mark.parametrize(
        ("raw", "base_url"),
        [
            ("https://docs.myurlshortener.com/", BASE),  # subdomain
            ("https://example.com/about", "https://example.com/s"),  # outside the path prefix
            ("https://example.com/s", "https://example.com/s"),
            ("http://localhost:3000/app", "http://localhost:8080"),  # another port
        ],
    )
    def test_other_urls_on_the_short_host_are_allowed(self, raw, base_url):
        assert parse_long_url(raw, base_url=base_url).original == raw


class TestParseShortUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://myurlshortener.com/fstp4",
            "http://myurlshortener.com/fstp4",  # scheme is not significant
            "HTTPS://MYURLSHORTENER.COM/fstp4",
            "https://myurlshortener.com/fstp4/",
            "https://myurlshortener.com:443/fstp4",
            "https://myurlshortener.com/fstp4?utm_source=chat",
            "https://myurlshortener.com/fstp4#top",
            "  https://myurlshortener.com/fstp4  ",
            "\ufeffhttps://myurlshortener.com/fstp4\u200b",
            "https://myurlshortener.com./fstp4",
        ],
    )
    def test_extracts_code(self, raw):
        assert parse_short_url(raw, base_url=BASE) == "fstp4"

    def test_code_is_case_sensitive(self):
        assert parse_short_url("https://myurlshortener.com/FsTp4", base_url=BASE) == "FsTp4"

    def test_supports_base_url_with_path_prefix(self):
        base = "https://example.com/s"
        assert parse_short_url("https://example.com/s/abcd123", base_url=base) == "abcd123"
        with pytest.raises(InvalidUrlError, match="not a short URL"):
            parse_short_url("https://example.com/abcd123", base_url=base)

    def test_supports_internationalised_base_url(self):
        base = "https://bücher.de"
        assert parse_short_url("https://xn--bcher-kva.de/abcd123", base_url=base) == "abcd123"
        assert parse_short_url("https://BÜCHER.de/abcd123", base_url=base) == "abcd123"

    def test_supports_base_url_with_port(self):
        base = "http://localhost:8080"
        assert parse_short_url("http://localhost:8080/abcd123", base_url=base) == "abcd123"
        with pytest.raises(InvalidUrlError, match="not a short URL"):
            parse_short_url("http://localhost/abcd123", base_url=base)

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("", "must not be empty"),
            ("fstp4", "must start with http"),
            ("https://example.com/fstp4", "not a short URL"),
            ("https://evil-myurlshortener.com/fstp4", "not a short URL"),
            ("https://myurlshortener.com.evil.com/fstp4", "not a short URL"),
            ("https://myurlshortener.com:8443/fstp4", "not a short URL"),
            ("https://myurlshortener.com/", "valid short code"),
            ("https://myurlshortener.com", "valid short code"),
            ("https://myurlshortener.com/fs\u200btp4", "whitespace or control"),
            ("https://myurlshortener.com/abc", "valid short code"),
            ("https://myurlshortener.com/fs-tp4", "valid short code"),
            ("https://myurlshortener.com/a/fstp4", "valid short code"),
            ("https://myurlshortener.com/fstp4//", "valid short code"),
            ("https://myurlshortener.com/" + "a" * 33, "valid short code"),
            ("https://myurlshortener.com/%66stp4", "valid short code"),
        ],
    )
    def test_rejects_invalid_short_urls(self, raw, message):
        with pytest.raises(InvalidUrlError, match=message):
            parse_short_url(raw, base_url=BASE)


@pytest.mark.parametrize(
    ("iri", "uri"),
    [
        ("https://example.com/path?q=1#top", "https://example.com/path?q=1#top"),
        ("https://bücher.de/straße?q=ü#ö", "https://xn--bcher-kva.de/stra%C3%9Fe?q=%C3%BC#%C3%B6"),
        ("https://example.com:8443/a%20b?x=%41", "https://example.com:8443/a%20b?x=%41"),
        ("http://[2001:db8::1]/日本", "http://[2001:db8::1]/%E6%97%A5%E6%9C%AC"),
    ],
)
def test_to_ascii_uri(iri, uri):
    assert to_ascii_uri(iri) == uri
    assert to_ascii_uri(iri).isascii()
