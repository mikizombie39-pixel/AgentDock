"""実演の通し：サーバーの裏で、待ちの出来事を起きた順に1件ずつ処理する（各組織の Claude AI パートナーを起こす）。

  画面（磁石ボード）は、この処理が DB に書いた事実を読んで変わる。台本の再生はしない。
  安全装置：
  - 呼び出しの合計の上限（max_total_calls）。超えたら止まり、画面の例外と検証の画面に出す
  - 起動が 'error' で終わったら止まる（同じ出来事を繰り返し呼ばない）。resume で再開する
  - 1つの処理は同時に1本だけ（組織の DB を並べて書かない）
"""
import datetime
import json
import threading
from pathlib import Path

from agentdock import agents, db, scene
from agentdock.seed import scenario as sc

MAX_TOTAL_CALLS = 150


class Worker:
    def __init__(self, clients: dict, run_kind: str = "real", max_total_calls: int = MAX_TOTAL_CALLS,
                 on_date: str = sc.DEMO_DATE, take_dir: Path | None = None):
        self.clients, self.run_kind, self.on_date = clients, run_kind, on_date
        self.max_total_calls, self.total_calls = max_total_calls, 0
        self.error: str | None = None
        self.last: dict | None = None
        self.take_dir = take_dir
        self.log: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        return dict(run_kind=self.run_kind, total_calls=self.total_calls, max_total_calls=self.max_total_calls,
                    error=self.error, last=self.last, running=bool(self._thread and self._thread.is_alive()))

    def step(self) -> dict | None:
        """待ちの出来事を1件処理する。処理しなければ None。"""
        with self._lock:
            if self.error:
                return None
            left = self.max_total_calls - self.total_calls
            if left <= 0:
                self.error = f"呼び出しの上限（{self.max_total_calls}回）に達しました"
                return None
            nxt = scene.next_event()
            if nxt is None:
                return None
            owner, ev = nxt
            r = agents.run_event(self.clients[owner], owner, ev, self.on_date, run_kind=self.run_kind,
                                 max_calls=min(agents.autonomy.MAX_CALLS, left))
            self.total_calls += r["calls"]
            self.last = dict(owner=owner, event_kind=ev["kind"], outcome=r["outcome"], calls=r["calls"], summary=(r.get("summary") or "")[:200])
            self.log.append(dict(at=db.now_iso(), **self.last))
            if r["outcome"] == "error":
                self.error = f"{owner} の起動が失敗しました：{r.get('summary')}"
            self._save()
            return r

    def resume(self) -> None:
        self.error = None

    def _save(self) -> None:
        """撮影の記録（編集の前の一連の実行の記録）を、処理のたびに take.json に書く。"""
        if not self.take_dir:
            return
        from agentdock import runlog
        data = scene._dump(self.take_dir)
        data["log"] = self.log
        data["spent_usd"] = runlog.spent_micro_usd() / 1_000_000
        data["live"] = True
        tmp = self.take_dir / "take.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.take_dir / "take.json")

    def start(self, interval: float = 0.4) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    r = self.step()
                except Exception as e:   # 部品の不具合：止めて画面に出す（繰り返し呼ばない）
                    self.error = f"{type(e).__name__}: {e}"
                    r = None
                if r is None:
                    self._stop.wait(interval)
        self._thread = threading.Thread(target=loop, name="agentdock-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def new_take_dir(base: str = scene.TAKES) -> Path:
    d = Path(base) / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-live")
    d.mkdir(parents=True)
    return d


def has_started(on_date: str = sc.DEMO_DATE) -> bool:
    c = db.connect("amiya")
    try:
        return c.execute("SELECT 1 FROM biz_event WHERE kind = 'order_new' LIMIT 1").fetchone() is not None
    finally:
        c.close()
