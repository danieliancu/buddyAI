# Internal HTTP gateway for Coolify. Traefik terminates HTTPS.

FROM node:22-alpine AS site
WORKDIR /src
COPY server/config/languages.json server/config/languages.json
COPY site/package.json site/package-lock.json site/
RUN cd site && npm ci --no-audit --no-fund
COPY site/ site/
RUN cd site && npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile.coolify /etc/caddy/Caddyfile
COPY --from=site /src/site/dist /srv/site
