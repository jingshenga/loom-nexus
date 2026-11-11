ARG BASE_IMAGE=nexus-core:v3-lifecycle-router-shadow-20261110
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG BASE_IMAGE_ID=sha256:9a6b125ee195a46a7ebd3ab3deca13e108053b321c16f40001acbc33a90ceb7e
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.nexus.base-image-id="${BASE_IMAGE_ID}" \
      io.nexus.release-source="Nexus V3 2026-11-10 overlay plus Active Router"
WORKDIR /app
COPY core/app/ ./app/
COPY core/static/ ./static/
