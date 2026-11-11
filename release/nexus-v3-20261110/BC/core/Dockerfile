ARG BASE_IMAGE=nexus-core:active-router-release-ba9a175
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG BASE_IMAGE_ID=sha256:16240edf79ab7e262048a5b70e12e58bbe88edcd70dda203eac4dfce8d987d70
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.nexus.base-image-id="${BASE_IMAGE_ID}" \
      io.nexus.release-source="Nexus V3 2026-11-10 overlay plus Active Router"
WORKDIR /app
COPY core/app/ ./app/
COPY core/static/ ./static/
