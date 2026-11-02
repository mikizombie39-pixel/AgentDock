"""画面へ流す口の HTTP の受け口（標準ライブラリだけ・この PC の中だけで待つ）。

  python -m agentdock.server --port 8791

GET  /api/state?owner=amiya
GET  /api/events?owner=amiya&since=0
GET  /api/view/bid?owner=amiya&item=shiroebi&kind=tender&date=2026-11-02
GET  /api/tender?lot=T-shiroebi-1&viewer=amiya
POST /api/answer   {"owner", "consultation_id", "option", "idempotency_key", "reason"}  ← 社長のスマホの答え

安全装置：127.0.0.1 だけで待つ。TALOS の番号（8772〜8777）では起動しない。
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agentdock import consult, db, feed
from agentdock.seed import scenario as sc

DEFAULT_PORT = 8791
TALOS_PORTS = range(8772, 8778)
MAX_BODY = 16 * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):   # 標準の記録は出さない
        pass

    def _send(self, status: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/state":
                return self._send(200, feed.state(q["owner"]))
            if u.path == "/api/events":
                return self._send(200, feed.events(q["owner"], int(q.get("since", 0)), int(q.get("limit", 100))))
            if u.path == "/api/view/bid":
                return self._send(200, feed.bid_view(q["owner"], q["item"], q.get("kind", "tender"), q.get("date", sc.DEMO_DATE)))
            if u.path == "/api/tender":
                return self._send(200, feed.tender_view(q["lot"], q["viewer"]))
            return self._send(404, dict(error="not found"))
        except (KeyError, ValueError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/answer":
            return self._send(404, dict(error="not found"))
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(400, dict(error="bad body size"))
        try:
            b = json.loads(self.rfile.read(n).decode("utf-8"))
            owner = b["owner"]
            if owner not in db.OWNER_CODES:
                raise ValueError(owner)
            c = db.connect(owner)
            try:
                r = consult.answer(c, int(b["consultation_id"]), b["option"], b["idempotency_key"], b.get("reason"))
            finally:
                c.close()
            return self._send(200, r)
        except consult.StaleError as e:
            return self._send(409, dict(error="stale", detail=str(e)))
        except consult.ConsultError as e:
            return self._send(409, dict(error="rejected", detail=str(e)))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    if port in TALOS_PORTS:
        raise ValueError(f"port {port} is reserved for TALOS")
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)
    try:
        srv = make_server(a.port)
    except ValueError as e:
        print(f"stop: {e}", file=sys.stderr)
        return 2
    print(f"agentdock feed on http://127.0.0.1:{srv.server_address[1]}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
