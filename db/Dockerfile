FROM golang:1.25-alpine AS goose

RUN apk add --no-cache git ca-certificates postgresql-client
RUN go install github.com/pressly/goose/v3/cmd/goose@v3.25.0

FROM alpine:3.20

RUN apk add --no-cache ca-certificates postgresql-client
COPY --from=goose /go/bin/goose /usr/local/bin/goose
WORKDIR /db
COPY migrations ./migrations
COPY migrate.sh ./migrate.sh
RUN chmod +x ./migrate.sh

ENTRYPOINT ["/db/migrate.sh"]
