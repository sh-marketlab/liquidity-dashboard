"""Generate docs/calendar.ics from docs/calendar_events.json.

Each event becomes an all-day entry (Taiwan date) with a reminder one day before.
Windows (events with "end") are added on their start date, with the end date in the title.
Run: python make_calendar_ics.py
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "docs" / "calendar_events.json"
OUT = ROOT / "docs" / "calendar.ics"


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    # RFC 5545: lines over 75 octets are folded with CRLF + space
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def build(data: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sh-marketlab//liquidity-dashboard debt calendar//ZH",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{esc(data.get('title', '債務日曆'))}",
        "X-WR-TIMEZONE:Asia/Taipei",
    ]
    for i, e in enumerate(data["events"]):
        d = date.fromisoformat(e["d"])
        title = e["t"] + ("（約）" if e.get("approx") else "")
        if e.get("end"):
            title += f"［至 {e['end']}］"
        desc = f"影響：{e.get('impact', '')}\n檢查：{e.get('check', '')}"
        if data.get("doc"):
            desc += f"\n文件：{data['doc']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:debtcal-{e['d']}-{i}@sh-marketlab",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{esc('［' + e.get('tag', '') + '］' + title)}",
            f"DESCRIPTION:{esc(desc)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(title)}",
            "TRIGGER:-P1D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


if __name__ == "__main__":
    data = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.write_text(build(data), encoding="utf-8", newline="")
    print(f"wrote {OUT} ({len(data['events'])} events)")
