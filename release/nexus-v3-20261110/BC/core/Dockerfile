ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG BASE_IMAGE_ID=sha256:600e996adcfb0e1db463335ee57058545d325cfdfcbb38b82245a34c8cd207ce
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.nexus.base-image-id="${BASE_IMAGE_ID}" \
      io.nexus.release-source="Nexus V3 2026-11-10 overlay plus Active Router"
WORKDIR /app
COPY core/app/ ./app/
COPY core/static/ ./static/
