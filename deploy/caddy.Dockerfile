# Caddy with the marketing site baked in. Build context: repository root.

FROM node:22-alpine AS site
WORKDIR /src
# The site reads the language list from the server config at build time.
COPY server/config/languages.json server/config/languages.json
COPY site/package.json site/package-lock.json site/
RUN cd site && npm ci --no-audit --no-fund
COPY site/ site/
RUN cd site && npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=site /src/site/dist /srv/site
