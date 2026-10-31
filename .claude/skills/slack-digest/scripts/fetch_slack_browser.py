# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright"]
# ///
"""Collect recent Slack messages via the web client (Playwright) and print JSON.

First run `--login` once to sign in; the session is kept in a local profile.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

# 1日の区切り: 当日5:00 〜 翌5:00 (29時) を1日として扱う
DAY_START_HOUR = 5

from playwright.sync_api import sync_playwright

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(SKILL_DIR, ".browser-profile")

EXTRACT_JS = """
root => Array.from((document.querySelector(root) || document).querySelectorAll('[data-qa="virtual-list-item"]'))
  .map(el => {
    const ts = el.getAttribute('data-item-key');
    if (!ts || !/^\\d+\\.\\d+$/.test(ts)) return null;
    const q = s => el.querySelector(s);
    const sender = q('[data-qa="message_sender_name"]') || q('.c-message__sender_link');
    const text = q('[data-qa="message-text"]');
    const reply = q('[data-qa="reply_bar_count"]') || q('.c-message__reply_count');
    const reactions = Array.from(el.querySelectorAll('.c-reaction__count'))
      .reduce((n, e) => n + (parseInt(e.textContent, 10) || 0), 0);
    return {
      ts,
      user: sender ? sender.textContent.trim() : null,
      text: text ? text.innerText.trim() : '',
      replies: reply ? (parseInt((reply.textContent.match(/\\d+/) || ['0'])[0], 10)) : 0,
      reactions,
    };
  }).filter(Boolean)
"""

MAIN_ROOT = "body"
PANE_ROOT = '[data-qa="threads_flexpane"]'


def launch(p, headless):
    os.makedirs(PROFILE, exist_ok=True)
    return p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=headless,
        viewport={"width": 1400, "height": 900})


def login(url):
    with sync_playwright() as p:
        ctx = launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url or "https://app.slack.com/client")
        print("ブラウザでSlackにログインしてください (最大10分待機)", file=sys.stderr)
        page.wait_for_url(re.compile(r"app\.slack\.com/client/"), timeout=600000)
        page.wait_for_selector('[data-qa="virtual-list-item"]', timeout=600000)
        time.sleep(3)
        ctx.close()
    print("ログイン状態を保存しました", file=sys.stderr)


def scroll_collect(page, root, stop_ts, max_iter=300):
    """Scroll a virtualized list upward, collecting items until stop_ts is reached."""
    seen = {}
    idle = 0
    for _ in range(max_iter):
        before = len(seen)
        last_sender = None
        for m in sorted(page.evaluate(EXTRACT_JS, root), key=lambda m: float(m["ts"])):
            if m["user"]:
                last_sender = m["user"]
            elif m["ts"] not in seen:
                m["user"] = last_sender or "unknown"
            if m["ts"] not in seen or (m["user"] and not seen[m["ts"]]["user"]):
                seen[m["ts"]] = m
        oldest_seen = min((float(t) for t in seen), default=None)
        if oldest_seen is not None and oldest_seen <= stop_ts:
            break
        idle = idle + 1 if len(seen) == before else 0
        if idle >= 4:
            break
        page.keyboard.press("Escape")
        box = page.locator(root + ' [data-qa="slack_kit_list"]').first if root == PANE_ROOT \
            else page.locator('[data-qa="slack_kit_list"]').first
        try:
            box.hover(timeout=2000)
        except Exception:
            page.mouse.move(1100 if root == PANE_ROOT else 600, 450)
        page.mouse.wheel(0, -2500)
        page.wait_for_timeout(900)
    return seen


def fetch_thread(page, base, cid, ts):
    """Open a thread via its URL and return all messages (parent first)."""
    page.goto("%s/thread/%s-%s" % (base, cid, ts))
    try:
        page.wait_for_selector(PANE_ROOT + ' [data-qa="virtual-list-item"]', timeout=15000)
    except Exception:
        return []
    page.wait_for_timeout(1200)
    seen = scroll_collect(page, PANE_ROOT, float(ts), max_iter=60)
    return sorted(seen.values(), key=lambda m: float(m["ts"]))


def short_name(name):
    """Drop affiliations; prefer the Japanese part of 'English / 日本語 (部署)'."""
    name = re.sub(r"\s*[(（].*$", "", name or "").strip()
    parts = [x.strip() for x in name.split(" / ") if x.strip()]
    jp = [x for x in parts if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", x)]
    return (jp or parts or [name])[0]


ABSENCE = re.compile(
    r"[_ ]?[(（]?[\d/,\-、]+(?:AM|PM|午前|午後|終日|日中)?(?:[(（][^)）]*[)）])?"
    r"(?:不在|研修|休暇|年休|休|出張)[^\s@]*")
CC_MARK = re.compile(r"(?:cc|CC|Cc|ＣＣ)\s*[:：]")
MENTION_LINE = re.compile(r"^\s*[(（]?(?:(?:cc|CC|Cc|ＣＣ)\s*[:：]?\s*)?[(（]?@")
MENTION = re.compile(r"@[^@]+?(?=\s*@|$)")


def _mentions(s, keep):
    names = [x.strip() for x in MENTION.findall(s.strip()) if x.strip()]
    if len(names) > keep:
        return " ".join(names[:keep]) + " 他%d名" % (len(names) - keep)
    return " ".join(names)


def compact_text(text, max_mentions=6, max_cc=2):
    """Strip absence notes / affiliations from mention lines; shrink long cc lists. Recipients are kept."""
    out = []
    for line in text.split("\n"):
        if MENTION_LINE.match(line):
            line = ABSENCE.sub("", line)
            line = re.sub(r"[(（][^()（）]*[)）]", "", line)
            m = CC_MARK.search(line)
            head, cc = (line[:m.start()], line[m.end():]) if m else (line, "")
            line = _mentions(head, max_mentions)
            if cc.strip():
                line = (line + " " if line else "") + "cc: " + _mentions(cc, max_cc)
        out.append(line)
    return "\n".join(out)


def hit(text, words):
    return any(w.lower() in text.lower() for w in words)


SKIP_COPY = shutil.ignore_patterns(
    "Singleton*", "*.lock", "LOCK", "Cache", "Code Cache", "CacheStorage", "ScriptCache",
    "GPUCache", "GrShaderCache", "GraphiteDawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "ShaderCache", "component_crx_cache", "extensions_crx_cache", "OptimizationGuide*")


def run_parallel(workers, names, argv):
    """Split channels across worker processes (each with its own profile copy) and merge results."""
    workers = max(1, min(workers, len(names)))
    groups = [names[i::workers] for i in range(workers)]
    tmp = tempfile.mkdtemp(prefix="slack-digest-")

    def work(i):
        prof = os.path.join(tmp, "profile%d" % i)
        shutil.copytree(PROFILE, prof, ignore=SKIP_COPY, dirs_exist_ok=True)
        cmd = [sys.executable, os.path.abspath(__file__), "--workers", "1", "--profile", prof] + argv
        for n in groups[i]:
            cmd += ["--channel", n]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit((r.stdout + r.stderr).strip() or "worker %d failed" % i)
        return json.loads(r.stdout)

    try:
        with ThreadPoolExecutor(workers) as ex:
            parts = list(ex.map(work, range(workers)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    order = {n: i for i, n in enumerate(names)}
    merged = dict(parts[0], channels=[], missing_channels=[])
    for part in parts:
        merged["channels"] += part["channels"]
        merged["missing_channels"] += part["missing_channels"]
    merged["channels"].sort(key=lambda c: order[c["channel"]])
    merged["missing_channels"].sort(key=order.get)
    return merged


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login", action="store_true", help="sign in once (opens browser)")
    p.add_argument("--hours", type=float, default=None,
                   help="last N hours (default: yesterday 05:00 - today 05:00)")
    p.add_argument("--date", help="YYYY-MM-DD: that local day")
    p.add_argument("--start", help="YYYY-MM-DD range start")
    p.add_argument("--end", help="YYYY-MM-DD range end")
    p.add_argument("--no-threads", action="store_true", help="skip thread fetching")
    p.add_argument("--keyword", action="append", default=[],
                   help="only keep threads/messages containing this word (repeatable)")
    p.add_argument("--show", action="store_true", help="show the browser (debug)")
    p.add_argument("--config", default=os.path.join(SKILL_DIR, "config.json"))
    p.add_argument("--channel", action="append", default=[], help="only this channel name (repeatable)")
    p.add_argument("--workers", type=int, default=5, help="parallel worker processes (1 = sequential)")
    p.add_argument("--profile", help="browser profile dir (used internally by workers)")
    args = p.parse_args()
    if args.profile:
        global PROFILE
        PROFILE = args.profile

    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    urls = cfg.get("channel_urls", {})
    # /archives/ 形式はWebクライアントへリダイレクトされ読み込みが終わらないため、/client/ 形式に変換する
    team = cfg.get("team_id") or next(
        (m.group(1) for u in urls.values() if (m := re.search(r"/client/([A-Z0-9]+)/", u))), None)
    if team:
        urls = {n: re.sub(r"^https://[^/]+/archives/([A-Z0-9]+).*$",
                          r"https://app.slack.com/client/%s/\1" % team, u)
                for n, u in urls.items()}

    if not team and any("/archives/" in u for u in urls.values()):
        sys.exit("/archives/ 形式のURLを使うには config.json に team_id (例: EE36FT5GU) を設定してください")

    if args.channel:
        urls = {n: u for n, u in urls.items() if n in args.channel}
    elif args.workers > 1 and len(urls) > 1 and not args.login:
        argv = [a for a in sys.argv[1:] if a != "--login"]
        for flag in ("--workers",):
            if flag in argv:
                i = argv.index(flag)
                del argv[i:i + 2]
        json.dump(run_parallel(args.workers, list(urls), argv), sys.stdout,
                  ensure_ascii=False, separators=(",", ":"))
        return

    if args.login:
        login(next(iter(urls.values()), None))
        return
    if not urls:
        sys.exit("config.json に channel_urls が未設定です (config.example.json 参照)")

    def day(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d") + timedelta(hours=DAY_START_HOUR)
        except ValueError:
            sys.exit("invalid date (use YYYY-MM-DD): " + s)

    latest = None
    if args.date:
        d = day(args.date)
        oldest, latest = d.timestamp(), (d + timedelta(days=1)).timestamp()
    elif args.start or args.end:
        s, e = day(args.start or args.end), day(args.end or args.start)
        if e < s:
            sys.exit("--end must not be before --start")
        oldest, latest = s.timestamp(), (e + timedelta(days=1)).timestamp()
    elif args.hours is not None:
        oldest = time.time() - args.hours * 3600
    else:
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        d += timedelta(hours=DAY_START_HOUR)
        oldest, latest = d.timestamp(), (d + timedelta(days=1)).timestamp()

    limit = int(cfg.get("max_messages_per_channel", 300))
    flt = cfg.get("filters", {})
    lookback = float(flt.get("thread_lookback_days", 7)) * 86400
    max_threads = int(flt.get("max_threads_per_channel", 50))
    min_replies = int(flt.get("min_thread_replies", 1))
    max_chars = int(flt.get("max_text_chars", 0))
    max_replies = int(flt.get("max_replies_per_thread", 100))
    exclude_kw = flt.get("exclude_keywords", [])
    exclude_users = flt.get("exclude_users", [])
    include_kw = list(flt.get("include_keywords", [])) + args.keyword

    def clip(t):
        return t if max_chars <= 0 or len(t) <= max_chars else t[:max_chars] + "…"

    drop_empty = flt.get("drop_empty_posts", True)
    context_chars = int(flt.get("earlier_reply_chars", 0))

    def ok_msg(m):
        return not (hit(m["text"], exclude_kw) or any(u.lower() in (m["user"] or "").lower()
                                                      for u in exclude_users))
    workspace_url = cfg.get("workspace_url", "").rstrip("/")
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "from": datetime.fromtimestamp(oldest).isoformat(timespec="minutes"),
        "to": datetime.fromtimestamp(latest or time.time()).isoformat(timespec="minutes"),
        "missing_channels": [], "channels": []}

    with sync_playwright() as pw:
        ctx = launch(pw, headless=not args.show)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for name, url in urls.items():
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector('[data-qa="virtual-list-item"]', state="attached", timeout=30000)
            except Exception:
                if "signin" in page.url or "workspace-signin" in page.url:
                    ctx.close()
                    sys.exit("未ログインです。先に --login を実行してください")
                result["missing_channels"].append(name)
                continue
            page.wait_for_timeout(1500)
            ids = re.search(r"/client/([A-Z0-9]+)/([A-Z0-9]+)", url)
            cid = ids.group(2) if ids else ""
            base = "https://app.slack.com/client/%s/%s" % (ids.group(1), cid) if ids else url
            hi = latest or float("inf")
            seen = scroll_collect(page, MAIN_ROOT, oldest - (0 if args.no_threads else lookback))
            parents = sorted(seen.values(), key=lambda m: float(m["ts"]))

            def link(ts):
                if workspace_url and cid:
                    return "%s/archives/%s/p%s" % (workspace_url, cid, ts.replace(".", ""))
                return url

            def fmt(m):
                return {"time": datetime.fromtimestamp(float(m["ts"])).strftime("%m-%d %H:%M"),
                        "user": short_name(m["user"]),
                        "text": clip(compact_text(re.sub(r"\n{2,}", "\n", m["text"])))}

            def blank(m):
                return drop_empty and not m["text"].strip() and not m["replies"]

            posts = {}
            for m in parents:
                if oldest <= float(m["ts"]) < hi and ok_msg(m) and not blank(m):
                    posts[m["ts"]] = dict(fmt(m), replies=m["replies"], reactions=m["reactions"],
                                          permalink=link(m["ts"]), new_post=True, thread=[])
            if not args.no_threads:
                cands = [m for m in parents if m["replies"] >= min_replies
                         and float(m["ts"]) >= oldest - lookback and ok_msg(m)]
                cands.sort(key=lambda m: float(m["ts"]), reverse=True)
                for m in cands[:max_threads]:
                    msgs = fetch_thread(page, base, cid, m["ts"])
                    replies = [r for r in msgs if r["ts"] != m["ts"] and ok_msg(r)]
                    active = [r for r in replies if oldest <= float(r["ts"]) < hi]
                    if not active and m["ts"] not in posts:
                        continue
                    entry = posts.get(m["ts"]) or dict(
                        fmt(m), replies=m["replies"], reactions=m["reactions"],
                        permalink=link(m["ts"]), new_post=False, thread=[])
                    entry["last_reply"] = datetime.fromtimestamp(
                        float(replies[-1]["ts"])).strftime("%m-%d %H:%M") if replies else None
                    entry["active_replies_in_period"] = len(active)
                    if entry["new_post"]:
                        shown = replies if len(replies) <= max_replies else \
                            replies[:3] + replies[-(max_replies - 3):]
                    else:
                        # 継続スレッドは期間内の返信のみ全文。それ以前は短く文脈として残す
                        shown = active if len(active) <= max_replies else \
                            active[:3] + active[-(max_replies - 3):]
                        earlier = [r for r in replies if float(r["ts"]) < oldest]
                        if earlier:
                            entry["earlier_context"] = [
                                dict(fmt(r), text=fmt(r)["text"][:context_chars] if context_chars > 0 else fmt(r)["text"]) for r in earlier]
                    entry["thread"] = [fmt(r) for r in shown]
                    posts[m["ts"]] = entry
            out = sorted(posts.values(), key=lambda e: e["time"])
            if include_kw:
                out = [e for e in out if hit(e["text"] + " ".join(r["text"] for r in e["thread"]),
                                             include_kw)]
            out = out[-limit:]
            for e in out:
                if not e["reactions"]:
                    del e["reactions"]
                if e["new_post"]:
                    del e["new_post"]
                if not e["thread"]:
                    del e["thread"]
            result["channels"].append({"channel": name, "messages": out})
        ctx.close()

    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
