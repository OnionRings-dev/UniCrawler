FROM golang:1.25-alpine AS build

WORKDIR /src
COPY packages/go/shared ./packages/go/shared
COPY packages/go/dbgen ./packages/go/dbgen
COPY services/mapper/go.mod services/mapper/go.sum ./services/mapper/
RUN cd services/mapper && GOWORK=off go mod download
COPY services/mapper ./services/mapper
RUN cd services/mapper && GOWORK=off CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/mapper .

FROM alpine:3.20

RUN adduser -D -H mapper
USER mapper
COPY --from=build /out/mapper /mapper

EXPOSE 8081

ENTRYPOINT ["/mapper"]
