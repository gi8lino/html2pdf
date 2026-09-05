# syntax=docker/dockerfile:1.27

FROM alpine:3.24

ARG VERSION=dev

RUN apk add --no-cache \
  weasyprint \
  py3-gunicorn \
  font-noto \
  font-noto-cjk \
  && adduser -S -D -H -u 65532 -G root html2pdf

WORKDIR /app

COPY app.py index.html logo.svg /app/

ENV HTML2PDF_VERSION=$VERSION \
  HTML2PDF_WORKERS=2 \
  HTML2PDF_TIMEOUT=45 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  HOME=/tmp \
  XDG_CACHE_HOME=/tmp/.cache

USER 65532:0

EXPOSE 8080

ENTRYPOINT ["sh", "-c"]
CMD ["exec gunicorn --bind=0.0.0.0:8080 --workers=\"$HTML2PDF_WORKERS\" --timeout=\"$HTML2PDF_TIMEOUT\" --graceful-timeout=10 --worker-tmp-dir=/tmp --no-control-socket --access-logfile=- --error-logfile=- app:application"]
