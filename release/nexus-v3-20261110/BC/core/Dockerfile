ARG BASE_IMAGE
FROM node:20-alpine AS frontend
WORKDIR /build
COPY core/frontend/package.json core/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY core/frontend/ ./
RUN npm run build

FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG BASE_IMAGE_ID
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.nexus.base-image-id="${BASE_IMAGE_ID}" \
      io.nexus.release-source="Nexus V3 unified core/app and core/frontend"
WORKDIR /app
COPY core/app/ ./app/
COPY --from=frontend /build/dist/ ./static/
