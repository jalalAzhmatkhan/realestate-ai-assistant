# Build context is ./frontend/admin (see docker-compose.yml).
#
# Two stages so the final image carries only the built static assets and nginx —
# not Node, not node_modules, not the Vite/TypeScript toolchain needed to produce
# the build in the first place.

# ---- builder: install deps and produce the static production build ----------
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked in at build time: Vite inlines `import.meta.env.*` into the bundle, there
# is no runtime env injection for a static SPA. Defaults to a same-origin relative
# path because the runtime stage's nginx proxies it straight to the `backend`
# service (see ./nginx.conf) — the bundle itself never needs to know the 'backend'
# hostname, which only resolves inside the Compose network, never inside the
# browser that actually executes this code.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---- runtime: nginx serving the static build + reverse-proxying the API -----
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 --start-period=5s \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
