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

Only `/render` is protected. `/`, `/healthz`, and `/logo.svg` remain public so they can be used for documentation, health probes, and the built-in usage page.

If `HTML2PDF__TOKEN` is unset or empty, `/render` accepts requests without authentication.

## Configuration

The service is configured through environment variables.

| Variable                   | Default        | Description                                 |
| -------------------------- | -------------- | ------------------------------------------- |
| `HTML2PDF__TOKEN`          | empty          | Optional bearer token for `/render`.        |
| `HTML2PDF__LISTEN_ADDRESS` | `0.0.0.0:8080` | Gunicorn bind address inside the container. |
| `HTML2PDF__WORKERS`        | `2`            | Number of Gunicorn workers.                 |
| `HTML2PDF__TIMEOUT`        | `45`           | Gunicorn worker timeout in seconds.         |
| `HTML2PDF__MAX_HTML_BYTES` | `33554432`     | Maximum HTML request size (32 MiB).         |
| `HTML2PDF__MAX_PDF_BYTES`  | `67108864`     | Maximum generated PDF size (64 MiB).        |

Size limits are configured in bytes.

For example, to allow HTML documents up to 64 MiB and generated PDFs up to 128 MiB:

```sh
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -e HTML2PDF__MAX_HTML_BYTES=67108864 \
  -e HTML2PDF__MAX_PDF_BYTES=134217728 \
  html2pdf
```

The configured limits and build version are displayed on the built-in usage page.

Invalid or non-positive size limits cause the application to fail during startup rather than silently falling back to another value.

## Version

The application version is embedded when the container image is built.

Pass it with the `VERSION` build argument:

```sh
docker build \
  --build-arg VERSION=v1.2.3 \
  -t html2pdf:v1.2.3 \
  .
```

The Dockerfile exposes that value to the application as `HTML2PDF__VERSION`.

When using the Makefile, `make build` uses the latest `v*` Git tag as the build version, or `dev` when no matching tag exists.

You can override it explicitly:

```sh
make build BUILD_VERSION=v1.2.3
```

Release builds performed by GitHub Actions automatically pass the generated release version into the image.

## Run with Docker

Build and start the service:

```sh
docker build --build-arg VERSION=dev -t html2pdf .
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

With custom runtime settings:

```sh
docker run --rm \
  -p 127.0.0.1:8080:8080 \
  -e HTML2PDF__WORKERS=4 \
  -e HTML2PDF__TIMEOUT=60 \
  -e HTML2PDF__MAX_HTML_BYTES=67108864 \
  -e HTML2PDF__MAX_PDF_BYTES=134217728 \
  html2pdf
```

Open `http://localhost:8080/` for the built-in usage page.

To expose the service on host port `9090`, keep the default container listen address and change the port mapping:

```sh
docker run --rm -p 127.0.0.1:9090:8080 html2pdf
```

`HTML2PDF__LISTEN_ADDRESS` controls the address inside the container. If you change its port, the container port in the mapping must match:

```sh
docker run --rm \
  -e HTML2PDF__LISTEN_ADDRESS=0.0.0.0:9090 \
  -p 127.0.0.1:9090:9090 \
  html2pdf
```

Both examples expose the usage page at `http://localhost:9090/`.

## Docker Compose

Start the service with:

```sh
docker compose up --build
```

To enable authentication:

```sh
HTML2PDF__TOKEN="$(openssl rand -hex 32)" docker compose up --build
```

Runtime settings can also be overridden through the environment:

```sh
HTML2PDF__WORKERS=4 \
HTML2PDF__TIMEOUT=60 \
HTML2PDF__MAX_HTML_BYTES=67108864 \
HTML2PDF__MAX_PDF_BYTES=134217728 \
docker compose up --build
```

The included Compose configuration runs the container with a read-only root filesystem, a bounded `/tmp`, dropped Linux capabilities, and `no-new-privileges`.

It maps host port `8080` to container port `8080` and does not forward `HTML2PDF__LISTEN_ADDRESS` from your shell. To change only the host port, edit `ports` to use, for example, `127.0.0.1:9090:8080`. To change the container's listening port as well, add `HTML2PDF__LISTEN_ADDRESS` to the service's `environment` and update the container port in `ports` to match.

## Resource limits

The default application limits are intentionally conservative:

- maximum HTML request: 32 MiB
- maximum generated PDF: 64 MiB
- Gunicorn workers: 2
- worker timeout: 45 seconds

All four values can be adjusted for the deployment environment.

These application-level limits do not replace container resource limits. CPU, memory, and request-rate limits should still be configured at the container, orchestrator, or reverse-proxy layer.

## Observability

Each render writes basic operational information to the application log.

Successful renders include the render duration and input/output sizes:

```text
render completed duration_ms=842 html_bytes=18342 pdf_bytes=91427
```

Rejected or failed renders include a reason where applicable:

```text
render rejected duration_ms=51 html_bytes=18342 reason=asset_policy
```

```text
render rejected duration_ms=917 html_bytes=18342 pdf_bytes=70000000 reason=pdf_too_large
```

This provides basic visibility into render performance and document sizes without requiring a separate metrics system.

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

The renderer accepts only embedded `data:` assets. Network URLs and local file references are rejected.

The built-in usage page also sends restrictive browser security headers, including a Content Security Policy that prevents framing and external resource loading.

The service does not implement TLS, user accounts, token rotation, rate limiting, or persistent storage.

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

The bind address, port, image name, development tag, and build version can be overridden when needed:

```sh
make dev \
  HOST=0.0.0.0 \
  PORT=9090 \
  IMAGE=example/html2pdf \
  DEV_TAG=local \
  BUILD_VERSION=dev
```

For the hardened Compose configuration:

```sh
make compose
```

or with authentication:

```sh
HTML2PDF__TOKEN="$(openssl rand -hex 32)" make compose-auth
```

The test suite covers the HTTP contract, optional bearer authentication, input validation, configured size limits, asset isolation, usage-page security headers, logging, and real PDF rendering.

## License

html2pdf is licensed under the [Apache License, Version 2.0](LICENSE).
