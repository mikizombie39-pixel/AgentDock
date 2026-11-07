"""記録の再生のサーバー：撮影の記録（take）を写した置き場で、画面に読み出すだけのサーバーを起動する。

  python -m agentdock.replay --take evidence/takes/20261105-203748-live --port 8798
  → http://127.0.0.1:8798/dock.html?mode=replay&item=buri_xl

- 撮影の記録そのものは書き換えない：DB を保存先の根（db.LOCAL）の tmp\\replay-<take の名前>-<日時> に写し、
  写しに番号つきの SQL を当てる（後から足した表がある時も、前の撮影を再生できる）
- 写す先が OneDrive の中なら止める。本物の Claude は呼ばない（裏の処理は起動しない）
"""
import argparse
import datetime
import os
import shutil
import sys
from pathlib import Path

from agentdock import db

TMP = db.LOCAL / "tmp"


def copy_take(take: Path, base: Path = TMP) -> Path:
    """take の DB を写し、写しに移行を当てる。写した先を返す。"""
    take = Path(take)
    if not all((take / f"{c}.sqlite").is_file() for c in (*db.OWNER_CODES, db.MARKET)):
        raise FileNotFoundError(f"take has no dbs: {take}")
    dst = Path(base) / f"replay-{take.name}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if "onedrive" in str(dst).lower():
        raise ValueError(f"replay copy must stay outside OneDrive: {dst}")
    dst.mkdir(parents=True)
    for f in take.iterdir():
        if f.suffix == ".sqlite" or f.name.endswith((".sqlite-wal", ".sqlite-shm")):
            shutil.copy2(f, dst / f.name)
    old = os.environ.get("AGENTDOCK_DATA")
    os.environ["AGENTDOCK_DATA"] = str(dst)
    try:
        for code in (*db.OWNER_CODES, db.MARKET):
            c = db.connect(code)
            try:
                db.migrate(c, "market" if code == db.MARKET else "owner")
            finally:
                c.close()
    finally:
        if old is None:
            os.environ.pop("AGENTDOCK_DATA", None)
        else:
            os.environ["AGENTDOCK_DATA"] = old
    return dst


def main(argv=None) -> int:
    from agentdock import server
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", required=True)
    ap.add_argument("--port", type=int, default=8798)
    a = ap.parse_args(argv)
    try:
        dst = copy_take(Path(a.take))
        srv = server.make_server(a.port)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"stop: {e}", file=sys.stderr)
        return 2
    os.environ["AGENTDOCK_DATA"] = str(dst)
    print(f"agentdock replay on http://127.0.0.1:{srv.server_address[1]}/dock.html?mode=replay&item=buri_xl  data={dst}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
