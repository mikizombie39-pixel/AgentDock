"""チャットの模擬の Claude（試験と画面の確かめ用・費用 0）。本物の Claude の代わりに、同じ道具を同じ順で呼び、道具の結果から答えを組む。

固定の文を返すのではなく、受け取った業務の状態と Vault の記録の中身から文を作る（指示や業務の状態が変われば答えも変わる）。
文の言い回しは模擬の決まりで、本物の Claude の答えとは違う。数字は道具の結果の文から取る（数字を作らない）。
  1回目：Vault の索引と業務の状態を読む
  2回目：質問に合う Vault の記録を読む・下見を計算する（要らなければ飛ばす）
  3回目：answer で答える
"""
import json
import re
import time

from agentdock import chat
from agentdock.companion import Reply

U = dict(input_tokens=2500, output_tokens=300)


def _results(msg) -> dict:
    out = {}
    for b in msg["content"] if isinstance(msg.get("content"), list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            name = b["tool_use_id"].split("#")[0]
            try:
                out[name] = json.loads(b["content"])
            except (TypeError, ValueError):
                out[name] = b["content"]
    return out


class ChatPolicy:
    def __init__(self, delay: float = 0.0):
        self.delay, self.n, self.requests = delay, 0, []
        self.q = self.ix = self.ws = None
        self.read = {}
        self.pv = None

    def _call(self, *calls):
        blocks = []
        for name, inp in calls:
            self.n += 1
            blocks.append({"type": "tool_use", "id": f"{name}#{self.n}", "name": name, "input": inp})
        return Reply(content=blocks, usage=U)

    def create(self, **params):
        self.requests.append(json.loads(json.dumps(params, ensure_ascii=False, default=str)))
        if self.delay:
            time.sleep(self.delay)
        msgs = params["messages"]
        if len(msgs) == 1:
            self.q = msgs[0]["content"].split("担当者の言葉：", 1)[-1].strip()
            self.ix = self.ws = self.pv = None
            self.read = {}
            return self._call(("list_vault", {}), ("read_work_state", {}))
        got = _results(msgs[-1])
        if "list_vault" in got:
            self.ix = got["list_vault"]["records"]
            self.ws = got["read_work_state"]
            ids, pv = self._pick()
            calls = []
            if ids:
                calls.append(("read_vault_record", {"ids": ids}))
            if pv:
                calls.append(("preview_allocation", {"priority": pv}))
            if calls:
                return self._call(*calls)
        if "read_vault_record" in got and isinstance(got["read_vault_record"], dict):
            self.read = {r["id"]: r for r in got["read_vault_record"]["records"]}
        if "preview_allocation" in got and isinstance(got["preview_allocation"], dict):
            self.pv = got["preview_allocation"]
        return self._call(("answer", self._answer()))

    # ── 何を読むか ──
    def _has(self, *ws):
        return any(w in self.q for w in ws)

    def _ids(self, pred):
        return [r["id"] for r in self.ix if pred(r)]

    def _unknown_shop(self):
        """質問の中の店の名前（〜商店・〜鮮魚店）が、今の注文の店に無い時はその名前（記録に無い店を、ある店に読み替えない）。"""
        known = {self._shop_of(k) for k in ("スーパー", "料亭")}
        for m in re.finditer(r"[一-龥ァ-ヶー]+(?:商店|鮮魚店)", self.q):
            if m.group(0) not in known:
                return m.group(0)
        return None

    def _pick(self):
        if self._unknown_shop():
            return [], None
        if self._has("代わり"):
            return self._ids(lambda r: "代わりの魚" in r["title"] or ("順風満帆" in r["subject"] and r["category"] == "仕入れの知識")), None
        if self._has("優先にすると", "どうなる", "先にすると"):
            first = "料亭" if self._has("順風満帆", "料亭") else "スーパー"
            return [], [first, "スーパー" if first == "料亭" else "料亭"]
        if self._has("社長") and self._has("言", "指示"):
            return self._ids(lambda r: r["category"] == "社長の指示"), None
        if self._has("なぜ", "どうして") and self._has("優先", "先に", "魚廣", "順風満帆"):
            return self._ids(lambda r: (r["category"] == "社長の指示" and r["use_label"].startswith("今有効")) or
                             (r["category"] == "会社のルール" and ("優先" in r["title"] or "指示に沿う" in r["title"]))), None
        return [], None

    # ── 業務の状態の文から ──
    def _facts(self, pred=lambda x: True):
        return [x for x in self.ws["facts"] if pred(x)]

    def _shop_of(self, kubun):
        for x in self._facts(lambda x: x["ref"].startswith("order_book:")):
            m = re.match(r"(.+?)（売り先の区分 (.+?)）", x["text"])
            if m and m.group(2) == kubun:
                return m.group(1)
        return kubun

    def _directive_fact(self):
        return next(iter(self._facts(lambda x: x["type"] == "directive")), None)

    def _ev(self, *refs, key=None):
        out = []
        for r in refs:
            if not r:
                continue
            if r in self.read:
                sents = [x + "。" for x in self.read[r]["body"].split("。") if x]
                hit = next((x for x in sents if key and key in x), sents[0])
                out.append(dict(ref=r, quote=hit))
            else:
                out.append(dict(ref=r, quote=None))
        return out

    def _ans(self, kind, conclusion, next_action, evidence, detail=None, interpretation=None, request=None):
        return dict(kind=kind, conclusion=conclusion, next_action=next_action, detail=detail, interpretation=interpretation,
                    evidence=evidence, request=request)

    def _answer(self):
        q = self.q
        shop = self._unknown_shop()
        if shop:
            return self._ans("question", chat.UNKNOWN, f"「{shop}」がどの店のことか、先輩か社長に確かめてください。",
                             self._ev(*(x["ref"] for x in self._facts(lambda x: x["ref"].startswith("order_book:")))),
                             detail=f"今の注文の店は {'・'.join(self._shop_of(k) for k in ('スーパー', '料亭'))} で、「{shop}」は記録にありません。")
        if self._has("代わり"):
            no = next((r for r in self.read.values() if "記録していない" in r["body"]), None)
            cond = next((r for r in self.read.values() if "代わりの魚" in r["title"]), None)
            rule = next((x + "。" for x in (cond["body"].split("。") if cond else []) if "代わりの魚で納める" in x), "")
            return self._ans("question", chat.UNKNOWN, f"{self._shop_of('料亭')}に、代わりの魚でよいか確かめるかを決めてください。",
                             self._ev(no and no["id"], cond and cond["id"], key="代わりの魚"),
                             detail=(f"{self._shop_of('料亭')}の代わりの魚の好みは、記録していません。" if no else "") + rule)
        if self.pv:
            d = self._directive_fact()
            return self._ans("preview", self.pv["text"], "下見だけです。この配分で進めるなら、そう頼んでください。",
                             self._ev(self.pv["ref"], d and self.pv["against_directive"] and d["ref"]),
                             interpretation="社長の指示と違う分け方は、私から社長に確かめます。" if self.pv["against_directive"] else None)
        if self._has("社長") and self._has("言", "指示"):
            cur = [r for r in self.read.values() if r["use"].startswith("今有効")]
            old = [r for r in self.read.values() if not r["use"].startswith("今有効")]
            d = self._directive_fact()
            if cur:
                m = re.search(r"「(.+?)」", cur[0]["body"])
                con = f"今朝、社長は別の浜から「{m.group(1) if m else cur[0]['title']}」と言っています。"
                nxt = f"{self._shop_of('スーパー')}の分を先にそろえます。"
            else:
                con, nxt = "今日有効な社長の指示は、記録にありません。", "先に受けた注文から確保します。"
            det = "".join(f"{r['title']}（{_md(r['valid_from'])}〜{_md(r['valid_to'])}）は{r['validity']}で、今は効力がありません。" for r in old) or None
            return self._ans("question", con, nxt, self._ev(*(r["id"] for r in cur), d and d["ref"], *(r["id"] for r in old)), detail=det)
        if self._has("なぜ", "どうして") and self._has("優先", "先に", "魚廣", "順風満帆"):
            d = self._directive_fact()
            rules = [r for r in self.read.values() if r["category"] == "会社のルール"]
            cur = [r for r in self.read.values() if r["category"] == "社長の指示"]
            if d and cur:
                m = re.search(r"「(.+?)」", d["text"])
                keep = self._shop_of("スーパー")
                other = self._shop_of("料亭")
                follow = next((r for r in rules if "指示に沿う" in r["title"]), None)
                return self._ans("question", f"{keep}を先にそろえるのは、社長の今朝の指示「{m.group(1)}」があるためです。",
                                 f"{other}を先にするなら、私が社長に確かめます。",
                                 self._ev(d["ref"], cur[0]["id"], follow and follow["id"]),
                                 interpretation="指示どおりの分け方なら、あなたが決めてよい決まりです。" if follow else None)
            base = next((r for r in rules if "優先" in r["title"]), None)
            return self._ans("question", "今日は社長の指示の記録がありません。先に受けた注文から確保する決まりで進めています。",
                             "優先を変えるなら、社長に確かめます。", self._ev(base and base["id"], *(x["ref"] for x in self._facts(lambda x: x["ref"].startswith("order_book:")))))
        if self._has("足りな", "配分", "何本"):
            opts = self._facts(lambda x: x["ref"].startswith("calc:option:"))
            tally = next(x for x in self.ws["facts"] if x["ref"] == "calc:tally")
            if opts:
                o = opts[0]
                m = re.search(r"この案では足りない：(.+?)(?:・|$)", o["text"])
                no = re.match(r"案(\d+)", o["text"]).group(1)
                con = (f"案{no}の配分だと、{m.group(1)}足りません。" if m else f"案{no}の配分なら、全部の注文がそろいます。")
                return self._ans("question", con, "ほかの案と比べるなら、画面の案のボタンを見てください。", self._ev(o["ref"], "calc:tally"), detail=tally["text"])
            return self._ans("question", tally["text"], "足りない分は、私が照会を続けます。", self._ev("calc:tally"))
        if self._has("漁協"):
            fs = self._facts(lambda x: "岩瀬漁協" in x["text"] and (x["ref"].startswith("network_message:") or x["ref"].startswith("offer_term:")))
            if not fs:
                return self._ans("question", chat.UNKNOWN, "岩瀬漁協とのやり取りが始まったら、お知らせします。", [])
            held = [x for x in fs if "仮押さえ" in x["text"]]
            main = (held or fs)[-1]
            return self._ans("question", main["text"] + "。", "判断が要る時は、私から声をかけます。", self._ev(*(x["ref"] for x in fs)))
        if self._has("進めて", "聞いて", "確かめて", "頼む", "見送") or re.search(r"(して|って|て)[。！!]?$", q):
            return self._request()
        return self._ans("question", chat.UNKNOWN, "誰に何を確かめるか、決めてください。", [])

    def _request(self):
        acts = self.ws["actions"]
        if self._has("見送"):
            a = next((a for a in acts if "見送" in a["label"]), None)
        elif self._has("聞いて", "確かめて"):
            a = next((a for a in acts if a["id"] == "sqconfirm_customer"), None)
        elif self._has("進めて", "頼む"):
            m = re.search(r"案\s*(\d+)", self.q)
            opts = [a for a in acts if a["id"].startswith("opt")]
            if m and opts:
                a = opts[int(m.group(1)) - 1] if int(m.group(1)) <= len(opts) else None
            else:
                a = next((a for a in opts if a["recommended"]), None)
        else:
            a = None
        if a is None:
            can = "／".join(a["label"] for a in acts) or "今は、頼める操作がありません"
            return self._ans("unsupported", "その頼みは、今の画面の操作では実行できません。", "できる操作から選ぶか、先輩か社長に相談してください。",
                             self._ev("state"), detail=f"できること：{can}。できないこと：画面にない相手への連絡・送信・登録。")
        sq = next((x for x in self.ws["facts"] if x["ref"].startswith("stock_question:")), None)
        if a["id"] == "sqconfirm_customer" and sq:
            m = re.search(r"在庫 (\d+)(本|箱|kg)", sq["text"])
            conf = f"{self._shop_of('料亭')}に、ブリ {m.group(1)}{m.group(2)}で対応できるか確かめる、という依頼ですね。" if m else None
        else:
            conf = f"「{a['label']}」で進める、という依頼ですね。" + ("社長の確認が要る案なので、私が社長に確かめます。" if a["president"] else "")
        return self._ans("request", conf or f"「{a['label']}」で進める、という依頼ですね。", "よければ［この内容で頼む］を押してください。",
                         self._ev("state", sq and sq["ref"]), request=dict(action_id=a["id"], confirm_text=conf or f"「{a['label']}」で進める、という依頼ですね。"))


def _md(d):
    return f"{int(d[5:7])}月{int(d[8:10])}日" if d else "期限なし"
