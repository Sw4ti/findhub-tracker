# Development/deployment candidate: rebuild only the pinned upstream collector
# plus the reviewed deletion patch. No production image is built or started here.
FROM alpine/git:2.45.2 AS source
ARG COLLECTOR_COMMIT=8ad438190beea726228b52bc35d732ff25e7dc91
RUN git clone --filter=blob:none https://github.com/P6g9YHK6/GoogleFindMyToolsWebUi.git /src \
    && git -C /src checkout --detach "${COLLECTOR_COMMIT}"
COPY deploy/collector-device-deletion.patch /tmp/collector-device-deletion.patch
RUN git -C /src apply --check /tmp/collector-device-deletion.patch \
    && git -C /src apply /tmp/collector-device-deletion.patch

FROM node:20-slim AS assets
WORKDIR /app
COPY --from=source /src/package.json /src/package-lock.json ./
RUN npm ci
COPY --from=source /src/webui/static ./webui/static
COPY --from=source /src/scripts/copy-vendor.mjs ./scripts/copy-vendor.mjs
RUN npm run build

FROM python:3.11-slim AS collector-runtime
WORKDIR /app
COPY --from=source /src/requirements.lock /app/
RUN pip install --no-cache-dir -r requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates procps tzdata git libusb-1.0-0 \
        cmake ninja-build ccache dfu-util libffi-dev libssl-dev wget flex bison gperf \
    && rm -rf /var/lib/apt/lists/*
COPY --from=source /src /app
COPY --from=assets /app/webui/static/dist /app/webui/static/dist
COPY --from=assets /app/webui/static/vendor /app/webui/static/vendor
ARG GFMT_BUILD_SHA=8ad438190beea726228b52bc35d732ff25e7dc91
ARG GFMT_BUILD_DATE=
ARG GFMT_BUILD_BRANCH=findhub-device-deletion
RUN printf '%s\n%s\n%s\n' "${GFMT_BUILD_SHA}" "${GFMT_BUILD_DATE}" "${GFMT_BUILD_BRANCH}" > /app/.build_info
ENV DISPLAY=:99 \
    GFMT_BROWSER_RUNTIME_DIR=/run/gfmt-browser \
    GFMT_DATA_DIR=/data \
    GFMT_FIRMWARE_DIR=/firmware \
    GFMT_NONINTERACTIVE=1 \
    GFMT_BUILD_SHA=${GFMT_BUILD_SHA} \
    GFMT_BUILD_DATE=${GFMT_BUILD_DATE} \
    GFMT_BUILD_BRANCH=${GFMT_BUILD_BRANCH}
EXPOSE 4321
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "docker/web/healthcheck.py"]
CMD ["python3", "-m", "webui.serve"]

# Test-only target for isolated development verification. The default final
# target remains collector-production and does not install pytest packages.
FROM collector-runtime AS collector-test
COPY deploy/collector-test-sitecustomize.py /usr/local/lib/python3.11/site-packages/sitecustomize.py
RUN pip install --no-cache-dir 'pytest>=9.2.0' 'pytest-asyncio>=1.4.1'

FROM collector-runtime AS collector-production
