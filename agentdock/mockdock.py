"""仲間との対話の画面（mate.html）の確かめ用の通し：ブリ（模擬の Claude・費用 0）。

  python -m agentdock.mockdock --port 8794            # 新しい置き場に種を入れ、裏で模擬の仲間が出来事を処理する
  python -m agentdock.mockdock --port 8795 --variant buri_floor_up

本物と同じサーバー・同じ記録・同じ画面で、仲間の返事だけを決まった方針の模擬にする（撮影と本番には使わない）。
検証の画面には run_kind が mock と出る。方針は業務 DB を読んで道具の入力を決めるだけで、数字の判定・権限・開示の検査はコードが行う。
  網谷水産：注文 → 漁協と B社に照会 → 返事が全部そろったら案を出す（注文の全部を見て。社長の指示の売り先を先に）→ 登録の後は残りを相談
  岩瀬漁協：在庫の内で 2つの条件（少ない数・まとめて）を返す。在庫が無ければ断る
  B社：引当のない在庫の内で、最低の値で返す
"""
import argparse
import json
import os
import sys
import time

from agentdock import agents, db, live, network, scene, server, stockwork
from agentdock.companion import Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

U = dict(input_tokens=3000, output_tokens=400)
D = sc.DEMO_DATE


def _q(owner, sql, args=()):
    c = db.connect(owner)
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


class Policy:
    """1回の起動の最初の呼び出しで、DB を読んで道具の呼び出しの列を決め、1つずつ返す（待ち時間つき）。"""

    def __init__(self, owner: str, delay: float = 1.5):
        self.owner, self.delay, self.plan, self.n = owner, delay, [], 0

    def create(self, **params):
        time.sleep(self.delay)
        if len(params["messages"]) == 1:
            self.plan = getattr(self, f"_{self.owner}")(params["messages"][0]["content"])
        if not self.plan:
            return Reply(content=[{"type": "text", "text": "以上です。"}], stop_reason="end_turn", usage=U)
        calls = self.plan.pop(0)
        blocks = []
        for name, inp in calls:
            self.n += 1
            blocks.append({"type": "tool_use", "id": f"{self.owner}{self.n}", "name": name, "input": inp})
        return Reply(content=blocks, usage=U)

    # ── 網谷水産 ──
    def _amiya(self, prompt):
        item = "buri_xl" if "buri_xl" in prompt else None
        if item is None:
            return [ [("do_nothing", dict(reason="この品目は扱いません"))] ]
        B = {"item_code": item}
        msgs = agents.thread("amiya", item, D)
        open_q = [m for m in msgs if m["from_owner"] == "amiya" and m["kind"] == "inquiry" and not any(r["in_reply_to"] == m["message_id"] for r in msgs)]
        orders = _q("amiya", "SELECT o.id, o.quantity, o.received_at, c.name AS customer, c.shop_name FROM order_book o JOIN customer c ON c.id = o.customer_id "
                             "JOIN item i ON i.id = o.item_id WHERE i.code = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id", (item, D))
        for o in orders:
            o["remaining"] = o["quantity"] - _q("amiya", "SELECT coalesce(sum(quantity), 0) AS n FROM stock WHERE order_id = ? AND status = 'allocated'", (o["id"],))[0]["n"]
        need = sum(o["remaining"] for o in orders)
        asked = {m["to_owner"] for m in msgs if m["from_owner"] == "amiya" and m["kind"] == "inquiry"}
        live_terms = [t for m in msgs if m["to_owner"] == "amiya" and m["kind"] == "offer" for t in m["term_list"] if t["status"] == "offered"]
        if "出来事：注文が入りました" in prompt and not asked:            # 朝の注文：漁協と B社に照会する（別の仕入先がある）
            return [ [("read_orders", B), ("read_thread", B)],
                    [("send_inquiry", dict(to_owner="iwase", side="buy", item_code=item, quantity=need, unit_price_yen=17500, request_id=None,
                                           message=f"ブリを {need}本、1本 17,500円でお願いできますか。", reason="朝の注文の分です"))],
                    [("send_inquiry", dict(to_owner="buyer_b", side="buy", item_code=item, quantity=2, unit_price_yen=17800, request_id=None,
                                           message="ブリを 2本、1本 17,800円で譲っていただけますか。", reason="漁協と合わせて当たっています"))],
                    [("wait_for_replies", dict(reason="漁協と B社の返事を待つ"))] ]
        if open_q:                                                           # 返事が全部そろうまで待つ
            return [ [("read_thread", B)], [("wait_for_replies", dict(reason="ほかの返事を待つ"))] ]
        if need <= 0:
            return [ [("read_orders", B)], [("do_nothing", dict(reason="満たしていない注文はありません"))] ]
        if not live_terms and "出来事：取引を登録しました" in prompt:          # 登録の後の残り：当たれる先に問い合わせ直す
            return [ [("read_orders", B), ("read_thread", B)],
                    [("send_inquiry", dict(to_owner="iwase", side="buy", item_code=item, quantity=need, unit_price_yen=17500, request_id=None,
                                           message=f"追加で、ブリを {need}本お願いできますか。", reason="注文が増えたため"))],
                    [("send_inquiry", dict(to_owner="buyer_b", side="buy", item_code=item, quantity=min(need, 2), unit_price_yen=18000, request_id=None,
                                           message=f"ブリを {min(need, 2)}本、譲っていただけますか。", reason="注文が増えたため"))],
                    [("wait_for_replies", dict(reason="返事を待つ"))] ]
        if not live_terms:                                                  # 当てが無い：断られた
            return [ [("read_thread", B)], [("do_nothing", dict(reason="当たれる先の返事が断りでした。次に当たる先を担当者と相談します"))] ]
        last_present = _q("amiya", "SELECT max(t.at) AS at FROM workspace_turn t WHERE t.source = 'present'")[0]["at"]
        newest = max([m["created_at"] for m in msgs] + [o["received_at"] for o in orders if o.get("received_at")])
        if last_present and last_present[:19] >= newest[:19]:   # 出した案の後に新しい返事も注文も無い：出し直さない
            return [ [("read_thread", B)], [("do_nothing", dict(reason="出した案の後に、新しい返事も注文もありません"))] ]
        return [ [("read_orders", B), ("read_thread", B), ("read_vault", B)], [self._present(item, orders, live_terms)] ]

    def _present(self, item, orders, terms):
        keep = next((o for o in orders if o["customer"] == "スーパー"), None)   # 社長の指示の売り先（区分の名前）
        rest = [o for o in orders if o is not keep and o["remaining"] > 0]
        need = sum(o["remaining"] for o in orders)
        fit = [t for t in terms if t["quantity"] <= need]   # 注文より多くは仕入れない（余りの出る条件は選ばない）
        big = max(fit or terms, key=lambda t: (t["quantity"], -t["unit_price_yen"]))
        name = lambda o: o["shop_name"] or o["customer"]   # noqa: E731
        opts, n = [], big["quantity"]
        alloc_a, left = [], n
        for o in ([keep] if keep and keep["remaining"] > 0 else []) + rest:
            k = min(left, o["remaining"])
            if k > 0:
                alloc_a.append(dict(kind="order", ref_id=o["id"], customer=None, quantity=k))
                left -= k
        lbl = "・".join(f"{name(o)}{a['quantity']}" for a in alloc_a for o in orders if o["id"] == a["ref_id"])
        opts.append(dict(code="A", label=f"漁協から {n}本（{lbl}）", short_label=lbl[:16], buy_term_id=big["term_id"], allocations=alloc_a,
                         reason="社長の朝の指示の売り先を先にそろえる", risk="ほかの注文が足りない時は、残りを別の仕入先で探す",
                         differs_from_decision=False, vault_refs=[]))
        short_after = sum(o["remaining"] for o in orders) - n
        if rest and keep and keep["remaining"] > 0 and short_after > 0:
            r = rest[0]
            k = min(n, r["remaining"])
            opts.append(dict(code="B", label=f"漁協から {n}本（{name(r)}に{k}）", short_label=f"{name(r)}に{k}本"[:16], buy_term_id=big["term_id"],
                             allocations=[dict(kind="order", ref_id=r["id"], customer=None, quantity=k)],
                             reason="追加の注文を先にそろえる", risk="社長の指示の売り先が欠ける（社長の確認が要る）", differs_from_decision=False, vault_refs=[]))
        opts.append(dict(code="E", label="見送る", short_label="見送る", buy_term_id=None, allocations=[], reason="条件が合わない", risk="注文を落とす",
                         differs_from_decision=False, vault_refs=[]))
        if short_after > 0 and rest:
            sq = f"{name(rest[0])}の追加で足りなくなりました。どう分けますか"[:40]   # 数はコードが画面に出す（文に数字を書かない）
            head = "注文が増え、今の当てでは足りません"
        else:
            sq = "この内容で登録しますか"
            head = "注文の分がそろいます"
        return ("present_options", dict(item_code=item, summary=head, headline=head, short_question=sq, ask_human=sq,
                                        cards=[dict(card_type="order", ref_id=o["id"], note=None) for o in orders][:4],
                                        options=opts, recommended="A", alternative="B" if len(opts) > 2 else None,
                                        actions=["approve", "compare", "ask"], suggested_questions=[]))

    # ── 岩瀬漁協 ──
    def _iwase(self, prompt):
        mid, item = _message(prompt)
        st = _q("iwase", "SELECT coalesce(sum(s.quantity), 0) AS n FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = ? AND s.status = 'for_sale'", (item,))[0]["n"]
        B = {"item_code": item}
        m = network.message_for("iwase", mid)
        floor = _q("iwase", "SELECT base_floor_yen + hold_up_yen AS f FROM seller_rule r JOIN item i ON i.id = r.item_id WHERE i.code = ?", (item,))[0]["f"]
        free = st   # 同じ相手の前の押さえは、新しい返事に置き換わる
        if free <= 0:
            return [ [("read_stock", B)], [("reply", dict(in_reply_to=mid, kind="decline", terms=[], message="あいにく、今日の相対の残りはありません。",
                                                       reason="今朝の相対の残りが無くなりました"))] ]
        q1 = min(m["quantity"] or free, free)
        p1 = max(floor, (m["unit_price_yen"] or floor) + 200)
        terms = [dict(quantity=q1, unit_price_yen=p1)]
        if free > q1:
            terms.append(dict(quantity=free, unit_price_yen=max(floor, p1 - 200)))
        return [ [("read_stock", B), ("read_thread", B)],
                [("reply", dict(in_reply_to=mid, kind="offer", terms=terms, message="条件をお送りします。まとめてなら値を下げられます。",
                                reason=f"今朝の相対の残りは {free}本です"))] ]

    # ── B社 ──
    def _buyer_b(self, prompt):
        if "問い合わせが届きました" not in prompt:
            return [ [("do_nothing", dict(reason="自社の動く出来事ではありません"))] ]
        mid, item = _message(prompt)
        c = db.connect("buyer_b")
        try:
            it = c.execute("SELECT id FROM item WHERE code = ?", (item,)).fetchone()
            # 同じ相手への前の押さえは、新しい返事に置き換わる（ほかの仲買の押さえだけを引く）
            free = sum(r["quantity"] for r in stockwork.unallocated(c, it["id"])) if it else 0
            free -= c.execute("SELECT coalesce(sum(h.quantity), 0) FROM stock_hold h JOIN stock s ON s.id = h.stock_id WHERE s.item_id = ? AND h.status = 'held' "
                              "AND h.buyer != 'amiya' AND h.expires_at > ?", (it["id"], agents.demo_now(D))).fetchone()[0] if it else 0
            fl = stockwork.resale_floor(c, it["id"]) if it else None
        finally:
            c.close()
        B = {"item_code": item}
        if not free or not fl:
            return [ [("read_stock", B)], [("reply", dict(in_reply_to=mid, kind="decline", terms=[], message="あいにく、お譲りできる分がありません。", reason=None))] ]
        m = network.message_for("buyer_b", mid)
        q = min(free, m["quantity"] or free)
        return [ [("read_stock", B)], [("reply", dict(in_reply_to=mid, kind="offer", terms=[dict(quantity=q, unit_price_yen=fl["floor_yen"])],
                                                   message=f"{q}本なら 1本 {fl['floor_yen']:,}円でお譲りできます。", reason="手元の余りの分です"))] ]

    def _buyer_c(self, prompt):
        return [ [("do_nothing", dict(reason="自社の動く出来事ではありません"))] ]


def _message(prompt: str):
    import re
    mid = int(re.search(r"番号 (\d+)・", prompt).group(1))
    item = re.search(r"品目のコード ([a-z_]+)", prompt).group(1)
    return mid, item


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8794)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--variant", default="normal")
    a = ap.parse_args(argv)
    take = live.new_take_dir()
    os.rename(take, f"{take}-mockdock-{a.port}")   # 同じ秒に2つ起動しても置き場が重ならない
    take = type(take)(f"{take}-mockdock-{a.port}")
    os.environ["AGENTDOCK_DATA"] = str(take)
    if build.main([]) != 0:
        return 2
    scene.apply_variant(a.variant)
    srv = server.make_server(a.port)
    server.WORKER = live.Worker({o: Policy(o, a.delay) for o in db.OWNER_CODES}, run_kind="mock", take_dir=take)
    server.WORKER.start()
    print(f"mock dock on http://127.0.0.1:{a.port}/mate.html  data={take}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.WORKER.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
