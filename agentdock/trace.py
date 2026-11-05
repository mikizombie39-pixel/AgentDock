"""一つの取引の証拠のたどり（技術の検証の画面の1節・2026-11-05 SAL の方針）。

登録した案件1件について、次の順に、業務 DB と市場の記録の行を1本の線で並べる（画面のための文は作らず、記録の値を並べる）：
  1 取引 → 2 案件の決定（誰が・どの案・理由）→ 3 案（根拠の Vault の識別子）→ 4 Vault のファイル
  → 5 Claude の起動と道具の入出力 → 6 漁協・B社とのメッセージ → 7 市場の取引帳簿 → 8 在庫と注文の変化

各行の印：
  kind    fact（事実の記録）・calc（コードの計算）・ai（AI の解釈・AI が書いた文）・experience（過去の経験＝Vault）・human（人の判断）
  origin  real（本物の Claude）・mock（模擬の AI）・person（人の操作）・code（コード）・record（記録）
"""
import hashlib
import json

from agentdock import db, deal, vault
from agentdock.seed import scenario as sc

KIND_LABEL = {"fact": "事実", "calc": "コードの計算", "ai": "AI の解釈", "experience": "過去の経験", "human": "人の判断"}
ORIGIN_LABEL = {"real": "本物の Claude", "mock": "模擬の AI", "person": "人の操作", "code": "コード", "record": "記録"}
STEPS = {1: "取引", 2: "案件の決定", 3: "案と根拠", 4: "Vault のファイル", 5: "Claude の起動と道具", 6: "漁協・B社とのメッセージ",
         7: "市場の取引帳簿", 8: "在庫と注文の変化"}
ACTION = {"approve": "承認", "hold": "保留", "reject": "見送り", "return": "差し戻し"}
RESULT = {"registered": "登録", "needs_president": "社長の確認へ", "returned": "担当者へ戻した", "held": "保留", "rejected": "見送り",
          "partial": "一部だけ登録", "stale": "古い案", "incomplete": "登録が途中"}
WHO = {"staff": "担当者", "president": "社長（網谷さん）"}
MAX_TEXT = 400


def _name(code: str) -> str:
    return sc.OWNERS.get(code, {}).get("name", code)


def _cut(s) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "…"


def _row(step, at, title, kind, origin, lines=(), ref=None, **extra):
    return dict(step=step, step_label=STEPS[step], at=at, title=title, kind=kind, kind_label=KIND_LABEL[kind],
                origin=origin, origin_label=ORIGIN_LABEL[origin], lines=[l for l in lines if l], ref=ref, **extra)


def _origin(run_kind) -> str:
    return "real" if run_kind == "real" else "mock"


def latest_registered_case(con) -> int | None:
    r = con.execute("SELECT case_id FROM case_decision WHERE result IN ('registered', 'partial') ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None


def trace(owner: str = "amiya", case_id: int | None = None) -> dict:
    """登録した案件（既定は最後に登録した案件）の証拠のたどり。登録した案件が無ければ rows は空。"""
    if owner not in db.OWNER_CODES:
        raise ValueError(owner)
    con = db.connect(owner)
    m = db.connect(db.MARKET)
    try:
        case_id = case_id or latest_registered_case(con)
        if case_id is None:
            return dict(owner=owner, case_id=None, rows=[], note="登録した取引はまだありません")
        case = con.execute("SELECT c.*, i.code AS item_code, i.name AS item_name, i.unit FROM deal_case c JOIN item i ON i.id = c.item_id WHERE c.id = ?",
                           (case_id,)).fetchone()
        if case is None:
            raise KeyError(case_id)
        u = sc.unit_label(case["unit"])
        shops = {r[0]: r[1] or r[2] for r in con.execute("SELECT id, shop_name, name FROM customer")}
        rows = []
        decisions = [dict(r) for r in con.execute("SELECT * FROM case_decision WHERE case_id = ? ORDER BY id", (case_id,))]
        final = next((d for d in reversed(decisions) if d["result"] in ("registered", "partial")), None)
        props = [dict(r) for r in con.execute("SELECT * FROM proposal WHERE case_id = ? AND option_id IS ? AND status = 'registered' ORDER BY id",
                                              (case_id, final["option_id"] if final else None))]
        # 1 取引（登録した仕入れ・売り）と合計（コードの計算）
        for p in props:
            rows.append(_row(1, p["decided_at"], f"{_name(p['counterparty'])}から {p['quantity']}{u} を仕入れ（1{u} {p['unit_price_yen']:,}円）"
                             if p["side"] == "buy" else f"{_name(p['counterparty'])}へ {p['quantity']}{u} を売り（1{u} {p['unit_price_yen']:,}円）",
                             "fact", "record", [f"取引 {p['trade_id']}・{case['item_name']}・{p['trade_date']}"],
                             dict(db="owner", table="proposal", id=p["id"])))
        if props:
            total = sum(p["quantity"] * p["unit_price_yen"] for p in props if p["side"] == "buy")
            rows.append(_row(1, final["at"], f"仕入れの合計 {total:,}円（{sum(p['quantity'] for p in props if p['side'] == 'buy')}{u}）", "calc", "code",
                             ["・".join(f"{p['quantity']}{u} × {p['unit_price_yen']:,}円 = {p['quantity'] * p['unit_price_yen']:,}円" for p in props
                                       if p["side"] == "buy")]))
        # 2 案件の決定（人の操作の記録・誰が・どの案・理由）
        labels = {r["id"]: r["label"] for r in con.execute("SELECT id, label FROM board_option WHERE case_id = ?", (case_id,))}
        for d in decisions:
            det = json.loads(d["detail"] or "{}")
            lines = [f"案：{labels.get(d['option_id'], '—')}", f"結果：{RESULT.get(d['result'], d['result'])}"]
            if det.get("reason"):
                lines.append(f"理由：{det['reason']}")
            if det.get("priority_change"):
                lines.append("社長の朝の指示と違う配分のため、社長の確認が要る（コードが判定）")
            a = det.get("authority") or {}
            if a.get("amount_yen") is not None:
                lines.append(f"権限の確認：{a['amount_yen']:,}円・{WHO.get(a.get('role'), a.get('role'))}の上限 {a.get('limit_yen') or 0:,}円（コードが判定）")
            rows.append(_row(2, d["at"], f"{WHO.get(d['actor'], d['actor'])}が{ACTION.get(d['action'], d['action'])}", "human", "person", lines,
                             dict(db="owner", table="case_decision", id=d["id"])))
        # 3 案と根拠（AI が書いた文・コードが計算した数・根拠の Vault の識別子）
        refs, opt = [], None
        if final and final["option_id"]:
            opt = dict(con.execute("SELECT * FROM board_option WHERE id = ?", (final["option_id"],)).fetchone())
            turn = con.execute("SELECT t.layout_json, t.run_id, t.at, r.run_kind FROM workspace_turn t LEFT JOIN agent_run r ON r.id = t.run_id "
                               "WHERE t.case_id = ? AND t.version = ? AND t.source = 'present' ORDER BY t.id DESC LIMIT 1",
                               (case_id, opt["version"])).fetchone()
            layout = json.loads(turn["layout_json"]) if turn else {}
            refs = (layout.get("vault_refs") or {}).get(opt["code"]) or []
            rows.append(_row(3, turn["at"] if turn else None, f"案 {opt['code']}「{opt['label']}」（版 {opt['version']}）", "ai",
                             _origin(turn["run_kind"]) if turn else "record",
                             [f"理由：{opt['reason']}", f"気をつけること：{opt['risk']}",
                              f"根拠の Vault：{'・'.join(str(v) for v in refs) if refs else 'なし'}（番号はコードが Vault にあることを確かめた）",
                              f"案を出した Claude の起動：{turn['run_id']}" if turn else ""],
                             dict(db="owner", table="board_option", id=opt["id"])))
            n = json.loads(opt["numbers_json"])
            pur = n.get("purchase") or {}
            names = {r[0]: r[1] or r[0] for r in con.execute("SELECT name, shop_name FROM customer")}
            sales = "・".join(f"{names.get(l['to'], l['to'])} {l['quantity']}{u}" for l in n.get("lines") or [])
            rows.append(_row(3, turn["at"] if turn else None, f"案 {opt['code']} の数（コードが業務 DB から計算）", "calc", "code",
                             [f"仕入れ {pur.get('quantity')}{u}・{(pur.get('amount_yen') or 0):,}円", f"売り先：{sales}" if sales else "",
                              f"粗利の見込み {n['gross_profit_yen']:,}円" if n.get("gross_profit_yen") is not None else ""],
                             dict(db="owner", table="board_option", id=opt["id"])))
        # 4 Vault のファイル（DB の行と、ファイルの照合番号）
        for vid in refs:
            v = con.execute("SELECT * FROM vault_note WHERE id = ?", (vid,)).fetchone()
            if v is None:
                continue
            path = vault.root() / owner / (v["category"] or "") / f"{v['vault_id']}.md" if v["vault_id"] else None
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path and path.exists() else None
            rows.append(_row(4, None, f"{v['vault_id'] or '番号 ' + str(v['id'])}「{v['title']}」", "experience", "record",
                             [f"ファイル：vault/{owner}/{v['category']}/{v['vault_id']}.md" if v["vault_id"] else "",
                              f"照合番号（SHA-256 の先頭 12桁）：{digest}" if digest else "ファイルが見つかりません",
                              f"出所：{v['author']}・{v['written_on']}・適用：{v['applies_when'] or '—'}"],
                             dict(db="owner", table="vault_note", id=v["id"])))
        if opt is not None and not refs:
            rows.append(_row(4, None, "案は、根拠の Vault の番号を付けていません", "experience", "record",
                             ["番号を付けた時だけ、ここに Vault のファイルと照合番号が並びます"]))
        # 5 Claude の起動と道具の入出力（この取引の登録の出来事より前の、網谷水産の起動。時刻は同じ秒に並ぶことがあるので出来事の番号で切る）
        reg = next((e["id"] for e in con.execute("SELECT id, payload_json FROM biz_event WHERE kind = 'trade_registered' ORDER BY id")
                    if json.loads(e["payload_json"]).get("case_id") == case_id), None)
        for r in con.execute("SELECT r.*, e.kind AS ev FROM agent_run r LEFT JOIN biz_event e ON e.id = r.event_id "
                             "WHERE ? IS NULL OR r.event_id < ? ORDER BY r.id", (reg, reg)):
            steps = [dict(s) for s in con.execute("SELECT seq, tool, input_json, result_text, is_error FROM agent_step WHERE run_id = ? ORDER BY seq", (r["id"],))]
            rows.append(_row(5, r["started_at"], f"起動 {r['id']}：{r['ev'] or '—'} → {r['outcome'] or '動作中'}（道具 {len(steps)}回）", "ai",
                             _origin(r["run_kind"]), ["道具：" + " → ".join(s["tool"] + ("（誤り）" if s["is_error"] else "") for s in steps)],
                             dict(db="owner", table="agent_run", id=r["id"]),
                             steps=[dict(seq=s["seq"], tool=s["tool"], is_error=bool(s["is_error"]), input=_cut(s["input_json"]), result=_cut(s["result_text"]))
                                    for s in steps], model=r["model"]))
        # 6 漁協・B社とのメッセージ（仕入れの条件の元の返事と、その問い合わせ。返事を書いた相手の AI の起動）
        term_ids = [p["term_id"] for p in props if p["term_id"]]
        for tid in term_ids:
            t = m.execute("SELECT * FROM offer_term WHERE id = ?", (tid,)).fetchone()
            if t is None:
                continue
            reply = m.execute("SELECT * FROM network_message WHERE id = ?", (t["message_id"],)).fetchone()
            ask = m.execute("SELECT * FROM network_message WHERE id = ?", (reply["in_reply_to"],)).fetchone() if reply and reply["in_reply_to"] else None
            if ask:
                rows.append(_row(6, ask["created_at"], f"{_name(ask['from_owner'])} → {_name(ask['to_owner'])}：問い合わせ（{ask['quantity']}{u}）", "ai",
                                 _run_origin(owner, ask["id"], "send_inquiry"), [f"文：{ask['body']}"], dict(db="market", table="network_message", id=ask["id"])))
            if reply:
                rows.append(_row(6, reply["created_at"], f"{_name(reply['from_owner'])} → {_name(reply['to_owner'])}：条件の返事", "ai",
                                 _run_origin(reply["from_owner"], reply["in_reply_to"], "reply"),
                                 [f"文：{reply['body']}", f"条件（コードが記録）：{t['quantity']}{u} × {t['unit_price_yen']:,}円・期限 {t['valid_until'][11:16]}"
                                  + (f"・仮押さえ {t['hold_ref']}" if t["hold_ref"] else "")], dict(db="market", table="network_message", id=reply["id"])))
        # 7 市場の取引帳簿（取引・両方の承認・売り手と買い手の帳簿・照合）
        tids = [p["trade_id"] for p in props if p["trade_id"]]
        rec = {r["trade_id"]: r for r in deal.reconcile(tids)} if tids else {}
        for tid in tids:
            tr = m.execute("SELECT * FROM trade WHERE trade_id = ?", (tid,)).fetchone()
            if tr is None:
                continue
            appr = [r["party"] for r in m.execute("SELECT party FROM trade_approval WHERE trade_id = ? ORDER BY id", (tid,))]
            sl = m.execute("SELECT quantity, unit_price_yen FROM seller_ledger WHERE trade_id = ?", (tid,)).fetchone()
            bl = m.execute("SELECT quantity, unit_price_yen FROM buyer_ledger WHERE trade_id = ?", (tid,)).fetchone()
            r = rec.get(tid, {})
            rows.append(_row(7, tr["created_at"], f"取引 {tid}：{_name(tr['seller'])} → {_name(tr['buyer'])}・{tr['quantity']}{u} × {tr['unit_price_yen']:,}円",
                             "fact", "code",
                             [f"状態：{tr['status']}・承認：{'・'.join(_name(a) for a in appr) or 'なし'}",
                              f"売り手の帳簿：{sl['quantity']}{u} × {sl['unit_price_yen']:,}円" if sl else "売り手の帳簿：なし",
                              f"買い手の帳簿：{bl['quantity']}{u} × {bl['unit_price_yen']:,}円" if bl else "買い手の帳簿：なし",
                              "照合：両方の帳簿にあります" if r.get("seller_booked") and r.get("buyer_booked") else "照合：片側だけ・要照合"],
                             dict(db="market", table="trade", id=tr["id"]), booked=bool(r.get("seller_booked") and r.get("buyer_booked"))))
        # 8 在庫と注文の変化（仕入れた在庫の引当・注文の状態・売り手の仮押さえ）
        for tid in tids:
            for s in con.execute("SELECT s.id, s.quantity, s.status, s.order_id, s.updated_at, o.quantity AS oq, o.status AS ostatus, o.customer_id "
                                 "FROM stock s LEFT JOIN order_book o ON o.id = s.order_id WHERE s.trade_id = ? ORDER BY s.id", (tid,)):
                if s["status"] == "allocated":
                    title = f"在庫 {s['quantity']}{u} を {shops.get(s['customer_id'], '—')}の注文（{s['oq']}{u}）に引当"
                    line = f"注文の状態：{ {'filled': '充足', 'partly_filled': '一部充足', 'open': '未充足'}.get(s['ostatus'], s['ostatus']) }"
                else:
                    title, line = f"在庫 {s['quantity']}{u}（引当のない分）", f"状態：{s['status']}"
                rows.append(_row(8, s["updated_at"], title, "fact", "code", [f"取引 {tid}", line], dict(db="owner", table="stock", id=s["id"])))
        for tid in term_ids:
            t = m.execute("SELECT seller, hold_ref FROM offer_term WHERE id = ?", (tid,)).fetchone()
            if not t or not t["hold_ref"]:
                continue
            sc_con = db.connect(t["seller"])
            try:
                h = sc_con.execute("SELECT id, quantity, status, expires_at FROM stock_hold WHERE id = ?", (t["hold_ref"],)).fetchone()
            finally:
                sc_con.close()
            if h:
                rows.append(_row(8, None, f"{_name(t['seller'])}の仮押さえ {h['quantity']}{u}：{ {'confirmed': '取引で確定', 'held': '押さえ中', 'released': '解除', 'expired': '期限切れ'}.get(h['status'], h['status']) }",
                                 "fact", "code", [f"{_name(t['seller'])}の業務 DB の行（stock_hold {h['id']}）"], dict(db=t["seller"], table="stock_hold", id=h["id"])))
    finally:
        con.close()
        m.close()
    kinds = sorted({r["origin"] for r in rows if r["origin"] in ("real", "mock")})
    return dict(owner=owner, case_id=case_id, item=dict(code=case["item_code"], name=case["item_name"], unit=u), rows=rows,
                ai_origins=[ORIGIN_LABEL[k] for k in kinds], kind_labels=KIND_LABEL, origin_labels=ORIGIN_LABEL)


def _run_origin(owner: str, ref_id, tool: str) -> str:
    """メッセージを送った AI の起動の種類。問い合わせは in_reply_to の無い send_inquiry、返事は in_reply_to が問い合わせの番号の reply で探す。
    見つからなければ record（記録だけ）。"""
    con = db.connect(owner)
    try:
        for s in con.execute("SELECT s.input_json, s.result_text, r.run_kind FROM agent_step s JOIN agent_run r ON r.id = s.run_id "
                             "WHERE s.tool = ? AND s.is_error = 0 ORDER BY s.id", (tool,)):
            try:
                inp = json.loads(s["input_json"] or "{}")
            except ValueError:
                continue
            if tool == "reply" and inp.get("in_reply_to") == ref_id:
                return _origin(s["run_kind"])
            if tool == "send_inquiry" and f"番号 {ref_id}）" in (s["result_text"] or ""):
                return _origin(s["run_kind"])
    finally:
        con.close()
    return "record"
