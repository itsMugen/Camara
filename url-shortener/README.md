# URL Shortener

A command line tool that shortens URLs and expands them again. Links are
stored in MongoDB and expire after a configurable number of seconds; a URL
that still has a valid link gets the same short URL back.

```console
$ url-shortener --minify='https://www.example.com/path?q=search'
https://myurlshortener.com/Ab3dE9x
$ url-shortener --expand=https://myurlshortener.com/Ab3dE9x
https://www.example.com/path?q=search
```

Needs Docker (for MongoDB) and Python 3.11+ with [uv](https://docs.astral.sh/uv/)
or [pipx](https://pipx.pypa.io/). From this directory:

```bash
./install.sh      # docker compose up -d --wait mongo, then uv tool install . (or pipx)
make test         # 261 unit tests, no database; make test-all adds 16 against MongoDB
```

The problem has been solved with the working assumption that the main goal was to have a small cli for convenience sake,
if I had to design this as an internal tool I would have developed it more as a thin cli wrapper around a docker service that handled calls over HTTP, improving latency (no startup cost on each call) and giving a straight forward path to shortened paths exposure to other collaborators/external usage (HTTPS in that case).

## Configuration

Every setting is an environment variable with the `SHORTENER_` prefix. An unset
or blank variable means the default. A value out of range is reported with the
variable's name and exit code 2 before anything is stored. `.env.example` lists
them in a form you can `export`.

| Variable | Default | Meaning |
|---|---|---|
| `SHORTENER_MONGO_URI` | `mongodb://localhost:27017` | Connection string. Options the driver does not understand are an error, not a warning. |
| `SHORTENER_MONGO_DATABASE` | `url_shortener` | Database name, at most 63 bytes, without `/\. "$*<>:\|?`. |
| `SHORTENER_BASE_URL` | `https://myurlshortener.com` | Prefix of the short URLs. `http` or `https`, optionally a port and a path; no credentials, `?` or `#`. At most 2015 characters, so that every short URL fits in 2048. |
| `SHORTENER_TTL_SECONDS` | `3600` | How long a short URL stays valid after it is created (1 to 2³¹−1). |
| `SHORTENER_CODE_LENGTH` | `7` | Characters in a short code, from `[0-9a-zA-Z]` (4 to 32). Changing it does not invalidate existing codes. |
| `SHORTENER_EXPIRED_RETENTION_SECONDS` | `86400` | How long MongoDB keeps an expired link, so that `--expand` can say "expired" rather than "not found" (0 to 2³¹−1). |
| `SHORTENER_MONGO_TIMEOUT_MS` | `3000` | How long to wait for MongoDB before giving up (1 to 999999999). |

## Exit codes

stdout carries only the resulting URL, and nothing on failure, so
`url=$(url-shortener --expand ...) && curl -L "$url"` is safe. Errors go to
stderr, prefixed with `error:`.

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | The short URL is not known, or has expired (the message says which, and when). |
| 2 | Bad arguments, an invalid URL, or an invalid configuration variable. |
| 3 | MongoDB is unreachable or rejected the operation. |
| 4 | Internal error: no free short code after several attempts. |
| 130 | Interrupted with Ctrl-C. |

`-v` logs what the tool does to stderr, `-vv` adds detail (including the
driver's full error text when MongoDB is unreachable).

## Known limits

Deliberate choices that can look like bugs:

- Two spellings of a URL get the same short URL only when they *must* name the
  same resource: the scheme and host are case-folded, default ports dropped,
  international host names compared in punycode, IP literals in canonical form.
  Paths, query strings and fragments are kept byte for byte, so
  `/a` and `/a/`, `/é` and `/%C3%A9`, `?a=1&b=2` and `?b=2&a=1`, and
  `example.com` and `example.com.` get different short URLs. A bare trailing
  `?` or `#` is ignored.
- Host names that end in a number must be a plain IPv4 address: `127.1`,
  `0x7f.0.0.1` and `999.999.999.999` are refused rather than guessed at.
- With a base URL that has no path, every URL on that host is refused for
  shortening, including its homepage.
- A URL minified again while its link is valid gets the same short URL and the
  same expiry, even 1 ms before it expires; the lifetime is never extended.
- Minifying a URL whose link has expired issues a new code and retires the old
  one, which then reports "not found" rather than "expired".
- Expiry is decided by the clock of the machine running the command. Machines
  with skewed clocks disagree about which links are still valid.
- The IDNA 2008 behaviour of browsers is emulated only for `ß` and `ς` (the
  characters IDNA 2003 would map away); `xn--` labels are not validated.
