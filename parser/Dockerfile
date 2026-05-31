FROM golang:1.23-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/parser .

FROM alpine:3.20

RUN apk add --no-cache chromium ca-certificates

ENV CHROME_PATH=/usr/bin/chromium-browser
WORKDIR /app
COPY --from=build /out/parser /app/parser

ENTRYPOINT ["/app/parser"]
