"""画面への読み出しの HTTP の受け付け（標準ライブラリだけ・この PC の中だけで待つ）。

  python -m agentdock.server --port 8791

GET  /api/state?owner=amiya
GET  /api/events?owner=amiya&since=0
GET  /api/view/bid?owner=amiya&item=shiroebi&kind=tender&date=2026-11-02
GET  /api/tender?lot=T-shiroebi-1&viewer=amiya
POST /api/answer   {"owner", "consultation_id", "option", "idempotency_key", "reason"}  ← 社長のスマホの答え
GET  /api/board?owner=amiya                          ← 磁石ボード（最新の版・金額はコードの計算）
POST /api/decide   {"owner", "case_id", "option_id", "action", "fingerprint", "idempotency_key"}  ← 担当者の承認・見送り・保留
GET  /api/flow?owner=amiya                           ← 段階と経過（業務 DB の事実からコードが決める）
POST /api/ask      {"owner", "case_id", "text", "idempotency_key"}  ← 担当者の質問（出来事として記録し、Claude を起こす）
GET  /api/verify?owner=amiya                         ← 技術の検証（Claude の道具の呼び出し・市場の取引帳簿の API・照合・費用）
POST /api/start    {"variant"}                        ← 業務を始める（漁協の資料と注文を入れる・1回だけ）
POST /api/worker/resume                               ← 止まった実演の通しを再開する

  python -m agentdock.server --port 8792 --real --new-take   # 本物の Claude で通す（記録は AgentDock-local の takes フォルダの「日時-live」）
GET  /             web/index.html（画面のファイルは web/ の直下の .html・.css・.js だけを返す）

安全装置：127.0.0.1 だけで待つ。TALOS の番号（8772〜8777）では起動しない。
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agentdock import consult, db, deal, events, feed, live, scene, stage, workspace
from agentdock.db import REPO
from agentdock.seed import scenario as sc

DEFAULT_PORT = 8791
TALOS_PORTS = range(8772, 8778)
MAX_BODY = 16 * 1024
WEB = (REPO / "web").resolve()
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
WORKER: live.Worker | None = None
MAX_QUESTION = 200


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

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (WEB / name).resolve()
        if f.parent != WEB or f.suffix not in TYPES or not f.is_file():
            return self._send(404, dict(error="not found"))
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES[f.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._static(u.path)
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
            if u.path == "/api/flow":
                out = stage.flow(q["owner"])
                out["worker"] = WORKER.status() if WORKER else None
                return self._send(200, out)
            if u.path == "/api/verify":
                out = stage.verify(q.get("owner", "amiya"))
                out["worker"] = WORKER.status() if WORKER else None
                out["worker_log"] = WORKER.log[-40:] if WORKER else []
                return self._send(200, out)
            if u.path == "/api/board":
                owner = q["owner"]
                if owner not in db.OWNER_CODES:
                    raise ValueError(owner)
                deal.expire(f"{sc.DEMO_DATE}T06:30:00+09:00")
                c = db.connect(owner)
                try:
                    return self._send(200, workspace.view(c))
                finally:
                    c.close()
            return self._send(404, dict(error="not found"))
        except (KeyError, ValueError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/decide":
            return self._decide()
        if u.path == "/api/ask":
            return self._ask()
        if u.path == "/api/start":
            return self._start()
        if u.path == "/api/worker/resume":
            if WORKER:
                WORKER.resume()
            return self._send(200, dict(worker=WORKER.status() if WORKER else None))
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


    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ValueError("bad body size")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _ask(self):
        try:
            b = self._body()
            if b["owner"] != "amiya":
                raise ValueError("owner")
            text = str(b["text"]).strip()
            if not text or len(text) > MAX_QUESTION:
                raise ValueError(f"text must be 1-{MAX_QUESTION} chars")
            key = str(b["idempotency_key"])
            c = db.connect("amiya")
            try:
                for e in c.execute("SELECT id, payload_json FROM biz_event WHERE kind = 'staff_question'"):
                    if json.loads(e["payload_json"]).get("key") == key:
                        return self._send(200, dict(event_id=e["id"], repeated=True))
                case = c.execute("SELECT id, status FROM deal_case WHERE id = ?", (int(b["case_id"]),)).fetchone()
                if case is None or case["status"] not in workspace.DECIDING:
                    return self._send(409, dict(error="rejected", detail="この案件は今、質問を受け付けていません"))
                eid = events.emit(c, "staff_question", dict(case_id=case["id"], text=text, key=key))
            finally:
                c.close()
            return self._send(200, dict(event_id=eid))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _start(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n <= MAX_BODY else {}
            variant = b.get("variant", "normal")
            if variant not in scene.VARIANTS:
                raise ValueError(variant)
            if live.has_started():
                return self._send(409, dict(error="rejected", detail="今日の業務はもう始まっています"))
            scene.apply_variant(variant)
            return self._send(200, dict(started=scene.start(variant=variant), variant=variant))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _decide(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(400, dict(error="bad body size"))
        try:
            b = json.loads(self.rfile.read(n).decode("utf-8"))
            if b["owner"] not in db.OWNER_CODES or b["action"] not in ("approve", "reject", "hold"):
                raise ValueError("owner or action")
            r = deal.decide(b["owner"], int(b["case_id"]), int(b["option_id"]) if b.get("option_id") else None, b["action"],
                            b.get("fingerprint"), str(b["idempotency_key"]))
            return self._send(409 if r["result"] == "stale" else 200, r)
        except deal.DealError as e:
            return self._send(409, dict(error="rejected", detail=str(e)))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    if port in TALOS_PORTS:
        raise ValueError(f"port {port} is reserved for TALOS")
    return _Exclusive(("127.0.0.1", port), Handler)


class _Exclusive(ThreadingHTTPServer):
    """同じ番号で2つ目のサーバーが起動しないようにする（Windows は再利用の指定で二重に待ててしまう・2026-11-03 実測）。"""
    allow_reuse_address = False

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--real", action="store_true", help="本物の Claude で待ちの出来事を処理する（実演の通し）")
    ap.add_argument("--new-take", action="store_true", help="撮影の置き場を新しく作り、種の DB を入れる")
    ap.add_argument("--max-calls", type=int, default=live.MAX_TOTAL_CALLS)
    a = ap.parse_args(argv)
    global WORKER
    take = None
    if a.new_take:
        import os
        from agentdock.seed import build
        take = live.new_take_dir()
        os.environ["AGENTDOCK_DATA"] = str(take)
        if build.main([]) != 0:
            return 2
    try:
        srv = make_server(a.port)
    except ValueError as e:
        print(f"stop: {e}", file=sys.stderr)
        return 2
    if a.real:
        from agentdock import agents, companion
        clients = {o: companion.ClaudeClient(effort=agents.autonomy.EFFORT) for o in db.OWNER_CODES}
        WORKER = live.Worker(clients, run_kind="real", max_total_calls=a.max_calls, take_dir=take)
        WORKER.start()
    print(f"agentdock feed on http://127.0.0.1:{srv.server_address[1]}  data={db.data_dir()}  real={a.real}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if WORKER:
            WORKER.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
