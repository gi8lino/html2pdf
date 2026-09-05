"""Small WSGI service that renders self-contained UTF-8 HTML to PDF."""

from __future__ import annotations
import hmac
import html
import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, BinaryIO, TypeAlias, cast

from weasyprint import HTML, default_url_fetcher

Environ: TypeAlias = Mapping[str, Any]
Header: TypeAlias = tuple[str, str]
Headers: TypeAlias = list[Header]
Response: TypeAlias = list[bytes]
StartResponse: TypeAlias = Callable[[str, Headers], Any]
Application: TypeAlias = Callable[[Environ, StartResponse], Response]

logger = logging.getLogger("gunicorn.error")

# Restrict the usage page to its local logo and inline CSS/JavaScript.
# Disable forms, base-URL overrides, framing, and referrer disclosure.
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


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration loaded from environment variables."""

    token: str
    version: str
    source_url: str
    workers: int
    timeout: int
    max_html_bytes: int
    max_pdf_bytes: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Config:
        """Load and validate runtime configuration from the supplied environment."""
        return cls(
            token=cls._env(environ, "HTML2PDF__TOKEN"),
            version=cls._env(environ, "HTML2PDF__VERSION", "dev") or "dev",
            source_url=cls._env(environ, "HTML2PDF__SOURCE_URL"),
            workers=cls._env_int(
                environ,
                "HTML2PDF__WORKERS",
                2,
            ),
            timeout=cls._env_int(
                environ,
                "HTML2PDF__TIMEOUT",
                45,
            ),
            max_html_bytes=cls._env_int(
                environ,
                "HTML2PDF__MAX_HTML_BYTES",
                32 * 1024 * 1024,
            ),
            max_pdf_bytes=cls._env_int(
                environ,
                "HTML2PDF__MAX_PDF_BYTES",
                64 * 1024 * 1024,
            ),
        )

    @staticmethod
    def _env(environ: Mapping[str, str], name: str, default: str = "") -> str:
        """Read a string environment variable."""
        return environ.get(name, default).strip()

    @classmethod
    def _env_int(
        cls,
        environ: Mapping[str, str],
        name: str,
        default: int,
        *,
        minimum: int = 1,
    ) -> int:
        """Read and validate a positive integer environment variable."""
        value = cls._env(environ, name)

        if not value:
            return default

        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc

        if result < minimum:
            raise ValueError(f"{name} must be at least {minimum}")

        return result


class AssetError(ValueError):
    """A document references an asset outside its submitted data URLs."""


class RequestError(ValueError):
    """An HTTP request failed validation."""

    def __init__(self, status: HTTPStatus, body: bytes) -> None:
        super().__init__(body.decode("utf-8", errors="replace").strip())
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class Request:
    """Small typed wrapper around the WSGI request environment."""

    _environ: Environ

    def _string(self, key: str) -> str:
        """Return a string value from the WSGI environment."""
        return cast(str, self._environ.get(key, ""))

    @property
    def method(self) -> str:
        """Return the HTTP request method."""
        return self._string("REQUEST_METHOD")

    @property
    def path(self) -> str:
        """Return the requested path."""
        return self._string("PATH_INFO")

    @property
    def media_type(self) -> str:
        """Return the normalized request media type."""
        return (
            self._string("CONTENT_TYPE")
            .partition(";")[0]
            .strip()
            .casefold()
        )

    @property
    def content_length(self) -> str:
        """Return the raw Content-Length value."""
        return self._string("CONTENT_LENGTH").strip()

    @property
    def authorization(self) -> str:
        """Return the Authorization header."""
        return self._string("HTTP_AUTHORIZATION").strip()

    def read(self, length: int) -> bytes:
        """Read up to length bytes from the request body."""
        stream = cast(BinaryIO, self._environ["wsgi.input"])
        return stream.read(length)


def format_bytes(value: int) -> str:
    """Return a human-readable binary byte size."""
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            if size.is_integer():
                return f"{int(size)} {unit}"

            return f"{size:.1f} {unit}"

        size /= 1024

    raise AssertionError("unreachable")


def load_index(config: Config) -> bytes:
    """Load the usage page and substitute runtime information."""
    replacements = {
        "{{VERSION}}": config.version,
        "{{SOURCE_URL}}": config.source_url,
        "{{AUTH_STATUS}}": "enabled" if config.token else "disabled",
        "{{WORKERS}}": str(config.workers),
        "{{TIMEOUT}}": str(config.timeout),
        "{{MAX_HTML_SIZE}}": format_bytes(config.max_html_bytes),
        "{{MAX_PDF_SIZE}}": format_bytes(config.max_pdf_bytes),
    }

    page = (
        Path(__file__)
        .with_name("index.html")
        .read_text(encoding="utf-8")
    )

    for placeholder, value in replacements.items():
        page = page.replace(
            placeholder,
            html.escape(value, quote=True),
        )

    return page.encode("utf-8")


def render_document(source: str) -> bytes:
    """Render one self-contained HTML document and return its PDF bytes."""
    asset_rejected = False

    def fetch_asset(url: str, *args: Any, **kwargs: Any) -> Any:
        nonlocal asset_rejected

        # Never give submitted HTML access to container files, cloud metadata,
        # private services, or the public internet. Callers must embed assets.
        if not url.startswith("data:"):
            asset_rejected = True
            raise AssetError("Assets must be embedded as data URLs")

        return default_url_fetcher(url, *args, **kwargs)

    result = HTML(
        string=source,
        # Resolve relative assets so they reach the rejecting fetcher instead
        # of being silently dropped by WeasyPrint as unresolved references.
        base_url="https://html2pdf.invalid/",
        url_fetcher=fetch_asset,
    ).write_pdf()

    # WeasyPrint can log asset failures and continue. Turn rejected assets into
    # a hard error so callers never receive a silently incomplete document.
    if asset_rejected:
        raise AssetError("Assets must be embedded as data URLs")

    return result


def bearer_token(request: Request) -> str:
    """Return the submitted bearer token, or an empty string when absent."""
    authorization = request.authorization
    if not authorization:
        return ""

    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return ""

    return parts[1].strip()


def authorized(request: Request, config: Config) -> bool:
    """Report whether the request may use the render endpoint."""
    if not config.token:
        return True

    return hmac.compare_digest(
        bearer_token(request).encode("utf-8"),
        config.token.encode("utf-8"),
    )


def duration_ms(started: float) -> int:
    """Return elapsed milliseconds since a perf-counter timestamp."""
    return round((time.perf_counter() - started) * 1000)


def respond(
    start_response: StartResponse,
    status: HTTPStatus,
    body: bytes,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: Iterable[Header] = (),
) -> Response:
    """Build a WSGI response."""
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        *extra_headers,
    ]

    start_response(
        f"{status.value} {status.phrase}",
        headers,
    )

    return [body]


def read_html(request: Request, config: Config) -> tuple[str, int]:
    """Validate and read a UTF-8 HTML request body."""
    if request.media_type != "text/html":
        raise RequestError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            b"Send text/html encoded as UTF-8\n",
        )

    if not request.content_length:
        raise RequestError(
            HTTPStatus.LENGTH_REQUIRED,
            b"Content-Length is required\n",
        )

    if not request.content_length.isascii() or not request.content_length.isdecimal():
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            b"Invalid Content-Length\n",
        )

    try:
        length = int(request.content_length)
    except ValueError as exc:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            b"Invalid Content-Length\n",
        ) from exc

    if length <= 0:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            b"HTML is required\n",
        )

    if length > config.max_html_bytes:
        raise RequestError(
            HTTPStatus.CONTENT_TOO_LARGE,
            b"HTML exceeds configured size limit\n",
        )

    body = request.read(length)

    if len(body) != length:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            b"Incomplete request body\n",
        )

    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            b"HTML must be UTF-8\n",
        ) from exc

    return source, length


def render_response(
    request: Request,
    start_response: StartResponse,
    config: Config,
) -> Response:
    """Validate a render request, render its PDF, and return the response."""
    if not authorized(request, config):
        return respond(
            start_response,
            HTTPStatus.UNAUTHORIZED,
            b"Invalid or missing bearer token\n",
            extra_headers=[("WWW-Authenticate", "Bearer")],
        )

    try:
        source, html_bytes = read_html(request, config)
    except RequestError as exc:
        return respond(
            start_response,
            exc.status,
            exc.body,
        )

    started = time.perf_counter()

    try:
        result = render_document(source)
    except AssetError:
        logger.warning(
            "render rejected duration_ms=%d html_bytes=%d reason=asset_policy",
            duration_ms(started),
            html_bytes,
        )

        return respond(
            start_response,
            HTTPStatus.UNPROCESSABLE_CONTENT,
            b"Embed images, fonts, and other assets as data URLs; "
            b"external assets are not fetched\n",
        )
    except Exception:
        logger.exception(
            "render failed duration_ms=%d html_bytes=%d reason=internal_error",
            duration_ms(started),
            html_bytes,
        )

        return respond(
            start_response,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            b"PDF rendering failed\n",
        )

    pdf_bytes = len(result)

    if pdf_bytes > config.max_pdf_bytes:
        logger.warning(
            "render rejected duration_ms=%d html_bytes=%d "
            "pdf_bytes=%d reason=pdf_too_large",
            duration_ms(started),
            html_bytes,
            pdf_bytes,
        )

        return respond(
            start_response,
            HTTPStatus.CONTENT_TOO_LARGE,
            b"PDF exceeds configured size limit\n",
        )

    logger.info(
        "render completed duration_ms=%d html_bytes=%d pdf_bytes=%d",
        duration_ms(started),
        html_bytes,
        pdf_bytes,
    )

    return respond(
        start_response,
        HTTPStatus.OK,
        result,
        "application/pdf",
    )


def create_application(config: Config) -> Application:
    """Create a WSGI application with its own configuration and cached assets."""
    index_html = load_index(config)
    logo_svg = Path(__file__).with_name("logo.svg").read_bytes()

    def application(
        environ: Environ,
        start_response: StartResponse,
    ) -> Response:
        """Serve the index, logo, health check, and HTML-to-PDF endpoint."""
        request = Request(environ)

        match request.path, request.method:
            case "/", "GET":
                return respond(
                    start_response,
                    HTTPStatus.OK,
                    index_html,
                    "text/html; charset=utf-8",
                    INDEX_HEADERS,
                )

            case "/logo.svg", "GET":
                return respond(
                    start_response,
                    HTTPStatus.OK,
                    logo_svg,
                    "image/svg+xml",
                )

            case "/healthz", "GET":
                return respond(
                    start_response,
                    HTTPStatus.OK,
                    b"ok\n",
                )

            case "/render", "POST":
                return render_response(
                    request,
                    start_response,
                    config,
                )

            case "/render", _:
                return respond(
                    start_response,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    b"Use POST\n",
                    extra_headers=[("Allow", "POST")],
                )

            case _:
                return respond(
                    start_response,
                    HTTPStatus.NOT_FOUND,
                    b"Not found\n",
                )

    return application


application = create_application(Config.from_env(os.environ))
