# Security policy

This is a gateway: it sits between your clients and a paid provider, holds the
provider credentials, and enforces per-caller limits. The interesting bugs are
therefore about one caller reaching something that belongs to another caller,
or to you.

## Threat model

Assume a caller holds a valid API key and is hostile. They can choose every
field of the request, send any volume, and observe latency, status codes,
headers, and the `cached` and `usage` fields of every response.

Assume also that the operator configures several keys for several tenants in
one process, because that is what the rate limiter and the cost accounting are
for.

## In scope

- **Auth bypass.** Reaching `POST /v1/chat/completions` without a configured
  key, or with a key that is not in `GATEWAY_API_KEYS`.
- **Rate-limit bypass.** Exceeding a key's token bucket, or spending another
  key's budget. The limiter is keyed on the authenticated key
  (`llmgateway/app.py`), so anything that decouples those is a finding.
- **Cross-caller cache access.** Reading, evicting, or poisoning another
  caller's cache entry. Cache keys are partitioned by a digest of the
  authenticated key (`llmgateway/cache.py`), and a way around that partition is
  a finding. So is a way to make the partition leak the key itself.
- **Provider credential disclosure.** Any path that puts a provider key or a
  gateway key into a response body, an error message, a log line, or the
  `/metrics` output.
- **Cost-accounting corruption.** Making one tenant's spend land on another, or
  making a request that consumes provider budget without being recorded.
- **Resource exhaustion** reachable from a single authenticated request:
  unbounded memory in the cache or the metrics store, or a request that makes
  the retry path loop.
- **SSRF or request smuggling** through model routing: getting the router to
  send a request somewhere the operator did not configure.

## Out of scope

- **The deterministic mock provider's outputs.** `mock*` models are fixtures.
  Their content is not a security property.
- **The browser demo** at the GitHub Pages URL. It runs the app client-side via
  Pyodide with a demo key, in the visitor's own tab, against no real provider.
  There is no server and nothing to reach.
- **Anything requiring the operator's own configuration to be hostile**, for
  example a `GATEWAY_DATABASE_URL` pointing somewhere harmful. The operator is
  trusted; callers are not.
- **The absence of TLS.** This app is meant to run behind a terminating proxy.

## Reporting

- **GitHub private advisory**, preferred:
  <https://github.com/egnaro9/llm-gateway/security/advisories/new>
- **Email**: erik@erikhill.dev

Include the commit, the config, and the smallest request sequence that shows
it. Acknowledgement within 3 days, a real answer within 14, credit by name in
the fix commit unless you would rather not.

Accepted findings get a regression test that is confirmed to fail against the
pre-fix code, so the hole stays closed rather than being fixed once.

## Known and fixed

- **Cross-tenant cache side channel**, found and fixed 2026-09-30 before any
  external report. `cache_key` hashed only `(model, messages, max_tokens)` while
  one `ResponseCache` served every configured key, so a second tenant sending a
  prompt a first tenant had already sent read the first tenant's entry. The
  body itself was not the leak, since at `temperature == 0` it is a
  deterministic function of the request the second tenant supplied; what leaked
  was that someone else had sent that exact prompt, which over a guessable
  prompt space is a usage oracle, and the provider cost of the entry, which the
  first tenant paid. Keys are now partitioned by a digest of the authenticated
  credential, with a regression test that fails when the partition is removed.

## Supported versions

`main`. There are no released versions to backport to yet.
