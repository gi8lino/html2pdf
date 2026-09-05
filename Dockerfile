# syntax=docker/dockerfile:1.27

FROM alpine:3.24

RUN apk add --no-cache weasyprint py3-gunicorn font-noto font-noto-cjk \
  && adduser -S -D -H -u 65532 -G root html2pdf

WORKDIR /app
COPY app.py index.html /app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 65532:0
EXPOSE 8080

# Sync workers bound concurrent renders; timeout kills and replaces a stuck worker.
ENTRYPOINT ["gunicorn"]
CMD ["--bind=0.0.0.0:8080", "--workers=2", "--timeout=45", "--graceful-timeout=10", "--worker-tmp-dir=/tmp", "--access-logfile=-", "--error-logfile=-", "app:application"]
