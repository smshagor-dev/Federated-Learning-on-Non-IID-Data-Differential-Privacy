FROM node:22-alpine AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json* ./
RUN npm install

# --dns-result-order=ipv4first works around a well-documented Node/musl
# (Alpine) issue where Next.js's internal static-generation worker calls
# resolve `localhost` to ::1 first and stall before falling back to IPv4,
# making each prerendered page take 60s+ instead of milliseconds. Verified
# by comparing native `npm run build` (~13s) against this Dockerfile before
# the fix (every page timed out at the configured deadline, ~13s of actual
# compile work followed by a hang) — see docs/known-limitations.md.
FROM node:22-alpine AS builder
WORKDIR /app
ENV NODE_OPTIONS=--dns-result-order=ipv4first
COPY --from=deps /app/node_modules ./node_modules
COPY web ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app ./
EXPOSE 3000
CMD ["npm", "run", "start"]
