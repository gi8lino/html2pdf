"""Small WSGI service that renders self-contained UTF-8 HTML to PDF."""

import hmac
import html
import logging
import os
import time
from http import HTTPStatus
from pathlib import Path

from weasyprint import HTML, default_url_fetcher

MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
AUTH_TOKEN = os.environ.get("HTML2PDF_TOKEN", "").strip()
VERSION = os.environ.get("HTML2PDF_VERSION", "dev").strip() or "dev"
INDEX_HTML = (
    Path(__file__)
    .with_name("index.html")
    .read_text(encoding="utf-8")
    .replace("{{VERSION}}", html.escape(VERSION))
    .encode("utf-8")
)
LOGO_SVG = Path(__file__).with_name("logo.svg").read_bytes()
INDEX_HEADERS = (
    (
        "Content-Security-Policy",
        "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'",
    ),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
)
logger = logging.getLogger("gunicorn.error")


class AssetError(ValueError):
    """A document references an asset outside its submitted data URLs."""


def render_document(source):
    """Render one self-contained HTML document and return its PDF bytes."""
    rejected = []

    def fetch_asset(url, *args, **kwargs):
        # Never give submitted HTML access to container files, cloud metadata,
        # private services, or the public internet. Callers must embed assets.
        if not url.startswith("data:"):
            rejected.append(True)
            raise AssetError("Assets must be embedded as data URLs")
        return default_url_fetcher(url, *args, **kwargs)

    result = HTML(string=source, url_fetcher=fetch_asset).write_pdf()

    # WeasyPrint can log asset failures and continue. Turn rejected assets into
    # a hard error so callers never receive a silently incomplete document.
    if rejected:
        raise AssetError("Assets must be embedded as data URLs")
    return result


def bearer_token(environ):
    """Return the submitted bearer token, or an empty string when absent."""
    authorization = environ.get("HTTP_AUTHORIZATION", "").strip()
    if not authorization:
        return ""

    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def authorized(environ):
    """Report whether the request may use the render endpoint."""
    if not AUTH_TOKEN:
        return True
    token = bearer_token(environ)
    return bool(token) and hmac.compare_digest(token, AUTH_TOKEN)


def application(environ, start_response):
    """Serve the index, logo, health check, and HTML-to-PDF render endpoint."""

    def respond(
        status,
        body,
        content_type="text/plain; charset=utf-8",
        extra=(),
    ):
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                *extra,
            ],
        )
        return [body]

    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "")

    if path == "/" and method == "GET":
        return respond(
            HTTPStatus.OK,
            INDEX_HTML,
            "text/html; charset=utf-8",
            extra=INDEX_HEADERS,
        )

    if path == "/logo.svg" and method == "GET":
        return respond(
            HTTPStatus.OK,
            LOGO_SVG,
            "image/svg+xml",
        )

    if path == "/healthz" and method == "GET":
        return respond(HTTPStatus.OK, b"ok\n")

    if path != "/render":
        return respond(HTTPStatus.NOT_FOUND, b"Not found\n")

    if method != "POST":
        return respond(
            HTTPStatus.METHOD_NOT_ALLOWED,
            b"Use POST\n",
            extra=[("Allow", "POST")],
        )

    if not authorized(environ):
        return respond(
            HTTPStatus.UNAUTHORIZED,
            b"Invalid or missing bearer token\n",
            extra=[("WWW-Authenticate", "Bearer")],
        )

    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[
        0].strip().lower()
    if content_type != "text/html":
        return respond(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            b"Send text/html encoded as UTF-8\n",
        )

    if not environ.get("CONTENT_LENGTH"):
        return respond(
            HTTPStatus.LENGTH_REQUIRED,
            b"Content-Length is required\n",
        )

    try:
        length = int(environ["CONTENT_LENGTH"])
    except ValueError:
        return respond(
            HTTPStatus.BAD_REQUEST,
            b"Invalid Content-Length\n",
        )

    if length <= 0:
        return respond(
            HTTPStatus.BAD_REQUEST,
            b"HTML is required\n",
        )

    if length > MAX_HTML_BYTES:
        return respond(
            HTTPStatus.CONTENT_TOO_LARGE,
            b"HTML exceeds 32 MiB\n",
        )

    body = environ["wsgi.input"].read(length)
    if len(body) != length:
        return respond(
            HTTPStatus.BAD_REQUEST,
            b"Incomplete request body\n",
        )

    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        return respond(
            HTTPStatus.BAD_REQUEST,
            b"HTML must be UTF-8\n",
        )

    started = time.perf_counter()

    try:
        result = render_document(source)
    except AssetError:
        logger.warning(
            "render rejected duration_ms=%d html_bytes=%d reason=asset_policy",
            round((time.perf_counter() - started) * 1000),
            length,
        )
        return respond(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            b"Embed images, fonts, and other assets as data URLs; external assets are not fetched\n",
        )
    except Exception:
        logger.exception(
            "render failed duration_ms=%d html_bytes=%d reason=internal_error",
            round((time.perf_counter() - started) * 1000),
            length,
        )
        return respond(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            b"PDF rendering failed\n",
        )

    pdf_bytes = len(result)
    if pdf_bytes > MAX_PDF_BYTES:
        logger.warning(
            "render rejected duration_ms=%d html_bytes=%d pdf_bytes=%d reason=pdf_too_large",
            round((time.perf_counter() - started) * 1000),
            length,
            pdf_bytes,
        )
        return respond(
            HTTPStatus.CONTENT_TOO_LARGE,
            b"PDF exceeds 64 MiB\n",
        )

    logger.info(
        "render completed duration_ms=%d html_bytes=%d pdf_bytes=%d",
        round((time.perf_counter() - started) * 1000),
        length,
        pdf_bytes,
    )

    return respond(
        HTTPStatus.OK,
        result,
        "application/pdf",
    )
