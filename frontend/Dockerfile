# Shiori web: the built React app, served by Caddy, which also proxies /api to the backend
# and obtains HTTPS certificates automatically when SITE_ADDRESS is a domain name.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
