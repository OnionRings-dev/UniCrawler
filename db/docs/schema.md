# UniCrawler Schema Notes

`db/migrations` is the authoritative schema history.

Do not add `CREATE TABLE` or `ALTER TABLE` statements to pipeline services. Add a migration instead, then add or update typed SQL in `db/queries` and regenerate `packages/go/dbgen`.
