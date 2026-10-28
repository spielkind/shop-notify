# syntax=docker/dockerfile:1

FROM node:24.22-trixie-slim@sha256:89c5244bc22a9bc3287d89ebc7e49b9cb5bb84cd40e6175ac3421d5a9f68b95a AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npx svelte-kit sync && npm run build


FROM node:24.22-trixie-slim@sha256:89c5244bc22a9bc3287d89ebc7e49b9cb5bb84cd40e6175ac3421d5a9f68b95a

# npm and corepack are not needed at runtime: the app is bundled and has no dependencies
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
        /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
    && mkdir /data && chown node:node /data \
    && printf '#!/bin/sh\nexec node /app/src/lib/server/cli.ts "$@"\n' > /usr/local/bin/shop-notify-ui \
    && chmod 755 /usr/local/bin/shop-notify-ui

WORKDIR /app
COPY --from=build /app/build ./build
COPY package.json ./
# For the shop-notify-ui command, which runs the TypeScript directly
COPY src/lib/server ./src/lib/server
COPY src/lib/i18n ./src/lib/i18n

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000 \
    UI_DATA_DIR=/data

USER 1000:1000
VOLUME /data
EXPOSE 3000

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]

CMD ["node", "build"]
