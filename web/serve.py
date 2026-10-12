"""
Serve the web map locally:  python web/serve.py   then open http://localhost:8000/
(Python's built-in server ignores byte-range requests, which PMTiles needs, so this adds them.)
"""
import os, re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

PORT = 8000


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.remaining = None
        m =re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = m.groups()
        if start:
            start, end = int(start), min(int(end) if end else size - 1, size - 1)
        else:  # "bytes=-N": the last N bytes
            start, end = max(0, size - int(end)), size - 1
        if start >= size:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        n = getattr(self, "remaining", None)
        if n is None:
            return super().copyfile(source, outputfile)
        while n > 0:
            chunk = source.read(min(65536, n))
            if not chunk:
                break
            outputfile.write(chunk)
            n -= len(chunk)
        self.remaining = None


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(f"Serving {here} at http://localhost:{PORT}/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), partial(RangeHandler, directory=here)).serve_forever()
