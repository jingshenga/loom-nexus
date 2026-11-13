ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG BASE_IMAGE_ID
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.nexus.base-image-id="${BASE_IMAGE_ID}" \
      io.nexus.release-source="Nexus V3 unified core/app and core/frontend"
WORKDIR /app
COPY core/app/ ./app/
COPY core/frontend/dist/ ./static/
