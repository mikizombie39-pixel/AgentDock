"""画面の確かめ用の通し（模擬の Claude・費用 0）：本物と同じサーバー・同じ記録・同じ画面で、仲間の返事だけ決まった模擬にする。

  python -m agentdock.mocklive --port 8793

撮影には使わない（撮影は server --real）。検証の画面には run_kind が mock と出る。
模擬の返事は tests/test_scene.py の台本に、画面の組み方（見出し・比べる案・問いかけ・操作）と、質問への答えを足したもの。
"""
import argparse
import copy
import os
import sys
import time

from agentdock import db, live, server
from agentdock.companion import MockClient, Reply
from agentdock.seed import build

U = dict(input_tokens=3000, output_tokens=400)


class SlowMock(MockClient):
    """模擬の返事に、本物の呼び出しに近い待ち時間を入れる（画面の動きを確かめるため）。"""

    def __init__(self, replies, delay):
        super().__init__(replies)
        self.delay = delay

    def create(self, **params):
        time.sleep(self.delay)
        return super().create(**params)


def _scripts():
    sys.path.insert(0, str(db.REPO))
    from tests import test_scene as t
    amiya = copy.deepcopy(t.AMIYA)
    last = amiya[-1].content[-1]["input"]
    last.update(headline="15kg まとめて仕入れる案を勧めます。B社が 5kg を条件付きで引き取ります",
                alternative="B", ask_human="どちらの方針で進めますか？",
                actions=["approve", "compare", "check_partner", "show_others", "ask"],
                suggested_questions=["B社との取引は本当に大丈夫？", "料亭に回す案と比べたい"])
    last["cards"] = [t.CARDS[2], t.CARDS[3], t.CARDS[1], t.CARDS[0]]
    amiya.append(Reply(content=[{"type": "tool_use", "id": "mq1", "name": "read_thread", "input": {"item_code": "aji"}}], usage=U))
    amiya.append(Reply(content=[{"type": "tool_use", "id": "mq2", "name": "answer_staff", "input": dict(
        answer="B社の合意は条件付きで、期限は 07:00 です。B社の担当者が事前に承認した範囲の内なので、網谷水産の承認と仕入れの確定で成立します。",
        headline="B社との合意の条件と期限を確かめました", focus="partner", primary="A", alternative="B",
        cards=[t.CARDS[2], t.CARDS[3]], actions=["approve", "compare", "ask"],
        suggested_questions=["料亭に回す案と比べたい"], ask_human="15kg の案で進めますか？")}], usage=U))
    amiya.append(Reply(content=[{"type": "tool_use", "id": "mq3", "name": "compare_options", "input": {"item_code": "aji", "options": [
        {k: o[k] for k in ("code", "buy_term_id", "allocations")} for o in t.OPTS[:2]]}}], usage=U))
    amiya.append(Reply(content=[{"type": "tool_use", "id": "mq4", "name": "answer_staff", "input": dict(
        answer="料亭に回す案は売値が高い一方、料亭にはまだ打診していません。当日の朝の連絡は断られることがあると社長の経験にあります。",
        headline="B社に回す案と料亭に回す案を並べました", focus="compare", primary="A", alternative="B",
        cards=[t.CARDS[3], t.CARDS[2]], actions=["approve", "check_partner", "ask"],
        suggested_questions=[], ask_human="どちらで進めますか？")}], usage=U))
    return dict(amiya=amiya, buyer_b=t.B, iwase=t.IWASE, buyer_c=t.C)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--delay", type=float, default=1.2)
    a = ap.parse_args(argv)
    take = live.new_take_dir()
    os.rename(take, str(take) + "-mock")
    take = type(take)(str(take) + "-mock")
    os.environ["AGENTDOCK_DATA"] = str(take)
    if build.main([]) != 0:
        return 2
    srv = server.make_server(a.port)   # 番号が使われていれば、ここで止まる（裏の処理は始めない）
    scripts = _scripts()
    clients = {o: SlowMock(scripts[o], a.delay) for o in db.OWNER_CODES}
    server.WORKER = live.Worker(clients, run_kind="mock", take_dir=take)
    server.WORKER.start()
    print(f"mock live on http://127.0.0.1:{a.port}/board.html  data={take}")
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
