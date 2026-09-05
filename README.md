# html2pdf

`html2pdf` is a small, self-hosted HTTP service that renders self-contained HTML to PDF with [WeasyPrint](https://weasyprint.org/).

It deliberately has a narrow API: send UTF-8 HTML to `POST /render` and receive an `application/pdf` response. The service does not know about users, documents, templates, or any particular application.

## Why a separate service?

WeasyPrint brings native rendering dependencies that many applications do not otherwise need. Running it separately keeps those dependencies and the comparatively expensive PDF workload isolated from the calling application.

The renderer also refuses network and file-system asset access. Submitted HTML cannot make the service fetch arbitrary URLs or local files; callers must provide self-contained documents.

## API

| Method | Path       | Description                         |
| ------ | ---------- | ----------------------------------- |
| `GET`  | `/`        | Small human-readable usage page.    |
| `GET`  | `/healthz` | Health check. Returns `200 OK`.     |
| `POST` | `/render`  | Render UTF-8 HTML and return a PDF. |

### Render HTML

The request body is the HTML document itself. Use `Content-Type: text/html; charset=utf-8`.

```sh
curl --fail-with-body \
  -H 'Content-Type: text/html; charset=utf-8' \
  --data-binary @document.html \
  http://localhost:8080/render \
  -o document.pdf
```

Inline CSS works normally:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <style>
      @page {
        size: A4;
        margin: 20mm;
      }
      body {
        font-family: sans-serif;
      }
      h1 {
        break-after: avoid;
      }
    </style>
  </head>
  <body>
    <h1>Hello</h1>
    <p>This document is rendered by html2pdf.</p>
  </body>
</html>
```

### Assets

Documents must be self-contained. Referenced images, fonts, and other resources must use `data:` URLs.

Allowed:

```html
<img src="data:image/png;base64,..." alt="Example" />
```

Rejected:

```html
<img src="https://example.com/image.png" alt="Example" />
<img src="file:///etc/passwd" alt="Example" />
```

This restriction prevents submitted documents from using the renderer for SSRF, cloud-metadata access, local-file reads, or requests to private services.

## Authentication

Bearer-token authentication is optional. For a service that is reachable only over a trusted private network, you may leave it disabled. If the renderer is reachable across a broader network, configuring a token is recommended in addition to normal network-level controls.

Set `HTML2PDF__TOKEN`:

```sh
export HTML2PDF__TOKEN="$(openssl rand -hex 32)"
```

Then send it as a bearer token:

```sh
curl --fail-with-body \
  -H "Authorization: Bearer $HTML2PDF__TOKEN" \
  -H 'Content-Type: text/html; charset=utf-8' \
  --data-binary @document.html \
  http://localhost:8080/render \
  -o document.pdf
```

Only `/render` is protected. `/` and `/healthz` remain public so they can be used for documentation and health probes.

If `HTML2PDF__TOKEN` is unset or empty, `/render` accepts requests without authentication.

## Run with Docker

Build and start the service:

```sh
docker build -t html2pdf .
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  html2pdf
```

With authentication:

```sh
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -e HTML2PDF__TOKEN="$HTML2PDF__TOKEN" \
  html2pdf
```

Open `http://localhost:8080/` for the built-in usage page.

## Docker Compose

```sh
docker compose up --build
```

To enable authentication:

```sh
HTML2PDF__TOKEN="$(openssl rand -hex 32)" docker compose up --build
```

The included Compose configuration runs the container with a read-only root filesystem, a bounded `/tmp`, dropped Linux capabilities, and `no-new-privileges`.

## Limits

The service currently enforces these fixed limits:

- HTML request: 32 MiB
- generated PDF: 64 MiB
- Gunicorn workers: 2
- render timeout: 45 seconds per worker

These limits are intentionally conservative defaults for a general-purpose service. Container CPU and memory limits should still be configured by the deployment environment.

## Security model

HTML-to-PDF rendering is a resource-intensive operation and should not be exposed as an unrestricted public endpoint.

Recommended deployment controls:

- keep the service on a private network whenever possible;
- configure `HTML2PDF__TOKEN` when callers are not fully trusted at the network layer;
- run the container as non-root;
- use a read-only root filesystem;
- provide only a temporary writable `/tmp`;
- set CPU, memory, and request-rate limits at the container or proxy layer;
- terminate TLS at a reverse proxy or ingress if traffic crosses an untrusted network.

The service itself blocks external and local asset fetching. It does not implement TLS, user accounts, token rotation, rate limiting, or persistent storage.

## Development

The Makefile follows the same small workflow used by the other projects in this organization. Run `make` or `make help` to list the available targets.

Run the test suite:

```sh
make test
```

Build the development image:

```sh
make build
```

Build and run the service locally on `127.0.0.1:8080`:

```sh
make dev
```

Enable bearer-token authentication during development:

```sh
HTML2PDF__TOKEN="$(openssl rand -hex 32)" make dev-auth
```

The bind address, port, image name, and development tag can be overridden when needed:

```sh
make dev HOST=0.0.0.0 PORT=9090 IMAGE=example/html2pdf DEV_TAG=local
```

For the hardened Compose configuration:

```sh
make compose
```

or:

```sh
HTML2PDF__TOKEN="$(openssl rand -hex 32)" make compose-auth
```

The test suite covers the HTTP contract, optional bearer authentication, input validation, asset isolation, and a real PDF render.

## License

html2pdf is licensed under the [Apache License, Version 2.0](LICENSE).
