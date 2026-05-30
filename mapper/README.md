# UniCrawler Mapper

Redis-driven site mapper. It blocks on an input queue, takes a seed URL, crawls links that belong to the same domain, de-duplicates discovered pages with a Redis set, and pushes discovered same-domain URLs to the output queue for the next node.

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

Seed manually:

```sh
docker compose exec redis redis-cli LPUSH mapper:in https://example.com/
```

Read output:

```sh
docker compose exec redis redis-cli BRPOP mapper:out 0
```

## Configuration

Environment variables:

- `REDIS_ADDR`, default `redis:6379`
- `INPUT_QUEUE`, default `mapper:in`
- `OUTPUT_QUEUE`, default `mapper:out`
- `WORKERS`, default `128`
- `REDIS_POOL_SIZE`, default `WORKERS * 2`
- `SAME_DOMAIN_MODE`, default `registrable`; use `host` to stay on the exact hostname
- `MAX_PAGES_PER_SEED`, default `0` for unlimited
- `REQUEST_TIMEOUT`, default `15s`
- `REDIS_BATCH_SIZE`, default `1000`
