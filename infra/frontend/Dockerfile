# Build context is ./frontend/admin (see docker-compose.yml).
#
# Two stages, but neither one is the container that actually serves traffic — that's
# the standalone `nginx` service in docker-compose.yml, fronted by ../nginx/nginx.conf.
# This image's only job is to produce the static build and hand it off: the `builder`
# stage has the full Node toolchain to run Vite, and `export` strips that back down to
# just the built assets, so the image docker-compose actually runs each time never
# carries Node, npm, or node_modules.

# ---- builder: install deps and produce the static production build ----------
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked in at build time: Vite inlines `import.meta.env.*` into the bundle, there
# is no runtime env injection for a static SPA. Defaults to a same-origin relative
# path because the `nginx` service proxies it straight to the `backend` service
# (see ../nginx/nginx.conf) — the bundle itself never needs to know the 'backend'
# hostname, which only resolves inside the Compose network, never inside the
# browser that actually executes this code.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---- export: just the built assets, nothing else -----------------------------
FROM alpine:3.20 AS export

COPY --from=builder /app/dist /dist

# docker-compose runs this container once per `up`/`build` to populate the
# `frontend_dist` named volume that the `nginx` service mounts read-only; it exits
# immediately afterward rather than staying up (`depends_on: condition:
# service_completed_successfully` is what makes nginx wait for this to finish).
CMD ["sh", "-c", "cp -r /dist/. /shared/"]
