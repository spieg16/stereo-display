from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
PORT = 8765

ALLOWED_FILES = {
    "/now_playing.json": ("now_playing.json", "application/json"),
    "/now_playing_artwork.jpg": ("now_playing_artwork.jpg", "image/jpeg"),
}


class NowPlayingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve_file(include_body=True)

    def do_HEAD(self):
        self.serve_file(include_body=False)

    def serve_file(self, include_body):
        entry = ALLOWED_FILES.get(self.path)

        if entry is None:
            self.send_error(404)
            return

        filename, content_type = entry
        path = DIRECTORY / filename

        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, "Now-playing file not available")
            return
        except OSError:
            self.send_error(500, "Could not read now-playing file")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if include_body:
            self.wfile.write(data)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), NowPlayingHandler)
    print(f"Now-playing server listening on port {PORT}", flush=True)
    server.serve_forever()
