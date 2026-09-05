# syntax=docker/dockerfile:1.27

FROM alpine:3.24

RUN apk add --no-cache \
  weasyprint \
  py3-gunicorn \
  font-noto \
  font-noto-cjk \
  && adduser -S -D -H -u 65532 -G root html2pdf

# Keep runtime settings after dependency installation so version and config
# changes preserve the cached package-installation layer.
ARG VERSION=dev
ENV \
  HTML2PDF__VERSION=$VERSION \
  HTML2PDF__LISTEN_ADDRESS=0.0.0.0:8080 \
  HTML2PDF__WORKERS=2 \
  HTML2PDF__TIMEOUT=45 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  HOME=/tmp \
  XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app

COPY app.py index.html logo.svg /app/

USER 65532:0

EXPOSE 8080

ENTRYPOINT ["sh", "-c"]
CMD ["exec gunicorn --bind=\"$HTML2PDF__LISTEN_ADDRESS\" --workers=\"$HTML2PDF__WORKERS\" --timeout=\"$HTML2PDF__TIMEOUT\" --graceful-timeout=10 --worker-tmp-dir=/tmp --no-control-socket --access-logfile=- --error-logfile=- app:application"]
