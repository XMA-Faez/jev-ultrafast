"""Serve bench/pages and the inspector's static fixtures on a free loopback port."""

import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PAGE_ROOTS = (
    Path(__file__).with_name("pages"),
    Path(__file__).resolve().parents[1] / "jev_ultrafast" / "static",
)
SERVED_SUFFIXES = {".html", ".js", ".css"}


def resolve_page(request_path):
    name = unquote(urlparse(request_path).path).lstrip("/")
    if not name or "/" in name or name.startswith(".") or Path(name).suffix not in SERVED_SUFFIXES:
        return None
    return next((root / name for root in PAGE_ROOTS if (root / name).is_file()), None)


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = resolve_page(self.path)
        if page is None:
            body, status, content_type = b"Not found", 404, "text/plain"
        else:
            body, status = page.read_bytes(), 200
            content_type = mimetypes.guess_type(page.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_arguments):
        pass


class LocalPages:
    """A loopback server on an OS-chosen port; use as a context manager or call close()."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, template):
        return template.format(local=self.origin)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_arguments):
        self.close()


if __name__ == "__main__":
    with LocalPages() as pages:
        print(pages.origin, flush=True)
        threading.Event().wait()
