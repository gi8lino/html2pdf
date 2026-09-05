"""Small WSGI service that renders self-contained UTF-8 HTML to PDF."""

import hmac
import logging
import os
from pathlib import Path

from weasyprint import HTML, default_url_fetcher

MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
AUTH_TOKEN = os.environ.get("HTML2PDF__TOKEN", "").strip()
INDEX_HTML = Path(__file__).with_name("index.html").read_bytes()
LOGO_SVG = Path(__file__).with_name("logo.svg").read_bytes()
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

    def respond(status, body, content_type="text/plain; charset=utf-8", extra=()):
        start_response(
            status,
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
        return respond("200 OK", INDEX_HTML, "text/html; charset=utf-8")

    if path == "/logo.svg" and method == "GET":
        return respond("200 OK", LOGO_SVG, "image/svg+xml")

    if path == "/healthz" and method == "GET":
        return respond("200 OK", b"ok\n")

    if path != "/render":
        return respond("404 Not Found", b"Not found\n")

    if method != "POST":
        return respond(
            "405 Method Not Allowed",
            b"Use POST\n",
            extra=[("Allow", "POST")],
        )

    if not authorized(environ):
        return respond(
            "401 Unauthorized",
            b"Invalid or missing bearer token\n",
            extra=[("WWW-Authenticate", "Bearer")],
        )

    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[
        0].strip().lower()
    if content_type != "text/html":
        return respond(
            "415 Unsupported Media Type",
            b"Send text/html encoded as UTF-8\n",
        )

    if not environ.get("CONTENT_LENGTH"):
        return respond("411 Length Required", b"Content-Length is required\n")

    try:
        length = int(environ["CONTENT_LENGTH"])
    except ValueError:
        return respond("400 Bad Request", b"Invalid Content-Length\n")

    if length <= 0:
        return respond("400 Bad Request", b"HTML is required\n")
    if length > MAX_HTML_BYTES:
        return respond("413 Content Too Large", b"HTML exceeds 32 MiB\n")

    body = environ["wsgi.input"].read(length)
    if len(body) != length:
        return respond("400 Bad Request", b"Incomplete request body\n")

    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        return respond("400 Bad Request", b"HTML must be UTF-8\n")

    try:
        result = render_document(source)
    except AssetError:
        return respond(
            "422 Unprocessable Content",
            b"Embed images, fonts, and other assets as data URLs; external assets are not fetched\n",
        )
    except Exception:
        logger.exception("PDF rendering failed")
        return respond("500 Internal Server Error", b"PDF rendering failed\n")

    if len(result) > MAX_PDF_BYTES:
        return respond("413 Content Too Large", b"PDF exceeds 64 MiB\n")

    return respond("200 OK", result, "application/pdf")
