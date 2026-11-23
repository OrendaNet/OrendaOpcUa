FROM golang:1.26-bookworm AS native
WORKDIR /src/native
COPY native/go.mod native/go.sum ./
RUN go mod download
COPY native/ ./
WORKDIR /src
RUN cd native && go mod vendor
RUN git apply --no-index --directory=native/vendor/github.com/gopcua/opcua native/session.patch
RUN cd native && CGO_ENABLED=0 go build -mod=vendor -trimpath -ldflags="-s -w" -o /collector .

FROM node:22-bookworm-slim
LABEL org.opencontainers.image.source="https://github.com/OrendaNet/OrendaOpcUa"
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force && install -d -o node -g node -m 700 /data
COPY --from=native /collector /app/bin/collector
COPY server.js ./
COPY lib ./lib
COPY public ./public
USER node
EXPOSE 3101
CMD ["node", "server.js"]
