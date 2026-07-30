# syntax=docker/dockerfile:1.9

ARG NODE_IMAGE=node:24.18.0-alpine3.24@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd

FROM ${NODE_IMAGE} AS dependencies
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY scripts ./scripts
RUN npm ci

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG BUILD_SHA
ENV BUILD_SHA=${BUILD_SHA}
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN test -n "${BUILD_SHA}" && npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ARG BUILD_SHA
ENV BUILD_SHA=${BUILD_SHA}
ENV HOSTNAME=0.0.0.0
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
ENV PORT=3000
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
LABEL org.opencontainers.image.source="https://github.com/loopguard/loopguard"
RUN test -n "${BUILD_SHA}"
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/health >/dev/null || exit 1
CMD ["node", "server.js"]
