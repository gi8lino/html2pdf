import io
import unittest
from unittest.mock import patch

import app
from app import AssetError, MAX_HTML_BYTES, application, render_document


class ServiceTests(unittest.TestCase):
    def request(self, method="POST", path="/render", body=b"<p>Hello</p>", **overrides):
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

        response["body"] = b"".join(application(environ, start))
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
            f"Version <code>{app.VERSION}</code>".encode(),
            result["body"],
        )

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
        with patch.object(app, "AUTH_TOKEN", "secret"):
            missing = self.request()
            wrong = self.request(HTTP_AUTHORIZATION="Bearer nope")
            with patch("app.render_document", return_value=b"%PDF-fixture"):
                valid = self.request(HTTP_AUTHORIZATION="Bearer secret")

        self.assertEqual(missing["status"], "401 Unauthorized")
        self.assertEqual(missing["headers"]["WWW-Authenticate"], "Bearer")
        self.assertEqual(wrong["status"], "401 Unauthorized")
        self.assertEqual(valid["status"], "200 OK")

    def test_authentication_does_not_protect_index_or_health(self):
        with patch.object(app, "AUTH_TOKEN", "secret"):
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
            ({"CONTENT_LENGTH": str(MAX_HTML_BYTES + 1)}, 413),
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
        with (
            patch.object(app, "MAX_PDF_BYTES", 3),
            patch("app.render_document", return_value=b"1234"),
            self.assertLogs("gunicorn.error", level="WARNING") as logs,
        ):
            result = self.request()

        self.assertEqual(result["status"], "413 Content Too Large")
        self.assertEqual(result["body"], b"PDF exceeds 64 MiB\n")
        self.assertIn("reason=pdf_too_large", logs.output[-1])

    def test_malformed_bearer_schemes_are_rejected(self):
        authorizations = [
            "Basic secret",
            "Token secret",
            "Bearer",
            "Bearer   ",
            "Bearer wrong",
        ]

        with patch.object(app, "AUTH_TOKEN", "secret"):
            for authorization in authorizations:
                with self.subTest(authorization=authorization):
                    result = self.request(HTTP_AUTHORIZATION=authorization)
                    self.assertEqual(result["status"], "401 Unauthorized")

    def test_real_pdf_render(self):
        result = render_document("<h1>Hello</h1><p>Rendered by html2pdf.</p>")
        self.assertTrue(result.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
