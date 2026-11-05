"""岩瀬漁協の AI の画面（読むだけ・2026-11-05 SAL の方針）。

漁協の DB と市場の記録から、漁協の仕事を時刻の順に並べる。演出だけの進行は出さず、行はすべて記録から作る：
  せりの結果の公開（全員に同じ結果・照合番号の検査）／水揚げと見通しの資料の配信（全員に同じ資料・照合番号の検査）
  ／シロエビの入札（受付は未接続）／照会への回答（AI の起動と道具の呼び出し・仮押さえ）／取引の登録と在庫

漁協の最低の値は、この画面（漁協の画面）にだけ出す。仲買の画面には出さない。
"""
import json

from agentdock import broadcast, db, mate, results, settle
from agentdock.seed import scenario as sc

OWNER = "iwase"
KIND_LABEL = {"fact": "事実", "calc": "コードの計算", "ai": "AI の解釈", "check": "コードの検査"}
ORIGIN_LABEL = {"real": "本物の Claude", "mock": "模擬の AI", "code": "コード", "record": "記録"}
HOLD = {"held": "押さえ中", "confirmed": "取引で確定", "released": "解除", "expired": "期限切れ"}
MAX_TEXT = 400


def _name(code: str) -> str:
    return sc.OWNERS.get(code, {}).get("name", code)


def _cut(s) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "…"


def _row(at, work, title, kind, origin, lines=(), ref=None, **extra):
    return dict(at=at, work=work, title=title, kind=kind, kind_label=KIND_LABEL[kind], origin=origin, origin_label=ORIGIN_LABEL[origin],
                lines=[l for l in lines if l], ref=ref, **extra)


def view(on_date: str = sc.DEMO_DATE) -> dict:
    con = db.connect(OWNER)
    m = db.connect(db.MARKET)
    try:
        units = {r["code"]: sc.unit_label(r["unit"]) for r in con.execute("SELECT code, unit FROM item")}
        names = {r["code"]: r["name"] for r in con.execute("SELECT code, name FROM item")}
        landing = [dict(item=r["name"], quantity=r["quantity"], unit=sc.unit_label(r["unit"]), size=r["size_note"], caught_at=r["caught_at"], area=r["area"])
                   for r in con.execute("SELECT i.name, i.unit, l.quantity, l.size_note, l.caught_at, l.area FROM landing l JOIN item i ON i.id = l.item_id "
                                        "WHERE l.landing_date = ? AND l.kind = 'actual' ORDER BY i.id", (on_date,))]
        rows = []
        # せりの結果の公開：市場の記録と、漁協・仲買の全員が受け取った公開の部分の照合番号
        market = [dict(r) for r in m.execute("SELECT result_id, lot_no, item_code AS item, buyer, unit_price_yen, quantity, trade_date FROM auction_result "
                                             "WHERE trade_date = ? ORDER BY result_id", (on_date,))]
        if market:
            fp = results.fingerprint([results._public(r) for r in market])
            got = {}
            for who in (results.SELLER, *results.BUYERS):
                c = db.connect(who)
                try:
                    pub = [dict(r, trade_date=on_date) for r in results._received(c, on_date)]
                    at = c.execute("SELECT min(received_at) FROM auction_result_in WHERE result_id LIKE ?", (f"{on_date}-%",)).fetchone()[0]
                finally:
                    c.close()
                got[who] = (results.fingerprint([results._public(r) for r in pub]) if pub else None, at)
            same = all(f == fp for f, _ in got.values())
            rows.append(_row(got[OWNER][1], "せりの結果の公開", f"結果 {len(market)}件を、漁協と仲買の全員へ同じ内容で送りました", "fact", "code",
                             ["・".join(f"{r['lot_no']} {names.get(r['item'], r['item'])} {r['quantity']}{units.get(r['item'], '')} × {r['unit_price_yen']:,}円"
                                       for r in market)],
                             dict(db="market", table="auction_result", id=market[0]["result_id"])))
            rows.append(_row(got[OWNER][1], "せりの結果の公開", "照合番号の検査：" + ("全員が受け取った内容は同じです" if same and results.verify(on_date) else "受け取った内容が違う相手があります"),
                             "check", "code", [f"{_name(w)}：{(f or '未着')[:12]}" for w, (f, _) in got.items()] + [f"市場の記録：{fp[:12]}"],
                             ok=bool(same and results.verify(on_date))))
        # 水揚げと見通しの資料の配信：1通を全員へ・届けた照合番号の検査
        for b in con.execute("SELECT * FROM broadcast ORDER BY id"):
            body = json.loads(b["body_json"])
            dels = [dict(r) for r in con.execute("SELECT recipient, delivered_at, fingerprint FROM broadcast_delivery WHERE broadcast_id = ? ORDER BY id", (b["id"],))]
            rows.append(_row(b["created_at"], "資料の配信", f"「{b['title']}」を仲買 {len(dels)}社へ同じ内容で配信しました", "fact", "code",
                             ["・".join(f"{i['name']} {i['quantity']}{sc.unit_label(i['unit'])}" for i in body.get("items") or []),
                              "宛先：" + "・".join(_name(d["recipient"]) for d in dels)], dict(db=OWNER, table="broadcast", id=b["id"])))
            ok = broadcast.verify(b["id"])
            rows.append(_row(b["created_at"], "資料の配信", "照合番号の検査：" + ("全員に同じ資料が届いています" if ok else "届いた資料が違う相手があります"), "check", "code",
                             [f"{_name(d['recipient'])}：{d['fingerprint'][:12]}" for d in dels] + [f"作った資料：{b['fingerprint'][:12]}"], ok=ok))
        # シロエビの入札：市場の入札の記録が無ければ、受付は未接続
        for code in [r["code"] for r in con.execute("SELECT DISTINCT i.code FROM landing l JOIN item i ON i.id = l.item_id WHERE l.landing_date = ? AND i.code = ?",
                                                    (on_date, sc.TODAY_TENDER["item"]))]:
            t = m.execute("SELECT t.id, t.status, t.opens_at, t.closes_at, l.lot_no FROM tender t JOIN lot l ON l.id = t.lot_id WHERE l.item_code = ? AND l.trade_date = ?",
                          (code, on_date)).fetchone()
            first_bc = con.execute("SELECT min(created_at) FROM broadcast").fetchone()[0]
            if not t and not first_bc:   # 業務が始まる前（資料を配っていない）は、入札の行も出さない
                continue
            rows.append(_row(t["opens_at"] if t else first_bc, "入札", f"{names[code]}の入札：" + (f"受付 {t['status']}（{t['lot_no']}）" if t else "入札の受付は未接続です"),
                             "fact", "record", ["市場の入札の記録はありません。今回は資料の配信までを扱います" if not t else f"締切 {t['closes_at']}"], connected=bool(t)))
        # 照会への回答：届いた問い合わせ → AI の起動（道具の呼び出し）→ 返事 → 仮押さえ
        for e in con.execute("SELECT b.id, b.at, b.payload_json, r.id AS run_id, r.started_at, r.outcome, r.run_kind, r.model FROM biz_event b "
                             "LEFT JOIN agent_run r ON r.event_id = b.id WHERE b.kind = 'quote_request' ORDER BY b.id"):
            p = json.loads(e["payload_json"])
            ask = m.execute("SELECT * FROM network_message WHERE id = ?", (p.get("message_id"),)).fetchone()
            if ask:
                u = units.get(ask["item_code"], "")
                rows.append(_row(ask["created_at"], "照会への回答", f"{_name(ask['from_owner'])}から問い合わせ：{names.get(ask['item_code'], ask['item_code'])} {ask['quantity']}{u}"
                                 + (f" × {ask['unit_price_yen']:,}円" if ask["unit_price_yen"] else ""), "fact", "record", [f"文：{ask['body']}"],
                                 dict(db="market", table="network_message", id=ask["id"])))
            if e["run_id"]:
                steps = [dict(s) for s in con.execute("SELECT seq, tool, input_json, result_text, is_error FROM agent_step WHERE run_id = ? ORDER BY seq", (e["run_id"],))]
                floor = next((json.loads(s["result_text"]).get("floor_yen") for s in steps if s["tool"] == "read_stock" and not s["is_error"]
                              and (s["result_text"] or "").startswith("{")), None)
                avail = next((json.loads(s["result_text"]).get("available") for s in steps if s["tool"] == "read_stock" and not s["is_error"]
                              and (s["result_text"] or "").startswith("{")), None)
                origin = "real" if e["run_kind"] == "real" else "mock"
                rows.append(_row(e["started_at"], "照会への回答", f"AI が在庫と販売の条件を読み、返事を組みました（道具 {len(steps)}回）", "ai", origin,
                                 ["道具：" + " → ".join(s["tool"] + ("（誤り）" if s["is_error"] else "") for s in steps),
                                  f"在庫を読んだ結果：出せる数 {avail}・自社の最低の値 {floor:,}円（漁協の画面にだけ表示）" if floor is not None else ""],
                                 dict(db=OWNER, table="agent_run", id=e["run_id"]),
                                 steps=[dict(seq=s["seq"], tool=s["tool"], is_error=bool(s["is_error"]), input=_cut(s["input_json"]), result=_cut(s["result_text"]))
                                        for s in steps], model=e["model"]))
            for rep in m.execute("SELECT * FROM network_message WHERE from_owner = ? AND in_reply_to = ? ORDER BY id", (OWNER, p.get("message_id"))):
                terms = [dict(t) for t in m.execute("SELECT * FROM offer_term WHERE message_id = ? ORDER BY id", (rep["id"],))]
                u = units.get(rep["item_code"], "")
                what = {"offer": "条件の返事", "decline": "断りの返事"}.get(rep["kind"], rep["kind"])
                rows.append(_row(rep["created_at"], "照会への回答", f"{_name(rep['to_owner'])}へ{what}", "ai", "real" if e["run_kind"] == "real" else "mock",
                                 [f"文：{rep['body']}"] + [f"条件（コードが記録）：{t['quantity']}{u} × {t['unit_price_yen']:,}円・期限 {t['valid_until'][11:16]}"
                                                           + (f"・仮押さえ {t['hold_ref']}" if t["hold_ref"] else "") for t in terms],
                                 dict(db="market", table="network_message", id=rep["id"])))
        for h in con.execute("SELECT h.*, i.code, i.name FROM stock_hold h JOIN stock s ON s.id = h.stock_id JOIN item i ON i.id = s.item_id ORDER BY h.id"):
            u = units.get(h["code"], "")
            rows.append(_row(h["updated_at"] if h["status"] != "held" else h["created_at"], "照会への回答",
                             f"仮押さえ {h['id']}：{_name(h['buyer'])}に {h['name']} {h['quantity']}{u} × {h['unit_price_yen']:,}円 → {HOLD.get(h['status'], h['status'])}",
                             "fact", "code", [f"期限 {h['expires_at'][11:16]}（ほかの仲買には、この数を出さない）"], dict(db=OWNER, table="stock_hold", id=h["id"])))
        # 取引の登録と在庫：市場の帳簿に載った漁協の売り
        for t in m.execute("SELECT t.*, l.item_code FROM trade t JOIN lot l ON l.id = t.lot_id WHERE t.seller = ? ORDER BY t.id", (OWNER,)):
            u = units.get(t["item_code"], "")
            sl = m.execute("SELECT 1 FROM seller_ledger WHERE trade_id = ?", (t["trade_id"],)).fetchone()
            bl = m.execute("SELECT 1 FROM buyer_ledger WHERE trade_id = ?", (t["trade_id"],)).fetchone()
            rows.append(_row(t["created_at"], "取引と在庫", f"取引 {t['trade_id']}：{_name(t['buyer'])}へ {names.get(t['item_code'], t['item_code'])} {t['quantity']}{u} × {t['unit_price_yen']:,}円",
                             "fact", "code", [f"状態：{t['status']}・" + ("売り手と買い手の両方の帳簿にあります" if sl and bl else "帳簿は片側だけ")],
                             dict(db="market", table="trade", id=t["id"])))
        for s in con.execute("SELECT s.*, i.name, i.code FROM stock s JOIN item i ON i.id = s.item_id WHERE s.trade_id IS NOT NULL ORDER BY s.id"):
            rows.append(_row(s["updated_at"], "取引と在庫", f"在庫 {s['name']} {s['quantity']}{units.get(s['code'], '')}：売れました（取引 {s['trade_id']}）",
                             "fact", "code", [], dict(db=OWNER, table="stock", id=s["id"])))
        left = [dict(item=r["name"], quantity=r["quantity"], unit=units.get(r["code"], "")) for r in con.execute(
            "SELECT i.name, i.code, s.quantity FROM stock s JOIN item i ON i.id = s.item_id WHERE s.status = 'for_sale' ORDER BY s.id")]
    finally:
        con.close()
        m.close()
    order = {"せりの結果の公開": 0, "資料の配信": 1, "入札": 2, "照会への回答": 3, "取引と在庫": 4}
    rows.sort(key=lambda r: (r["at"] is None, (r["at"] or "")[:19], order[r["work"]]))
    kinds = sorted({r["origin"] for r in rows if r["origin"] in ("real", "mock")})
    ck = mate.clock("amiya", on_date)   # 仲間の画面と同じ模擬の時計（網谷水産の委任を朝に合わせる）で時刻を出す
    for r in rows:
        r["at_shown"] = mate.shown_time(r["at"], ck)
    jobs, head, stance = _jobs(on_date, ck)
    return dict(owner=OWNER, owner_name=_name(OWNER), date=on_date, landing=landing, for_sale=left, rows=rows, clock=ck,
                jobs=jobs, headline=head, stance=stance, settle_note=settle.NOTE,
                ai_origins=[ORIGIN_LABEL[k] for k in kinds], kind_labels=KIND_LABEL, origin_labels=ORIGIN_LABEL)


STATE = {"waiting": "待ち", "running": "処理中", "done": "完了", "mismatch": "不一致", "idle": "待機", "answering": "対応中", "answered": "返事済み",
         "pending": "追加集計待ち", "reflected": "反映済み"}
PARTY = {"other": "ほかの仲買（Agent Dock なし）"}


def _jobs(on_date: str, ck: dict):
    """仕事のカード（並行して動く仕事ごと）・見出し・表情。件数と状態は集計の表と起動の記録から数える（画面のための数は作らない）。"""
    st = settle.status(on_date)
    con = db.connect(OWNER)
    m = db.connect(db.MARKET)
    try:
        cons = {str(r[0]): r[1] for r in con.execute("SELECT id, name FROM consignor")}
        totals = {k: [(r[0], r[1], r[2]) for r in con.execute("SELECT party, sum(amount_yen), count(*) FROM settle_line WHERE kind = ? GROUP BY party ORDER BY party", (k,))]
                  for k in ("invoice", "payout")}
        checks = [dict(r) for r in con.execute("SELECT who, min(ok) AS ok, count(*) AS n FROM settle_check GROUP BY who ORDER BY min(id)")]
        run = con.execute("SELECT r.id, r.started_at, r.run_kind, b.payload_json FROM agent_run r JOIN biz_event b ON b.id = r.event_id "
                          "WHERE b.kind = 'quote_request' AND r.ended_at IS NULL ORDER BY r.id DESC LIMIT 1").fetchone()
        tools = [s["tool"] for s in con.execute("SELECT tool FROM agent_step WHERE run_id = ? ORDER BY seq", (run["id"],))] if run else []
        ask = m.execute("SELECT * FROM network_message WHERE id = ?", (json.loads(run["payload_json"]).get("message_id"),)).fetchone() if run else None
        got = m.execute("SELECT count(*) FROM network_message WHERE to_owner = ? AND kind = 'inquiry'", (OWNER,)).fetchone()[0]
        sent = m.execute("SELECT count(*) FROM network_message WHERE from_owner = ? AND kind IN ('offer', 'decline')", (OWNER,)).fetchone()[0]
        kinds = {r[0] for r in con.execute("SELECT run_kind FROM agent_run")}
    finally:
        con.close()
        m.close()
    j = st["jobs"]
    def card(key, title, who, status, done, total_, lines, ref):
        return dict(key=key, title=title, who=who, who_label={"code": "コードが処理", "ai": "AI（Claude）が対応"}[who], status=status,
                    status_label=STATE[status], done=done, total=total_, lines=lines, ref=ref)
    cards = []
    if st["started"]:
        r = j["reconcile"]
        cards.append(card("reconcile", "せりの結果の照合", "code", r["status"], r["done"], r["total"],
                          [("✓ " if c["ok"] else "× ") + f"{_name(c['who'])}（{c['n']}件）" for c in checks], dict(table="settle_check")))
        for k, title, name in (("invoice", "仲買への請求の集計", lambda p: PARTY.get(p, _name(p))), ("payout", "荷主への支払の集計", lambda p: cons.get(p, p))):
            x = j[k]
            cards.append(card(k, title, "code", x["status"], x["done"], x["total"],
                              [f"{name(p)}：{amt:,}円（{n}件）" for p, amt, n in totals[k]], dict(table="settle_line", kind=k)))
    cards.append(card("inquiry", "照会への回答", "ai", "answering" if run else ("answered" if sent else "idle"), sent, got,
                      ([f"{_name(ask['from_owner'])}から {ask['quantity']}本の問い合わせ"] if ask else [])
                      + ([" → ".join(tools) + " …"] if run else []), dict(table="agent_run")))
    waiting = st.get("waiting") or []
    reflected = [s for s in settle.sources(on_date) if s["source_kind"] == "trade" and s not in waiting] if st["started"] else []
    cards.append(card("additional", "取引と追加集計", "code", "pending" if waiting else ("reflected" if reflected else "idle"), len(reflected),
                      len(reflected) + len(waiting),
                      [f"{s['source_id']}：{_name(s['buyer'])}へ {s['quantity']}本 × {s['unit_price_yen']:,}円・" + ("追加集計待ち" if s in waiting else "反映済み")
                       for s in [*reflected, *waiting]], dict(table="settle_run", additional=1)))
    # 見出しと表情（記録から決める）
    if any(c["status"] == "mismatch" for c in cards):
        head, stance = "照合で食い違いが見つかりました。請求と支払は止めています。", "struggle"
    elif run and ask:
        busy = "・".join(c["title"] for c in cards if c["status"] == "running")
        head = f"{_name(ask['from_owner'])}から {ask['quantity']}本の問い合わせです。在庫と販売の条件を確かめています。" + (f"（{busy}も続けています）" if busy else "")
        stance = "careful"
    elif waiting:
        head, stance = "取引が新しく成立しました。追加の集計に入れます。", "careful"
    elif any(c["status"] == "running" for c in cards):
        head, stance = "せりの後の照合と集計を進めています。", "calm"
    elif st.get("complete"):
        head, stance = "今日の請求と支払の集計案がそろいました。", "relief"
    else:
        head, stance = "業務の開始を待っています。", "calm"
    return cards, head, stance
