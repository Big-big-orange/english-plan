#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
english-mail-reminder.py
每天读 english-plan.md，把「今天具体做什么、用哪个 App、怎么操作」发到邮箱。

用法：
    python english-mail-reminder.py --dry-run              # 控制台预览，不发信
    python english-mail-reminder.py --test                 # 立刻发一封
    python english-mail-reminder.py --date 2027-03-02      # 预览指定日期
    python english-mail-reminder.py                        # 正常发信（任务计划程序调用）
"""

import argparse
import datetime as dt
import html
import json
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

try:  # Windows 控制台默认 GBK，预览中文/音标会炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parent
PLAN = BASE / "english-plan.md"
CONFIG = BASE / "english-mail.config.json"
LOG = BASE / "mail.log"

WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 第 1 天专用的「照着做」时间轴
WALK_HEAD = [
    ("07:30 地铁上 · 泛听 10 min",
     "播客打开这集，从头播一遍。不暂停、不回放、不查词。播完 6 分钟再从头播一遍。"),
    ("18:30 地铁上 · 跟读 10 min",
     "拖到 00:00–00:45，听一句滞后半秒重复一句（气声即可），这 45 秒重复 5 遍。听不清的跳过。"),
    ("20:00 到家 · 精听 10 min",
     "① 只听一遍抓大意　② 一句一停，把听到的音写下来（拼错无所谓）　③ 开字幕对照，红笔圈出没听出来的词 → 3–5 个词今晚进 Anki"),
    ("20:10 到家 · 开口 10 min",
     "盖住文本，用 3–5 句英文复述这 45 秒。例：This episode is about procrastination. "
     "The speaker says we delay things because the task feels too big. She suggests breaking it into small steps. "
     "语法烂没关系，说不出来就换简单词，别沉默。"),
]
WALK_TAIL = [("睡前 · Anki 2 min", "把今天圈出的 3–5 个词做成卡，刷完")]


def walk_rows(today_action, wn, idx):
    """每天的逐分钟时间轴：前 4 步固定，第 5 步是当天专项，最后是 Anki"""
    mid = (f"20:20 额外 · 今天专项 10 min",
           f"{today_action}（这一步不在前 40 分钟里，录音存 audio/W{wn:02d}-{idx+1}-special.m4a）")
    return WALK_HEAD + [mid] + WALK_TAIL

WD_SHORT = ["一", "二", "三", "四", "五", "六", "日"]


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(
            f"[配置缺失] 没有找到 {path.name}\n"
            f"请复制 english-mail.config.example.json 为 english-mail.config.json，填入 163 邮箱和授权码。"
        )
    with path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("user", "auth_code", "to", "start_date"):
        if not cfg.get(k):
            sys.exit(f"[配置错误] {path.name} 缺少字段：{k}")
    cfg.setdefault("smtp_host", "smtp.163.com")
    cfg.setdefault("smtp_port", 465)
    cfg.setdefault("sender_name", "English Coach")
    return cfg


def clean(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    return s.strip()


# --------------------------------------------------------------------------- #
# 解析计划
# --------------------------------------------------------------------------- #
def parse_weeks(text: str) -> dict:
    lines = text.splitlines()
    heads = []
    for i, ln in enumerate(lines):
        m = re.match(r"^### W(\d{2}) · (.+?)\s+<sub>(\w+)</sub>", ln)
        if m:
            heads.append((i, m))

    weeks = {}
    for k, (start, m) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^## ", lines[j]):
                end = min(end, j)
                break
        body = lines[start + 1 : end]
        joined = "\n".join(body)

        def bullet(label):
            mm = re.search(r"^-\s+\*\*" + label + r"\*\*：(.+)$", joined, re.M)
            return clean(mm.group(1)) if mm else ""

        meta = re.search(r"^>\s+\*\*第 ([\d–\-]+) 天\*\*.*?│\s*素材：(.+)$", joined, re.M)
        material = clean(meta.group(2)) if meta else ""

        longday = flexday = ""
        mm = re.search(r"^-\s+\*\*第 6 天[^*]*\*\*：(.+)$", joined, re.M)
        if mm:
            longday = clean(mm.group(1))
        mm = re.search(r"^-\s+\*\*第 7 天[^*]*\*\*：(.+)$", joined, re.M)
        if mm:
            flexday = clean(mm.group(1))

        # 这 7 天做什么：| 3 | 11/05 四 | 内容 | ☐ |
        days = []
        for ln in body:
            dm = re.match(r"^\|\s*([1-7])\s*\|\s*(\d{2}/\d{2})\s*(\S)\s*\|\s*(.+?)\s*\|\s*(\S*)\s*\|", ln)
            if dm:
                days.append({
                    "n": int(dm.group(1)),
                    "date": dm.group(2),
                    "wd": dm.group(3),
                    "text": clean(dm.group(4)),
                    "done": "✅" in dm.group(5),
                })
        # 打卡表：| 通勤泛听 10min | ☐ | ... | (8 列)
        rows = []
        for ln in body:
            if not ln.startswith("|"):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) != 8:
                continue
            if cells[0] in ("打卡项", "") or set(cells[0]) <= set("-"):
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if cells[0].isdigit():
                continue
            rows.append((cells[0], cells[1:]))

        weeks[int(m.group(1))] = {
            "title": m.group(2).strip(),
            "phase": m.group(3).strip(),
            "task": bullet("本周任务"),
            "criteria": bullet("通过标准"),
            "material": material,
            "listen": bullet("本周听力素材"),
            "longday": longday,
            "flexday": flexday,
            "days": days,
            "rows": rows,
        }
    return weeks


def parse_phrases(text: str) -> list:
    m = re.search(r"^## 附录 C.*?$", text, re.M)
    if not m:
        return []
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    block = rest[: nxt.start()] if nxt else rest
    out = []
    for ln in block.splitlines():
        mm = re.match(r"^\s*(\d{1,2})\.\s+(.+)$", ln)
        if mm:
            out.append((int(mm.group(1)), mm.group(2).strip()))
    out.sort()
    return [p[1] for p in out]


def parse_stats(weeks: dict) -> dict:
    done = sum(1 for wk in weeks.values() for _, st in wk["rows"] for x in st if x == "✅")
    total = sum(len(wk["rows"]) * 7 for wk in weeks.values())
    return {"done": done, "total": total}


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def build(ctx: dict):
    D = html.escape
    today = ctx["today"]
    day_no = ctx["day_no"]
    wn = ctx["week_no"]
    wk = ctx["week"]
    idx = (day_no - 1) % 7          # 0..6 本周第几天
    left = ctx["days_left"]
    mat = wk["material"] or "见计划"
    listen = wk.get("listen") or "本周那集 6 Minute English"

    today_action = ""
    for d in wk["days"]:
        if d["n"] == idx + 1:
            today_action = d["text"]
    if idx == 5:
        role = "长时段专项（60–90 分钟）"
    elif idx == 6:
        role = "弹性日（补欠账 / 轻量复听）"
    else:
        role = "常规日"
    today_action = re.sub(r"^(长时段专项|弹性日)：", "", today_action)
    if ctx.get("pre_start"):
        today_action = f"（计划 {ctx['start_text']} 开始，今天可先预热）{today_action}"

    subject = f"[英语] {today:%m/%d} 第{day_no}天 · W{wn:02d} {wk['title']}"

    # ---------------- 纯文本 ----------------
    t = []
    t.append(f"第 {day_no} 天 · {today:%Y-%m-%d}（{WD[today.weekday()]}）· W{wn:02d} {wk['title']}（{wk['phase']}）")
    if left >= 0:
        t.append(f"距离出发还有 {left} 天")
    t.append("")
    t.append(f"【今天做什么 · {role}】")
    t.append(f"  {today_action}")
    t.append("")
    t.append(f"【今天学什么】{mat}")
    t.append(f"【今天听什么】{listen}")
    t.append("")
    if idx <= 4:
        t.append("【逐分钟照着做】")
        t.append("  今天只碰两样东西：一段 6ME（前 40 分钟）+ 今天的专项（额外 10 分钟）")
        t.append("")
        for slot, what in walk_rows(today_action, wn, idx):
            t.append(f"  ■ {slot}")
            t.append(f"    {what}")
            t.append("")
    elif idx == 5:
        t.append("【今天的安排】")
        t.append(f"  · 60–90 分钟长时段：{wk['longday']}")
        t.append("  · 复盘：回听本周录音，勾掉已完成项，卡点写进卡壳本")
        t.append("  · Anki 复习到清零")
        t.append("")
    elif idx == 6:
        t.append("【今天的安排】")
        t.append(f"  · {wk['flexday']}")
        t.append("  · 只做 20 分钟轻量复听，别硬撑")
        t.append("")
    t.append("【本周 7 天】")
    for d in wk["days"]:
        mark = "▶" if d["n"] == idx + 1 else " "
        done = "✅" if ctx["day_done"].get(d["n"]) else "  "
        t.append(f" {mark}{d['n']} {d['date']} {d['wd']} {done} {d['text']}")
    t.append("")
    if wk["rows"]:
        done = sum(1 for _, st in wk["rows"] for x in st if x == "✅")
        t.append(f"【本周打卡】{done}/{len(wk['rows'])*7}")
        for item, st in wk["rows"]:
            marks = "".join("●" if x == "✅" else "○" for x in st)
            t.append(f"  {item:<14} {marks}  {sum(1 for x in st if x=='✅')}/7")
        t.append("")
    if ctx["phrase"]:
        t.append("【今日一句 · 会前念 3 遍】")
        t.append(f"  {ctx['phrase']}")
        t.append("")
    t.append(f"【提醒】今天必须开口，哪怕 10 分钟。累计完成 {ctx['stats']['done']}/{ctx['stats']['total']} 项。")
    text_body = "\n".join(t)

    # ---------------- HTML ----------------
    def bar(done, total):
        pct = 0 if total == 0 else round(done * 100 / total)
        return (f'<div style="background:#e8eef7;border-radius:6px;height:8px;overflow:hidden;">'
                f'<div style="width:{pct}%;height:8px;background:#2f6fed;"></div></div>')

    def row2(k, v, bold=False):
        return (
            f'<tr><td style="padding:9px 12px;border-bottom:1px solid #eef2f7;color:#2f6fed;'
            f'font-size:12px;font-weight:600;white-space:nowrap;vertical-align:top;">{D(k)}</td>'
            f'<td style="padding:9px 12px;border-bottom:1px solid #eef2f7;color:#22303f;'
            f'font-size:13px;line-height:1.7;{"font-weight:600;" if bold else ""}">{v}</td></tr>'
        )

    walk_html = ""
    if idx <= 4:
        rows = "".join(row2(slot, D(what)) for slot, what in walk_rows(today_action, wn, idx))
        walk_html = (
            '<h3 style="font-size:13px;color:#5a6b7d;margin:28px 0 8px;font-weight:600;'
            'letter-spacing:.5px;">逐分钟照着做</h3>'
            '<div style="font-size:12px;color:#8a97a6;margin-bottom:8px;line-height:1.7;">'
            '今天只碰两样东西：<b>一段 6ME</b>（前 40 分钟）+ <b>今天的专项</b>（额外 10 分钟）。'
            '专项不在前 40 分钟里；复述的是 6ME，不是专项。</div>'
            f'<table style="width:100%;border-collapse:collapse;">{rows}</table>'
        )
    elif idx == 5:
        walk_html = (
            '<div style="margin-top:22px;background:#f0f7ff;border-left:3px solid #2f6fed;'
            'padding:14px 16px;border-radius:4px;font-size:14px;color:#22303f;line-height:1.8;">'
            f'<b>60–90 分钟长时段</b>：{D(wk["longday"])}<br>'
            '<b>复盘</b>：回听本周录音，勾掉已完成项，卡点写进卡壳本<br>'
            '<b>Anki</b>：复习到清零</div>'
        )
    elif idx == 6:
        walk_html = (
            '<div style="margin-top:22px;background:#f3f8f3;border-left:3px solid #4a9d5f;'
            'padding:14px 16px;border-radius:4px;font-size:14px;color:#22303f;line-height:1.8;">'
            f'<b>弹性日</b>：{D(wk["flexday"])}<br>只做 20 分钟轻量复听，别硬撑。</div>'
        )

    rows_html = ""
    for d in wk["days"]:
        on = d["n"] == idx + 1
        done = ctx["day_done"].get(d["n"])
        bg = "#fff8e6" if on else "transparent"
        border = "2px solid #f0b429" if on else "1px solid #eef2f7"
        mark = "▶" if on else ""
        tick = "✅" if done else "☐"
        rows_html += (
            f'<tr style="background:{bg};">'
            f'<td style="padding:8px 10px;border-bottom:{border};font-size:12px;color:#8a97a6;'
            f'white-space:nowrap;">{mark} D{d["n"]} {d["date"]}</td>'
            f'<td style="padding:8px 10px;border-bottom:{border};font-size:13px;color:#22303f;'
            f'line-height:1.6;">{D(d["text"])}</td>'
            f'<td style="padding:8px 10px;border-bottom:{border};font-size:12px;'
            f'text-align:center;">{tick}</td></tr>'
        )
    days_block = (
        '<h3 style="font-size:13px;color:#5a6b7d;margin:26px 0 8px;font-weight:600;'
        'letter-spacing:.5px;">本周 7 天</h3>'
        f'<table style="width:100%;border-collapse:collapse;">{rows_html}</table>'
    )

    prog_html = ""
    if wk["rows"]:
        cells = ""
        for item, st in wk["rows"]:
            cells += (
                f'<tr><td style="padding:6px 10px;font-size:13px;color:#22303f;">{D(item)}</td>'
                f'<td style="padding:6px 10px;font-size:13px;color:#5a6b7d;letter-spacing:2px;">'
                f'{"".join("●" if x=="✅" else "○" for x in st)}</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#8a97a6;text-align:right;">'
                f'{sum(1 for x in st if x=="✅")}/7</td></tr>'
            )
        done = sum(1 for _, st in wk["rows"] for x in st if x == "✅")
        total = len(wk["rows"]) * 7
        prog_html = (
            '<h3 style="font-size:13px;color:#5a6b7d;margin:26px 0 8px;font-weight:600;'
            f'letter-spacing:.5px;">本周打卡 · {done}/{total}</h3>'
            f'{bar(done, total)}'
            f'<table style="width:100%;border-collapse:collapse;margin-top:8px;">{cells}</table>'
        )

    phrase_html = ""
    if ctx["phrase"]:
        phrase_html = (
            '<div style="margin-top:26px;background:#fff8e6;border-left:3px solid #f0b429;'
            'padding:14px 16px;border-radius:4px;">'
            '<div style="font-size:11px;color:#8a6d1f;letter-spacing:.5px;margin-bottom:6px;">'
            '今日一句 · 会前念 3 遍</div>'
            f'<div style="font-size:15px;color:#3d3418;line-height:1.6;">{D(ctx["phrase"])}</div></div>'
        )

    left_html = (f'<span style="color:#ffd166;font-weight:600;">距出发 {left} 天</span>'
                 if left >= 0 else "")

    html_body = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f6f9;">
<div style="max-width:640px;margin:0 auto;padding:24px 16px;
            font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
  <div style="background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.07);">

    <div style="background:#1f3a5f;padding:20px 24px;">
      <div style="font-size:12px;color:#9db6d6;letter-spacing:.5px;">
        第 {day_no} 天　{today:%Y-%m-%d} {WD[today.weekday()]}　W{wn:02d}/{52}　{left_html}
      </div>
      <div style="font-size:21px;color:#fff;font-weight:600;margin-top:8px;">{D(wk['title'])}</div>
      <div style="font-size:12px;color:#9db6d6;margin-top:4px;">{D(wk['phase'])}　·　{role}</div>
    </div>

    <div style="padding:22px 24px;">

      <div style="background:#f7f9fc;border:1px solid #e3ebf5;border-left:4px solid #2f6fed;
                  border-radius:6px;padding:16px 18px;">
        <div style="font-size:11px;color:#2f6fed;letter-spacing:1px;margin-bottom:8px;">
          TODAY · 今天做什么</div>
        <div style="font-size:16px;color:#16212e;line-height:1.7;font-weight:500;">{D(today_action)}</div>
        <div style="margin-top:12px;padding-top:10px;border-top:1px dashed #d6e2f2;
                    font-size:12px;color:#5a6b7d;line-height:1.9;">
          今天学什么　{D(mat)}<br>
          今天听什么　{D(listen)}
        </div>
      </div>

      {walk_html}
      {days_block}
      {prog_html}
      {phrase_html}

      <div style="margin-top:26px;padding-top:16px;border-top:1px solid #eef2f7;
                  font-size:12px;color:#8a97a6;line-height:1.9;">
        · 今天必须开口说话，哪怕只有 10 分钟。地铁上小声跟读也算。<br>
        · 目标：本周主动说出 <b>≥50 句</b> 完整英文。<br>
        · 完成一项，把 english-plan.md 里的 ☐ 改成 ✅
        （累计 {ctx['stats']['done']}/{ctx['stats']['total']} 项）。<br>
        · 本周任务：{D(wk['task'])}　│　通过标准：{D(wk['criteria'])}
      </div>
    </div>
  </div>
  <div style="text-align:center;font-size:11px;color:#a8b3c0;padding:16px;">
    english-plan.md · 自动提醒
  </div>
</div></body></html>"""

    return subject, text_body, html_body


# --------------------------------------------------------------------------- #
# 发送
# --------------------------------------------------------------------------- #
def send(cfg: dict, subject: str, text_body: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{cfg.get('sender_name', 'English Coach')} <{cfg['user']}>"
    msg["To"] = cfg["to"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["smtp_host"], int(cfg["smtp_port"]), context=context, timeout=30) as s:
        s.login(cfg["user"], cfg["auth_code"])
        s.send_message(msg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只在控制台预览")
    ap.add_argument("--test", action="store_true", help="立刻发一封")
    ap.add_argument("--date", help="按指定日期生成（YYYY-MM-DD）")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if not PLAN.exists():
        sys.exit(f"[错误] 找不到 {PLAN}")

    text = PLAN.read_text(encoding="utf-8")
    weeks = parse_weeks(text)
    phrases = parse_phrases(text)
    stats = parse_stats(weeks)

    routine = [
        ("早上通勤 · 10 min", "泛听：耳机放本周那集 6ME，听不懂也继续，不查词、不回放"),
        ("晚上通勤 · 10 min", "影子跟读：同一集挑 30–60 秒，滞后半秒小声重复（气声也行）"),
        ("到家 · 10 min", "精听：就是刚才那 30–60 秒，盲听→听写→对字幕找漏"),
        ("到家 · 10 min", "开口：不看稿，用 3–5 句英文复述刚才那段 6ME（不是英语兔），录音"),
        ("碎片 · ≤5 min", "Anki：新卡 ≤8 张/天"),
    ]

    # 云端 runner 是 UTC，必须按北京时间算「今天」，否则会差一天
    tz_off = int(cfg.get("tz_offset_hours", 8))
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=tz_off)
    today = dt.date.fromisoformat(args.date) if args.date else now.date()
    start = dt.date.fromisoformat(cfg["start_date"])
    day_no = max(1, (today - start).days + 1)   # 计划开始前统一按第 1 天展示
    week_no = max(1, min(52, (max(day_no, 1) - 1) // 7 + 1))

    trip = cfg.get("trip_date")
    days_left = (dt.date.fromisoformat(trip) - today).days if trip else -999

    wk = weeks.get(week_no) or {
        "title": "（未找到该周）", "phase": "-", "task": "-", "criteria": "-",
        "material": "", "longday": "-", "flexday": "-", "days": [], "rows": [],
    }

    day_done = {d["n"]: d.get("done", False) for d in wk["days"]}

    phrase = phrases[today.toordinal() % len(phrases)] if phrases else ""

    subject, text_body, html_body = build({
        "today": today, "day_no": day_no, "week_no": week_no, "week": wk,
        "routine": routine, "phrase": phrase, "stats": stats,
        "days_left": days_left, "day_done": day_done,
        "pre_start": today < start, "start_text": f"{start:%m/%d}",
    })

    if args.dry_run:
        print("Subject:", subject)
        print("-" * 64)
        print(text_body)
        return

    try:
        send(cfg, subject, text_body, html_body)
    except Exception as e:  # noqa: BLE001
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} 发送失败: {e}\n")
        sys.exit(f"[发送失败] {type(e).__name__}: {e}\n详见 {LOG.name}")

    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} 已发送 -> {cfg['to']} | {subject}\n")
    print("已发送:", subject)


if __name__ == "__main__":
    main()
