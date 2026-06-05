FROM golang:1.25-alpine AS build

WORKDIR /src
COPY packages/go/shared ./packages/go/shared
COPY packages/go/dbgen ./packages/go/dbgen
COPY services/parser/go.mod services/parser/go.sum ./services/parser/
RUN cd services/parser && GOWORK=off go mod download
COPY services/parser ./services/parser
RUN cd services/parser && GOWORK=off CGO_ENABLED=0 GOOS=linux go build -o /out/parser .

FROM alpine:3.20

RUN apk add --no-cache chromium ca-certificates

ENV CHROME_PATH=/usr/bin/chromium-browser
WORKDIR /app
COPY --from=build /out/parser /app/parser

EXPOSE 8081

ENTRYPOINT ["/app/parser"]
