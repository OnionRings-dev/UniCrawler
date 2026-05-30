# UniCrawler Mapper

Redis-driven site mapper with Postgres-backed durable storage. It blocks on an input queue, takes a seed URL, crawls links that belong to the same domain, de-duplicates discovered pages with a Redis set, writes the generated sitemap to Postgres, and pushes discovered same-domain URLs to the output queue for the next node.

## Queues

- Input: `mapper:in`
- Output: `mapper:out`

## Queue Data

Both queues currently contain plain Redis list items where each item is a single URL string.

Input item example:

```text
https://example.com/
```

The mapper accepts absolute `http` and `https` URLs. If a seed URL has no scheme, `https` is assumed. Fragment identifiers are discarded during normalization.

Output item example:

```text
https://example.com/products?page=2
```

Output URLs are normalized, same-domain links discovered from `<a href="...">` and `<area href="...">`, and are pushed only the first time they are seen for that mapped domain. The mapper keeps its internal de-duplication state in Redis sets named with the `VISITED_PREFIX` value, default `mapper:visited:<domain-hash>`.

## Postgres Storage

The durable sitemap is stored in Postgres:

- `domains`: one row per mapped domain.
- `crawl_runs`: one row per crawl execution, with seed URL, status, counts, and timestamps.
- `urls`: one row per normalized URL per domain.

The uniqueness rule is `(domain_id, url_hash)`, so the same URL is stored once for a domain and updated with `last_seen_at` on later crawls.

Seed manually:

```sh
docker compose exec redis redis-cli LPUSH mapper:in https://example.com/
```

Read output:

```sh
docker compose exec redis redis-cli BRPOP mapper:out 0
```

Replay a stored sitemap into Redis without crawling again:

```sh
docker compose run --rm mapper replay example.com mapper:out
```

The first argument is the stored domain key. With the default `SAME_DOMAIN_MODE=registrable`, use values like `example.com`; with `SAME_DOMAIN_MODE=host`, use values like `www.example.com`. Replay pushes every stored URL for that domain, including the original seed URL, to the selected Redis queue.

Redis visited sets are runtime de-duplication state. If you intentionally want to remap a domain from scratch instead of replaying the Postgres sitemap, delete its `mapper:visited:<domain-hash>` key before reseeding.

## Configuration

Environment variables:

- `REDIS_ADDR`, default `redis:6379`
- `POSTGRES_DSN`, default `postgres://unicrawler:unicrawler@postgres:5432/unicrawler?sslmode=disable`
- `INPUT_QUEUE`, default `mapper:in`
- `OUTPUT_QUEUE`, default `mapper:out`
- `WORKERS`, default `128`
- `REDIS_POOL_SIZE`, default `WORKERS * 2`
- `SAME_DOMAIN_MODE`, default `registrable`; use `host` to stay on the exact hostname
- `MAX_PAGES_PER_SEED`, default `0` for unlimited
- `REQUEST_TIMEOUT`, default `15s`
- `REDIS_BATCH_SIZE`, default `1000`
