"""Loopback-only HTTP API and bundled browser UI."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from .store import Store, WorkflowError

MAX_BODY = 34 * 1024 * 1024
WEB = Path(__file__).parent / "web"


def make_server(store: Store, port=8788):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, data, status=200, mime="application/json; charset=utf-8", disposition=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            port = self.server.server_address[1]
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            try:
                if not self.valid_host():
                    raise WorkflowError("只允许本机Host", 403)
                path = urlsplit(self.path).path
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path in static:
                    name, mime = static[path]
                    return self.reply((WEB / name).read_bytes(), mime=mime)
                if path == "/api/state":
                    return self.reply(store.state())
                match = re.fullmatch(r"/api/tasks/(fig-[a-f0-9]{10})/inspect", path)
                if match:
                    return self.reply(store.inspect(match[1]))
                match = re.fullmatch(r"/api/files/([a-f0-9]{32})", path)
                if match:
                    file, data = store.file_bytes(match[1])
                    return self.reply(data, mime="image/png" if file["role"] == "figure" else "application/octet-stream",
                                      disposition=("inline" if file["role"] == "figure" else "attachment") + "; filename*=UTF-8''" + quote(file["name"]))
                match = re.fullmatch(r"/api/releases/([a-f0-9]{32})\.zip", path)
                if match:
                    return self.reply(store.release_bytes(match[1]), mime="application/zip",
                                      disposition=f'attachment; filename="figure-package-{match[1][:8]}.zip"')
                raise WorkflowError("路径不存在", 404)
            except WorkflowError as exc:
                self.reply({"error": str(exc)}, exc.status)
            except (OSError, ValueError) as exc:
                self.reply({"error": str(exc)}, 400)

        def do_POST(self):
            try:
                if not self.valid_host():
                    raise WorkflowError("只允许本机Host", 403)
                origin = self.headers.get("Origin")
                if origin is not None and origin != "http://" + self.headers["Host"]:
                    raise WorkflowError("拒绝跨来源写入", 403)
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise WorkflowError("需要application/json", 415)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise WorkflowError("无效Content-Length") from None
                if not 0 < length <= MAX_BODY:
                    raise WorkflowError("请求为空或超过34MiB", 413)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise WorkflowError("请求必须为JSON对象")
                path = urlsplit(self.path).path
                if path == "/api/tasks":
                    return self.reply(store.create_task(payload), 201)
                if path == "/api/releases":
                    return self.reply(store.freeze(payload), 201)
                match = re.fullmatch(r"/api/tasks/(fig-[a-f0-9]{10})/(start|submit|review)", path)
                if match:
                    return self.reply(getattr(store, match[2])(match[1], payload))
                raise WorkflowError("路径不存在", 404)
            except WorkflowError as exc:
                self.reply({"error": str(exc)}, exc.status)
            except (OSError, ValueError) as exc:
                self.reply({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
