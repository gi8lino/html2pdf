import io
from dataclasses import replace
import unittest
from unittest.mock import patch

import app
from app import AssetError, render_document


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = app.Config(
            token="", version="test", source_url="", workers=2, timeout=45,
            max_html_bytes=1024, max_pdf_bytes=65536,
        )
        self.application = app.create_application(self.config)

    def configure(self, **overrides):
        self.config = replace(self.config, **overrides)
        self.application = app.create_application(self.config)

    def request(self, method="POST", path="/render", body=b"<p>Hello</p>", application=None, **overrides):
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_TYPE": "text/html; charset=utf-8",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
        environ.update(overrides)
        response = {}

        def start(status, headers):
            response["status"] = status
            response["headers"] = dict(headers)

        response["body"] = b"".join((application or self.application)(environ, start))
        return response

    def test_index(self):
        result = self.request(method="GET", path="/")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(
            result["headers"]["Content-Type"],
            "text/html; charset=utf-8",
        )
        self.assertIn(b"html2pdf", result["body"])
        self.assertIn(b'class="copy-button"', result["body"])
        self.assertIn(b'href="/logo.svg"', result["body"])
        self.assertIn(
            f'<span class="version">{self.config.version}</span>'.encode(),
            result["body"],
        )
        csp = result["headers"]["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(result["headers"]["X-Frame-Options"], "DENY")
        self.assertEqual(result["headers"]["Referrer-Policy"], "no-referrer")

    def test_applications_keep_configuration_and_pages_isolated(self):
        original = self.application
        self.configure(token="secret", version="other", max_html_bytes=1)
        with patch("app.render_document", return_value=b"%PDF-fixture"):
            self.assertEqual(self.request()["status"], "401 Unauthorized")
            self.assertEqual(
                self.request(HTTP_AUTHORIZATION="Bearer secret")["status"],
                "413 Content Too Large",
            )
            self.assertEqual(self.request(application=original)["status"], "200 OK")
        for application, version in ((original, "test"), (self.application, "other")):
            result = self.request(method="GET", path="/", application=application)
            self.assertIn(f'<span class="version">{version}</span>'.encode(), result["body"])

    def test_assets_are_loaded_once_per_application(self):
        with (
            patch("app.load_index", return_value=b"cached page") as index,
            patch.object(app.Path, "read_bytes", return_value=b"cached logo") as logo,
        ):
            application = app.create_application(self.config)
            for _ in range(2):
                self.assertEqual(
                    self.request(method="GET", path="/", application=application)["body"],
                    b"cached page",
                )
                self.assertEqual(
                    self.request(method="GET", path="/logo.svg", application=application)["body"],
                    b"cached logo",
                )
            index.assert_called_once_with(self.config)
            logo.assert_called_once_with()

    def test_module_wsgi_entry_point_serves_health(self):
        result = self.request(method="GET", path="/healthz", application=app.application)
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["body"], b"ok\n")

    def test_logo(self):
        result = self.request(method="GET", path="/logo.svg")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["headers"]["Content-Type"], "image/svg+xml")
        self.assertTrue(result["body"].startswith(b"<svg"))

    def test_health(self):
        result = self.request(method="GET", path="/healthz")
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["body"], b"ok\n")

    def test_http_contract(self):
        with patch("app.render_document", return_value=b"%PDF-fixture") as render:
            result = self.request()
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["headers"]["Content-Type"], "application/pdf")
        self.assertEqual(result["body"], b"%PDF-fixture")
        render.assert_called_once_with("<p>Hello</p>")

    def test_render_logging(self):
        with self.assertLogs("gunicorn.error", level="INFO") as logs:
            with patch("app.render_document", return_value=b"%PDF-fixture"):
                result = self.request()

        self.assertEqual(result["status"], "200 OK")
        self.assertIn("render completed", logs.output[-1])
        self.assertIn("html_bytes=12", logs.output[-1])
        self.assertIn("pdf_bytes=12", logs.output[-1])
        self.assertIn("duration_ms=", logs.output[-1])

    def test_rejected_render_logging(self):
        with self.assertLogs("gunicorn.error", level="WARNING") as logs:
            with patch("app.render_document", side_effect=AssetError):
                result = self.request()

        self.assertEqual(result["status"], "422 Unprocessable Content")
        self.assertIn("render rejected", logs.output[-1])
        self.assertIn("reason=asset_policy", logs.output[-1])
        self.assertIn("html_bytes=12", logs.output[-1])

    def test_optional_bearer_authentication(self):
        self.configure(token="secret")
        missing = self.request()
        wrong = self.request(HTTP_AUTHORIZATION="Bearer nope")
        with patch("app.render_document", return_value=b"%PDF-fixture"):
            valid = self.request(HTTP_AUTHORIZATION="Bearer secret")

        self.assertEqual(missing["status"], "401 Unauthorized")
        self.assertEqual(missing["headers"]["WWW-Authenticate"], "Bearer")
        self.assertEqual(wrong["status"], "401 Unauthorized")
        self.assertEqual(valid["status"], "200 OK")

    def test_authentication_does_not_protect_index_or_health(self):
        self.configure(token="secret")
        self.assertEqual(self.request(method="GET", path="/")["status"], "200 OK")
        self.assertEqual(
            self.request(method="GET", path="/healthz")["status"],
            "200 OK",
        )
        self.assertEqual(
            self.request(method="GET", path="/logo.svg")["status"],
            "200 OK",
        )

    def test_routes_and_validation(self):
        cases = [
            ({"method": "GET"}, 405),
            ({"path": "/render/"}, 404),
            ({"CONTENT_TYPE": "application/json"}, 415),
            ({"CONTENT_LENGTH": ""}, 411),
            ({"CONTENT_LENGTH": "bad"}, 400),
            ({"CONTENT_LENGTH": "-1"}, 400),
            ({"CONTENT_LENGTH": str(self.config.max_html_bytes + 1)}, 413),
            ({"CONTENT_LENGTH": "1000"}, 400),
            ({"body": b"\xff"}, 400),
        ]

        for args, expected in cases:
            with self.subTest(args=args), patch("app.render_document") as render:
                result = self.request(**args)
                self.assertEqual(int(result["status"].split()[0]), expected)
                render.assert_not_called()

    def test_network_and_file_assets_are_rejected(self):
        urls = [
            "https://example.com/image.png",
            "http://127.0.0.1:8080/private",
            "http://10.0.0.1/private",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ]

        for url in urls:
            with self.subTest(url=url), self.assertRaises(AssetError):
                render_document(f'<img src="{url}">')

    def test_data_url_assets_are_allowed(self):
        source = (
            '<img alt="pixel" '
            'src="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%221%22%20height%3D%221%22%2F%3E">'
        )
        result = render_document(source)
        self.assertTrue(result.startswith(b"%PDF-"))

    def test_generated_pdf_size_limit(self):
        self.configure(max_pdf_bytes=3)
        with (
            patch("app.render_document", return_value=b"1234"),
            self.assertLogs("gunicorn.error", level="WARNING") as logs,
        ):
            result = self.request()

        self.assertEqual(result["status"], "413 Content Too Large")
        self.assertEqual(result["body"], b"PDF exceeds configured size limit\n")
        self.assertIn("reason=pdf_too_large", logs.output[-1])

    def test_malformed_bearer_schemes_are_rejected(self):
        authorizations = [
            "Basic secret",
            "Token secret",
            "Bearer",
            "Bearer   ",
            "Bearer wrong",
        ]

        self.configure(token="secret")
        for authorization in authorizations:
            with self.subTest(authorization=authorization):
                result = self.request(HTTP_AUTHORIZATION=authorization)
                self.assertEqual(result["status"], "401 Unauthorized")

    def test_real_pdf_render(self):
        result = render_document("<h1>Hello</h1><p>Rendered by html2pdf.</p>")
        self.assertTrue(result.startswith(b"%PDF-"))

    def test_non_ascii_authorization_is_rejected_without_rendering(self):
        self.configure(token="secret")
        with (
            patch("app.render_document") as render,
        ):
            result = self.request(HTTP_AUTHORIZATION="Bearer séc ret")
        self.assertEqual(result["status"], "401 Unauthorized")
        render.assert_not_called()

    def test_bearer_scheme_is_case_insensitive(self):
        self.configure(token="secret")
        with (
            patch("app.render_document", return_value=b"%PDF-fixture"),
        ):
            result = self.request(HTTP_AUTHORIZATION="  bEaReR   secret  ")
        self.assertEqual(result["status"], "200 OK")

    def test_authentication_precedes_body_read(self):
        self.configure(token="secret")
        result = self.request(CONTENT_TYPE="application/json", **{"wsgi.input": None})
        self.assertEqual(result["status"], "401 Unauthorized")

    def test_content_length_requires_ascii_digits(self):
        for length in ("+12", "1_2", "１２", "١٢", "1.2"):
            with self.subTest(length=length), patch("app.render_document") as render:
                result = self.request(CONTENT_LENGTH=length)
                self.assertEqual(result["status"], "400 Bad Request")
                render.assert_not_called()

    def test_oversized_content_length_is_rejected_before_body_read(self):
        for length in (str(self.config.max_html_bytes + 1), "9" * 5000):
            with self.subTest(digits=len(length)), patch("app.render_document") as render:
                result = self.request(CONTENT_LENGTH=length, **{"wsgi.input": None})
                self.assertEqual(result["status"], "413 Content Too Large")
                render.assert_not_called()

    def test_content_length_with_leading_zeroes(self):
        with patch("app.render_document", return_value=b"%PDF-fixture") as render:
            result = self.request(CONTENT_LENGTH="0" * 5000 + "12")
        self.assertEqual(result["status"], "200 OK")
        render.assert_called_once_with("<p>Hello</p>")
        result = self.request(CONTENT_LENGTH="0000", **{"wsgi.input": None})
        self.assertEqual(result["status"], "400 Bad Request")

    def test_utf8_charset_declarations(self):
        content_types = (
            "text/html",
            "text/html; charset=utf-8",
            'text/html; CHARSET="UTF-8"',
            "text/html; charset=utf8",
            'text/html; note="a;charset=latin-1"; charset=utf-8',
            'text/html; note="a;charset=latin-1"',
        )
        for content_type in content_types:
            with self.subTest(content_type=content_type), patch(
                "app.render_document", return_value=b"%PDF-fixture"
            ) as render:
                result = self.request(CONTENT_TYPE=content_type)
                self.assertEqual(result["status"], "200 OK")
                render.assert_called_once_with("<p>Hello</p>")

    def test_unsupported_charsets_are_rejected_before_body_read(self):
        for charset in ("latin-1", "utf-16", '"ISO-8859-1"', ""):
            with self.subTest(charset=charset), patch("app.render_document") as render:
                result = self.request(
                    CONTENT_TYPE=f"text/html; charset={charset}",
                    **{"wsgi.input": None},
                )
                self.assertEqual(result["status"], "415 Unsupported Media Type")
                render.assert_not_called()

    def test_known_get_endpoints_reject_other_methods(self):
        for path in ("/", "/logo.svg", "/healthz"):
            for method in ("POST", "PUT", "DELETE", "OPTIONS"):
                with self.subTest(path=path, method=method):
                    result = self.request(method=method, path=path)
                    self.assertEqual(result["status"], "405 Method Not Allowed")
                    self.assertEqual(result["headers"]["Allow"], "GET")
                    self.assertEqual(result["body"], b"Use GET\n")

    def test_empty_body_is_rejected(self):
        with patch("app.render_document") as render:
            result = self.request(body=b"")
        self.assertEqual(result["status"], "400 Bad Request")
        render.assert_not_called()

    def test_html_limit_is_inclusive_and_counts_utf8_bytes(self):
        body = "<p>é</p>".encode()
        self.configure(max_html_bytes=len(body))
        with (
            patch("app.render_document", return_value=b"%PDF-fixture") as render,
        ):
            result = self.request(body=body, CONTENT_TYPE="TEXT/HTML; charset=UTF-8")
            self.assertEqual(result["status"], "200 OK")
            render.assert_called_once_with(body.decode())
            render.reset_mock()
            result = self.request(body=body + b" ", **{"wsgi.input": None})
            self.assertEqual(result["status"], "413 Content Too Large")
            render.assert_not_called()

    def test_pdf_limit_is_inclusive(self):
        self.configure(max_pdf_bytes=4)
        with (
            patch("app.render_document", return_value=b"1234"),
        ):
            result = self.request()
        self.assertEqual(result["status"], "200 OK")

    def test_internal_render_error_is_logged_but_not_exposed(self):
        with (
            patch("app.render_document", side_effect=RuntimeError("private details")),
            self.assertLogs("gunicorn.error", level="ERROR") as logs,
        ):
            result = self.request()
        self.assertEqual(result["status"], "500 Internal Server Error")
        self.assertEqual(result["body"], b"PDF rendering failed\n")
        self.assertIn("reason=internal_error", logs.output[-1])

    def test_response_headers_and_allowed_method(self):
        for method, path in (("GET", "/"), ("GET", "/logo.svg"),
                             ("GET", "/healthz"), ("GET", "/render"),
                             ("GET", "/missing")):
            with self.subTest(path=path):
                result = self.request(method=method, path=path)
                headers = result["headers"]
                self.assertEqual(int(headers["Content-Length"]), len(result["body"]))
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                if path == "/render":
                    self.assertEqual(headers["Allow"], "POST")

    def test_external_stylesheets_and_css_images_never_reach_fetcher(self):
        sources = [
            '<img src="image.png">',
            '<img src="/image.png">',
            '<img src="//example.com/image.png">',
            '<link rel="stylesheet" href="style.css">',
            '<link rel="stylesheet" href="https://example.com/style.css">',
            '<style>@import "file:///etc/passwd";</style>',
            '<style>body { background-image: url(http://127.0.0.1/private) }</style><p>x</p>',
            '<base href="https://example.com/"><img src="image.png">',
        ]
        for source in sources:
            with (
                self.subTest(source=source),
                patch("app.default_url_fetcher") as fetch,
                self.assertRaises(AssetError),
            ):
                render_document(source)
            fetch.assert_not_called()

    def test_inline_css_and_fragment_links_are_allowed(self):
        result = render_document(
            '<style>p { color: red }</style>'
            '<a href="#target">Jump</a><p id="target">Hello</p>'
        )
        self.assertTrue(result.startswith(b"%PDF-"))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_independent_of_host_environment(self):
        config = app.Config.from_env({})
        self.assertEqual(config, app.Config("", "dev", "", 2, 45,
                                            32 * 1024 * 1024, 64 * 1024 * 1024))

    def test_environment_overrides_and_whitespace(self):
        config = app.Config.from_env({
            "HTML2PDF__TOKEN": " secret ", "HTML2PDF__VERSION": " v1 ",
            "HTML2PDF__SOURCE_URL": " https://example.com/repo ",
            "HTML2PDF__WORKERS": " 3 ", "HTML2PDF__TIMEOUT": "60",
            "HTML2PDF__MAX_HTML_BYTES": "100", "HTML2PDF__MAX_PDF_BYTES": "200",
        })
        self.assertEqual(config, app.Config("secret", "v1", "https://example.com/repo",
                                            3, 60, 100, 200))

    def test_invalid_numeric_configuration_fails_startup(self):
        for name in ("WORKERS", "TIMEOUT", "MAX_HTML_BYTES", "MAX_PDF_BYTES"):
            for value in ("0", "-1", "nope", "1.5"):
                key = f"HTML2PDF__{name}"
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                    app.Config.from_env({key: value})

    def test_blank_values_use_defaults(self):
        config = app.Config.from_env({
            "HTML2PDF__VERSION": " ", "HTML2PDF__WORKERS": " ",
        })
        self.assertEqual(config.version, "dev")
        self.assertEqual(config.workers, 2)

    def test_index_escapes_configuration_and_never_displays_token(self):
        config = app.Config('hidden-secret', '<test>', 'https://example.com/"&',
                            3, 60, 1536, 2048)
        page = app.load_index(config).decode()
        self.assertIn('&lt;test&gt;', page)
        self.assertIn('https://example.com/&quot;&amp;', page)
        self.assertIn('enabled', page)
        self.assertIn('1.5 KiB', page)
        self.assertIn('2 KiB', page)
        self.assertNotIn('hidden-secret', page)
        self.assertNotIn('{{', page)

    def test_byte_size_formatting(self):
        for value, expected in ((0, "0 B"), (1023, "1023 B"), (1024, "1 KiB"),
                                (1536, "1.5 KiB"), (1024 ** 2, "1 MiB"),
                                (1024 ** 3, "1 GiB")):
            with self.subTest(value=value):
                self.assertEqual(app.format_bytes(value), expected)


if __name__ == "__main__":
    unittest.main()
