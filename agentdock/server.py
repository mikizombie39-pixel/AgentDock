"""画面への読み出しの HTTP の受け付け（標準ライブラリだけ・この PC の中だけで待つ）。

  python -m agentdock.server --port 8791

GET  /api/state?owner=amiya
GET  /api/events?owner=amiya&since=0
GET  /api/view/bid?owner=amiya&item=shiroebi&kind=tender&date=2026-11-02
GET  /api/tender?lot=T-shiroebi-1&viewer=amiya
POST /api/answer   {"owner", "consultation_id", "option", "idempotency_key", "reason", "change", "role"}  ← 相談への答え
                   option="change" は磁石ボードの相談で数量・配分を変える：change={"quantity", "allocations": [{"order_id" か "customer", "quantity"}]}
                   role は答えた人（staff・president）。答えが通ると出来事 human_choice を書き、Claude が交渉し直す
GET  /api/thread?owner=amiya&item=aji&date=2026-11-02   ← 組織のやり取り（送り手・受け手・種類・数量・値・期限・仮押さえ・理由）
GET  /api/board?owner=amiya                          ← 磁石ボード（最新の版・金額はコードの計算）
GET  /api/current?owner=amiya&item=buri_xl           ← 現在地（注文ごとの状態の行・押さえ・人の決定・AI の短い問いと案。状態の行はコードが作る）
GET  /api/work?owner=amiya&item=buri_xl              ← 作業の画面の一覧（読み取ったこと・AIが済ませたこと・あなたを待つこと・やり取り・残った仕事）
GET  /api/source?owner=amiya&db=market&table=network_message&id=3   ← 一覧の根拠の行（自社の行と、自社が当事者の市場の行だけ）
POST /api/decide   {"owner", "case_id", "option_id", "action", "fingerprint", "idempotency_key", "actor"}  ← 担当者（または社長）の承認・見送り・保留・社長の差し戻し（action=return）
GET  /api/flow?owner=amiya                           ← 段階と経過（業務 DB の事実からコードが決める）
POST /api/ask      {"owner", "case_id", "text", "idempotency_key", "kind"}  ← 担当者の質問・選択肢にない指示（kind=instruction）。出来事として記録し、Claude を起こす
GET  /api/verify?owner=amiya                         ← 技術の検証（Claude の道具の呼び出し・市場の取引帳簿の API・照合・費用）
POST /api/start    {"variant", "item"}                ← 業務を始める（漁協の資料と品目の注文を入れる・1回だけ・item は aji か buri_xl）
POST /api/order    {"owner", "customer", "item", "quantity", "idempotency_key"}  ← 作業の途中で売り先の注文が入る（実演の操作・売値は売り先の表から）
POST /api/draft    {"owner", "draft_id", "action": "approve"|"discard", "role"}  ← 売り先への連絡の下書きを人が決める（送るのは人）
POST /api/retry   {"owner", "item", "idempotency_key", "role"}  ← 不足が残り、どこにも当たっていない時に、AI にもう一度当たってもらう（出来事を書き、AI を起こす）
POST /api/stock_answer {"owner", "question_id", "choice", "idempotency_key", "role"}  ← 引当のない在庫の扱いの問いに人が答える（出来事を書き、AI を起こす）
GET  /api/mate?owner=amiya&item=buri_xl             ← 仲間との対話の画面（態度・実行の状態・語りかけ・行動・理由の1枚を1つの返り値で）
GET  /api/morning?owner=amiya                       ← 今日の仕事の流れ（コードの決まりで並べる）と「一緒に確認する」の中身
POST /api/delegate {"owner", "scope", "checked": {"orders", "directives"}, "idempotency_key", "role", "item"}  ← 確かめて任せる（業務を始める）
GET  /api/current_timeline?owner=amiya&item=buri_xl  ← 記録の再生：今の状態が変わった時刻ごとの今の状態（記録から組み直す）
POST /api/worker/resume                               ← 止まった実演の通しを再開する
POST /api/chat     {"owner", "item", "text", "idempotency_key"}  ← 仲間へのチャット（聞く・頼む）。読むだけの道具で答える（業務の状態は変えない）
GET  /api/chat?owner=amiya&item=buri_xl              ← これまでの対話（新しい順）
GET  /api/chat/turn?owner=amiya&id=3                 ← 1回の対話の記録（渡した文脈・取得した記録・捨てた根拠・道具の呼び出し）
POST /api/chat/run {"owner", "turn_id", "idempotency_key"}  ← 依頼の確認の後［この内容で頼む］：今の画面の操作と同じ中身なら、ボタンと同じ処理（dispatch）へ渡す

  python -m agentdock.server --port 8792 --real --new-take   # 本物の Claude で通す（記録は AgentDock-local の takes フォルダの「日時-live」）
GET  /             web/index.html（画面のファイルは web/ の直下の .html・.css・.js だけを返す）

安全装置：127.0.0.1 だけで待つ。TALOS の番号（8772〜8777）では起動しない。
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agentdock import agents, chat, consult, coop, db, deal, events, feed, live, mate, scene, stage, stockwork, trace, work, workspace
from agentdock.db import REPO
from agentdock.seed import scenario as sc

DEFAULT_PORT = 8791
TALOS_PORTS = range(8772, 8778)
MAX_BODY = 16 * 1024
WEB = (REPO / "web").resolve()
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".png": "image/png", ".ico": "image/x-icon"}   # ロゴとファビコン（web/ の直下だけ）
WORKER: live.Worker | None = None
CHAT = None          # チャットの呼び出し先（--real の時は本物の Claude・ほかは模擬の Claude）
CHAT_KIND = "mock"
CHAT_RUN = False     # チャットからの依頼の実行（段4）。段2 は質問と下見だけ：依頼は確認の文まで出し、実行は断る（2026-11-04 デイブ）
CHAT_RUN_OFF = "チャットからの実行は、まだ使えません。画面のボタンで進めてください。"
MAX_QUESTION = 200


class Rejected(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def dispatch(owner: str, path: str, body: dict, key: str) -> tuple[int, dict]:
    """人の操作の実行（画面のボタンとチャットの依頼の両方がここを通る）。権限・社長の指示・二重の防止は、各処理の検査がそのまま行う。"""
    if owner not in db.OWNER_CODES:
        raise ValueError("owner")
    if path == "/api/decide":
        actor = body.get("actor") or "staff"
        if body["action"] not in ("approve", "reject", "hold", "return") or actor not in ("staff", "president"):
            raise ValueError("action or actor")
        try:
            r = deal.decide(owner, int(body["case_id"]), int(body["option_id"]) if body.get("option_id") else None, body["action"],
                            body.get("fingerprint"), key, actor=actor, reason=body.get("reason"))
        except deal.DealError as e:
            raise Rejected(str(e)) from None
        return (409 if r["result"] == "stale" else 200), r
    c = db.connect(owner)
    try:
        if path == "/api/stock_answer":
            try:
                return 200, stockwork.answer(c, int(body["question_id"]), str(body["choice"]), key, str(body.get("role") or "staff"))
            except stockwork.StockError as e:
                raise Rejected(str(e)) from None
        if path == "/api/draft":
            try:
                return 200, consult.decide_draft(c, int(body["draft_id"]), str(body["action"]), str(body.get("role") or "staff"))
            except consult.ConsultError as e:
                raise Rejected(str(e)) from None
        if path == "/api/retry":   # 行き止まりかどうかはコードが今の記録で確かめる（画面が古くても、当たっている先がある時は受け付けない）
            item = str(body["item"])
            prev = c.execute("SELECT 1 FROM retry_request WHERE idempotency_key = ?", (key,)).fetchone()
            short = mate.dead_end(owner, item)
            if short is None and prev is None:
                raise Rejected("いまは当たり直しを頼めません（足りない数が無いか、AI が当たっている最中です）")
            try:
                return 200, consult.request_retry(c, item, sc.DEMO_DATE, short or 1, key, str(body.get("role") or "staff"))
            except consult.ConsultError as e:
                raise Rejected(str(e)) from None
    finally:
        c.close()
    raise ValueError(f"no action for {path}")


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
            if u.path == "/api/coop":   # 岩瀬漁協の AI の画面（読むだけ）
                return self._send(200, coop.view(q.get("date", sc.DEMO_DATE)))
            if u.path == "/api/trace":   # 一つの取引の証拠のたどり（技術の検証の画面の1節）
                try:
                    return self._send(200, trace.trace(q.get("owner", "amiya"), int(q["case"]) if q.get("case") else None))
                except KeyError as e:
                    if str(e).strip("'") == "case":
                        raise
                    return self._send(404, dict(error="not found"))
            if u.path == "/api/thread":
                if q["owner"] not in db.OWNER_CODES:
                    raise ValueError(q["owner"])
                return self._send(200, dict(owner=q["owner"], item=q["item"], unit=_unit(q["owner"], q["item"]),
                                            messages=thread_view(q["owner"], q["item"], q.get("date", sc.DEMO_DATE))))
            if u.path == "/api/current":
                deal.expire(f"{sc.DEMO_DATE}T06:30:00+09:00")
                return self._send(200, work.current(q["owner"], q["item"], q.get("date", sc.DEMO_DATE)))
            if u.path == "/api/current_timeline":
                return self._send(200, work.timeline(q["owner"], q["item"], q.get("date", sc.DEMO_DATE)))
            if u.path == "/api/mate":
                deal.expire(f"{sc.DEMO_DATE}T06:30:00+09:00")
                if q["owner"] not in db.OWNER_CODES:
                    raise ValueError(q["owner"])
                if mate.tender_item(q["owner"], q["item"]):
                    return self._send(200, mate.tender_view(q["owner"], q["item"], q.get("date", sc.DEMO_DATE)))
                return self._send(200, mate.view(q["owner"], q["item"], q.get("date", sc.DEMO_DATE), worker_error=WORKER.error if WORKER else None))
            if u.path == "/api/morning":
                if q["owner"] not in db.OWNER_CODES:
                    raise ValueError(q["owner"])
                return self._send(200, dict(mate.morning(q["owner"], q.get("date", sc.DEMO_DATE)), check=mate.check_sheet(q["owner"], q.get("date", sc.DEMO_DATE))))
            if u.path == "/api/work":
                return self._send(200, work.view(q["owner"], q["item"], q.get("date", sc.DEMO_DATE)))
            if u.path == "/api/chat":
                if q["owner"] not in db.OWNER_CODES:
                    raise ValueError(q["owner"])
                return self._send(200, dict(turns=chat.history(q["owner"], q.get("item", "buri_xl"), int(q.get("limit", 20))), run_kind=CHAT_KIND,
                                            run_enabled=CHAT_RUN))
            if u.path == "/api/chat/turn":
                if q["owner"] not in db.OWNER_CODES:
                    raise ValueError(q["owner"])
                try:
                    return self._send(200, chat.turn(q["owner"], int(q["id"])))
                except KeyError:
                    return self._send(404, dict(error="not found"))
            if u.path == "/api/source":
                try:
                    return self._send(200, work.source(q["owner"], q["db"], q["table"], int(q["id"])))
                except KeyError as e:
                    if str(e).strip("'") in ("owner", "db", "table", "id"):
                        raise
                    return self._send(404, dict(error="not found"))
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
        if u.path == "/api/order":
            return self._order()
        if u.path == "/api/draft":
            return self._draft()
        if u.path == "/api/stock_answer":
            return self._stock_answer()
        if u.path == "/api/retry":
            return self._act("/api/retry")
        if u.path == "/api/delegate":
            return self._delegate()
        if u.path == "/api/chat":
            return self._chat()
        if u.path == "/api/chat/run":
            return self._chat_run()
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
                r = consult.answer(c, int(b["consultation_id"]), b["option"], b["idempotency_key"], b.get("reason"),
                                   change=b.get("change"), role=str(b.get("role") or "staff"))
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
                kind = "instruction" if b.get("kind") == "instruction" else "question"   # 選択肢にない進め方の指示（2026-11-05 デイブ）か、質問か
                eid = events.emit(c, "staff_question", dict(case_id=case["id"], text=text, key=key, kind=kind))
            finally:
                c.close()
            return self._send(200, dict(event_id=eid))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _order(self):
        """作業の途中で売り先の注文が入る（実演の操作）。注文と出来事 order_new を同じトランザクションで書く。売値は売り先の表から読む。"""
        try:
            b = self._body()
            owner, customer, item, qty, key = b["owner"], str(b["customer"]), str(b["item"]), b["quantity"], str(b["idempotency_key"])
            if owner not in db.OWNER_CODES or not isinstance(qty, int) or isinstance(qty, bool) or not 0 < qty <= 1000:
                raise ValueError("owner or quantity")
            c = db.connect(owner)
            try:
                for e in c.execute("SELECT payload_json FROM biz_event WHERE kind = 'order_new'"):
                    p = json.loads(e[0])
                    if p.get("key") == key:
                        return self._send(200, dict(order_id=p["order_id"], repeated=True))
                sp = c.execute("SELECT s.unit_price_yen FROM sales_price s JOIN customer cu ON cu.id = s.customer_id JOIN item i ON i.id = s.item_id "
                               "WHERE cu.name = ? AND i.code = ? ORDER BY s.valid_from DESC LIMIT 1", (customer, item)).fetchone()
                if sp is None:
                    raise ValueError(f"no sales price for {customer} / {item}")
                oid, eid = events.order_received(c, customer, item, qty, sp[0], sc.DEMO_DATE, key=key)
            finally:
                c.close()
            return self._send(200, dict(order_id=oid, event_id=eid, repeated=False))
        except (KeyError, ValueError, json.JSONDecodeError, events.EventError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _draft(self):
        """売り先への連絡の下書きを、人が使う（approve）か使わない（discard）か決める。Agent Dock は売り先に送らない。"""
        return self._act("/api/draft")

    def _act(self, path):
        """人の操作の受け付け（ボタン）。中身は dispatch（チャットの依頼の実行と同じ処理）。"""
        try:
            b = self._body()
            status, r = dispatch(b["owner"], path, b, str(b["idempotency_key"]) if path != "/api/draft" else str(b.get("idempotency_key") or ""))
            return self._send(status, r)
        except Rejected as e:
            return self._send(409, dict(error="rejected", detail=e.detail))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _stock_answer(self):
        """引当のない在庫の扱いの問いに、人が答える。答えと出来事は同じトランザクションで書く（AI が起きて続ける）。"""
        return self._act("/api/stock_answer")

    def _delegate(self):
        """担当者が今朝の注文と社長の指示を確かめ、任せる範囲を記録する。業務がまだ始まっていなければ、ここで始める（AI が働き始める）。"""
        try:
            b = self._body()
            if b["owner"] not in db.OWNER_CODES:
                raise ValueError("owner")
            item = b.get("item", "buri_xl")
            if item not in sc.SCENE_ORDERS:
                raise ValueError(item)
            c = db.connect(b["owner"])
            try:
                r = mate.delegate(c, sc.DEMO_DATE, b["scope"], b.get("checked") or {}, str(b["idempotency_key"]), str(b.get("role") or "staff"))
            finally:
                c.close()
            if not r["repeated"] and not live.has_started():
                scene.start(item=item)
                r["started"] = item
            return self._send(200, r)
        except mate.MateError as e:
            return self._send(409, dict(error="rejected", detail=str(e)))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _start(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n <= MAX_BODY else {}
            variant = b.get("variant", "normal")
            item = b.get("item", "aji")
            if variant not in scene.VARIANTS:
                raise ValueError(variant)
            if item not in sc.SCENE_ORDERS:
                raise ValueError(item)
            if live.has_started():
                return self._send(409, dict(error="rejected", detail="今日の業務はもう始まっています"))
            scene.apply_variant(variant)
            return self._send(200, dict(started=scene.start(variant=variant, item=item), variant=variant, item=item))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _decide(self):
        return self._act("/api/decide")

    # ── 仲間へのチャット ──
    def _chat(self):
        try:
            b = self._body()
            owner, item = b["owner"], str(b.get("item") or "buri_xl")
            if owner not in db.OWNER_CODES:
                raise ValueError("owner")
            if CHAT is None:
                return self._send(503, dict(error="unavailable", detail="チャットの呼び出し先がありません"))
            r = chat.ask(CHAT, owner, item, str(b["text"]), str(b["idempotency_key"]), sc.DEMO_DATE, run_kind=CHAT_KIND, run_enabled=CHAT_RUN)
            return self._send(200, dict(r, run_kind=CHAT_KIND, run_enabled=CHAT_RUN))
        except chat.ChatFailed as e:
            return self._send(502, dict(error="failed", detail=str(e), retry=True))
        except chat.ChatError as e:
            return self._send(400, dict(error="bad request", detail=str(e)))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))

    def _chat_run(self):
        """依頼の確認の後［この内容で頼む］：同じキーなら前の結果。今の画面に同じ操作が無ければ止める。中身はボタンと同じ dispatch。"""
        try:
            b = self._body()
            owner, tid, key = b["owner"], int(b["turn_id"]), str(b["idempotency_key"])
            if owner not in db.OWNER_CODES:
                raise ValueError("owner")
            if not CHAT_RUN:
                return self._send(403, dict(error="not_enabled", detail=CHAT_RUN_OFF))
            try:
                t = chat.turn(owner, tid)
            except KeyError:
                return self._send(404, dict(error="not found"))
            if t["executed"] and t["executed"]["key"] == key:
                return self._send(t["executed"]["status"], dict(t["executed"]["result"], repeated=True, via="chat", turn_id=tid))
            try:
                ck = chat.check_request(owner, tid, sc.DEMO_DATE)
            except chat.ChatError as e:
                return self._send(409, dict(error="rejected", detail=str(e)))
            api = ck["action"]["api"]
            try:
                status, r = dispatch(owner, api["path"], api["body"], key)
            except Rejected as e:
                status, r = 409, dict(error="rejected", detail=e.detail)
            chat.record_execution(owner, tid, key, status, r)
            return self._send(status, dict(r, via="chat", turn_id=tid, path=api["path"]))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._send(400, dict(error=f"bad request: {e}"))


THREAD_FIELDS = ("message_id", "from_owner", "frm", "to_owner", "to", "kind", "side", "quantity", "unit_price_yen", "valid_until",
                 "hold_state", "reason", "body", "in_reply_to", "created_at")


def _unit(owner: str, item: str) -> str | None:
    """品目の単位の書き方（本・kg・箱）。品目が無ければ None。"""
    c = db.connect(owner)
    try:
        r = c.execute("SELECT unit FROM item WHERE code = ?", (item,)).fetchone()
    finally:
        c.close()
    return sc.unit_label(r[0]) if r else None


def thread_view(owner: str, item: str, on_date: str) -> list[dict]:
    """owner が送ったか受け取った文だけを、市場の記録から読む（読むだけ）。条件ごとの仮押さえの状態も添える。"""
    out = []
    for m in agents.thread(owner, item, on_date):
        d = {k: m[k] for k in THREAD_FIELDS}
        d["terms"] = [dict(term_id=t["term_id"], quantity=t["quantity"], unit_price_yen=t["unit_price_yen"], valid_until=t["valid_until"],
                           status=t["status"], hold_state=t["hold_state"]) for t in m["term_list"]]
        out.append(d)
    return out


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
    global WORKER, CHAT, CHAT_KIND
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
        CHAT, CHAT_KIND = companion.ClaudeClient(effort=chat.EFFORT), "real"   # チャットも本物の Claude（同じ鍵の読み方）
    else:
        from agentdock import chatmock
        CHAT, CHAT_KIND = chatmock.ChatPolicy(), "mock"
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
