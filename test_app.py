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
        self.assertEqual(result["headers"]["Content-Type"],
                         "text/html; charset=utf-8")
        self.assertIn(b"html2pdf", result["body"])

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
            self.assertEqual(self.request(
                method="GET", path="/")["status"], "200 OK")
            self.assertEqual(self.request(
                method="GET", path="/healthz")["status"], "200 OK")

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

    def test_external_assets_are_rejected(self):
        with self.assertRaises(AssetError):
            render_document('<img src="https://example.com/image.png">')

    def test_real_pdf_render(self):
        result = render_document("<h1>Hello</h1><p>Rendered by html2pdf.</p>")
        self.assertTrue(result.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
