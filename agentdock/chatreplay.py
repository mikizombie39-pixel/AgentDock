"""本物の Claude の記録（chatreal の record.json）の生の答えを、今のサーバーの検査（chat.finish）にかけ直す。
Claude は呼ばない・DB に書かない（道具は読むだけ）。業務の状態は撮影の最後の状態なので、途中の出来事より後の問だけが同じ条件になる。

  python -X utf8 -m agentdock.chatreplay <撮影のフォルダ> 4,6,7,8
"""
import json
import os
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -X utf8 -m agentdock.chatreplay <take folder> <step numbers, e.g. 4,6,7>")
        return 2
    take, nos = argv[0], {int(x) for x in argv[1].split(",")}
    os.environ["AGENTDOCK_DATA"] = os.path.join(take, "data")
    from agentdock import chat
    from agentdock.seed import scenario as sc
    rec = json.load(open(os.path.join(take, "record.json"), encoding="utf-8"))
    by = {n: fn for n, _, _, fn in chat.TOOLS}
    for s in rec["steps"]:
        if s["no"] not in nos or s["kind"] != "ask":
            continue
        t = chat.Turn("amiya", "buri_xl", sc.DEMO_DATE)
        chat._state(t)
        for st in s["turn"]["steps"]:
            if st["tool"] != "answer" and not st["is_error"]:
                by[st["tool"]](t, st["input"])
        t.answer = s["turn"]["raw"]
        out, dropped = chat.finish(t, s["question"], rec.get("run_enabled", False))
        print(f"=== {s['no']}. {s['question']}")
        for k in ("conclusion", "next_action", "detail", "interpretation"):
            print(f"[{k}] {out[k]}")
        for r in dropped["replaced"]:
            print(f"[removed:{r['field']}] {r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
