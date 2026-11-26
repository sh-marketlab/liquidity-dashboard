"""逃生檢查表：階段改變時開一則 GitHub Issue（GitHub 會寄 email，也會推播到 GitHub 手機 App）

用法（Actions 內）: python notify_escape.py /tmp/prev_escape.json docs/escape_data.json
需要 GITHUB_TOKEN、GITHUB_REPOSITORY；本機沒有 token 時只印出內容。
"""
import json
import os
import sys
import urllib.request

PAGE = "https://sh-marketlab.github.io/liquidity-dashboard/escape.html"
ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴", "na": "⚪"}


def load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    prev, cur = load(sys.argv[1]), load(sys.argv[2])
    if not cur:
        print("沒有本次資料，不通知")
        return
    S = cur["summary"]
    e1, r1 = S["escape"]["stage"], S["recovery"]["stage"]
    if prev:
        e0, r0 = prev["summary"]["escape"]["stage"], prev["summary"]["recovery"]["stage"]
        if (e0, r0) == (e1, r1):
            print(f"階段沒變（逃生 {e1}／回升 {r1}），不通知")
            return
        parts = []
        if e0 != e1:
            parts.append(f"逃生 {e0}→{e1}{'⬆' if e1 > e0 else '⬇'}")
        if r0 != r1:
            parts.append(f"回升 {r0}→{r1}{'⬆' if r1 > r0 else '⬇'}")
        title = f"逃生檢查 {cur['last_session']}：" + "、".join(parts) + f"（{S['escape']['name']}／{S['recovery']['name']}）"
    else:
        title = f"逃生檢查通知已啟用（{cur['last_session']} 基準：逃生 {e1}／回升 {r1}）"

    rows = "\n".join(f"| {ICON[s['status']]} | {s['no']}. {s['name']} | {'無法確認' if s['value'] is None else s['value']} {s['unit'] if s['value'] is not None else ''} | {s['date'] or ''} |"
                     for s in cur["signals"])
    on = lambda lst: "、".join(f"{c['id']} {c['text']}" for c in lst if c["on"]) or "無"
    body = "\n".join([
        f"**{S['line']}**",
        "",
        f"- 該做的事：{S['escape']['action']}",
        f"- {S['escape']['next']}",
        f"- {S['recovery']['next']}",
        f"- 情境：{S['scenario']['name']}（{S['scenario']['desc']}）",
        f"- 惡化訊號：{on(cur['deter'])}",
        f"- 危險訊號：{on(cur['danger'])}",
        f"- 回升訊號：{on(cur['recov'])}",
        "",
        "| | 訊號 | 數值 | 資料日 |", "|---|---|---|---|", rows,
        "",
        f"完整頁面：{PAGE}",
        "",
        "> 指標監控，並非買賣建議。門檻是經驗值，可能發出假警報。",
    ])
    print(title)
    print(body)
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("（沒有 GITHUB_TOKEN，略過開 Issue）")
        return
    payload = {"title": title, "body": body, "assignees": [repo.split("/")[0]]}
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
           "Content-Type": "application/json"}
    for attempt in (payload, {"title": title, "body": body}):   # 指派失敗（例如帳號是組織）就不指派
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues",
                                     data=json.dumps(attempt).encode(), headers=hdr, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print("issue:", r.status, json.loads(r.read()).get("html_url"))
                return
        except Exception as e:  # noqa: BLE001
            print("開 Issue 失敗:", e)


if __name__ == "__main__":
    main()
