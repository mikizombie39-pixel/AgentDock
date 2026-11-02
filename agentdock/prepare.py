"""画面に出す状態を DB に用意する（模擬の仲間で・Claude は呼ばない）。

  python -m agentdock.prepare bid     # 入札の場面：資料を届け、網谷水産の計画を承認し、仲間の入札を記録する

作り直し（seed.build）から始めるので、画面への読み出し（server）を止めてから実行する。
"""
import sys

from agentdock import broadcast, consult, db, evidence, plans
from agentdock.seed import build
from agentdock.seed import scenario as sc


def bid() -> dict:
    if build.main([]) != 0:
        raise SystemExit(2)
    iw = db.connect("iwase")
    try:
        bid_id = broadcast.publish(broadcast.compose(iw, sc.DEMO_DATE))
    finally:
        iw.close()
    a = db.connect("amiya")
    try:
        p = sc.AMIYA_TENDER_PLAN
        plans.approve(a, "shiroebi", "tender", sc.DEMO_DATE, p["max_unit_price_yen"], p["max_quantity"])
        t = consult.open_task(a, "tender", "shiroebi")
        price, qty = next((pr, q) for who, pr, q in sc.TODAY_TENDER["bids"] if who == "amiya")
        order = a.execute("SELECT o.id, c.name, o.quantity FROM order_book o JOIN item i ON i.id = o.item_id JOIN customer c ON c.id = o.customer_id "
                          "WHERE i.code = 'shiroebi' ORDER BY o.id LIMIT 1").fetchone()
        did = evidence.record(a, t, "bid", price, qty, sc.AMIYA_TENDER_RATIONALE,
                              [("order", order[0], f"{order[1]}の注文 {order[2]}箱"),
                               ("price_history", None, "直近30日と去年の同じ時期の相場"),
                               ("broadcast", bid_id, "漁協の資料：今朝の水揚げ")], sc.DEMO_DATE, model="mock")
    finally:
        a.close()
    return dict(broadcast_id=bid_id, decision_id=did)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["bid"]:
        print("usage: python -m agentdock.prepare bid", file=sys.stderr)
        return 2
    print(bid())
    return 0


if __name__ == "__main__":
    sys.exit(main())
