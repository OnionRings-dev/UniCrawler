FROM golang:1.22-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/mapper .

FROM alpine:3.20

RUN adduser -D -H mapper
USER mapper
COPY --from=build /out/mapper /mapper

ENTRYPOINT ["/mapper"]
