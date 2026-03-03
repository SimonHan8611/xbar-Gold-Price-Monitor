#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import sys
import subprocess
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from urllib.parse import urlencode
from http.cookiejar import CookieJar

HTTP_TIMEOUT = 10

STATE_PATH = os.path.expanduser("~/.xbar_gold_banks_state.json")
SELECT_PATH = os.path.expanduser("~/.xbar_gold_selected_bank.txt")
PENDING_PATH = os.path.expanduser("~/.xbar_gold_pending.txt")

BANKS = ("ICBC", "CCB", "CMB", "JD")


# -------------------- state --------------------
def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last": {}, "alert_state": {}, "day": "", "day_base": {}}


def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_selected_bank():
    try:
        s = open(SELECT_PATH, "r", encoding="utf-8").read().strip()
        if s in BANKS:
            return s
    except Exception:
        pass
    return "ICBC"


def set_selected_bank(name: str):
    with open(SELECT_PATH, "w", encoding="utf-8") as f:
        f.write(name)


def set_pending(bank: str):
    try:
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            f.write(bank)
    except Exception:
        pass


def get_pending():
    try:
        return open(PENDING_PATH, "r", encoding="utf-8").read().strip()
    except Exception:
        return ""


def clear_pending():
    try:
        if os.path.exists(PENDING_PATH):
            os.remove(PENDING_PATH)
    except Exception:
        pass


# -------------------- notify --------------------
def notify(title, message):
    safe_title = (title or "").replace('"', "'")
    safe_message = (message or "").replace('"', "'")
    script = 'display notification "{}" with title "{}"'.format(safe_message, safe_title)
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass


# -------------------- http utils --------------------
def parse_json_or_jsonp(text: str):
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group(0)
    return json.loads(text)


def http_post_form(url, form, headers=None):
    data = urlencode(form).encode("utf-8")
    hdr = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if headers:
        hdr.update(headers)
    req = Request(url, data=data, headers=hdr, method="POST")
    return urlopen(req, timeout=HTTP_TIMEOUT).read().decode("utf-8", errors="ignore")


def http_get(url, headers=None, opener=None):
    hdr = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hdr.update(headers)
    req = Request(url, headers=hdr, method="GET")
    if opener:
        return opener.open(req, timeout=HTTP_TIMEOUT).read().decode("utf-8", errors="ignore")
    return urlopen(req, timeout=HTTP_TIMEOUT).read().decode("utf-8", errors="ignore")


# -------------------- bank fetchers --------------------
def fetch_icbc():
    url = "https://mybank.icbc.com.cn/servlet/AsynGetDataServlet"
    raw = http_post_form(
        url,
        {"tranCode": "A00622"},
        headers={"Referer": "https://mybank.icbc.com.cn/"},
    )
    data = parse_json_or_jsonp(raw)
    rf = data.get("rf") or []
    if not rf:
        raise RuntimeError("ICBC: no rf")
    item = rf[0]

    cur = float(item["ActivePrice"])
    sell = float(item.get("SellPrice", cur))
    buy = float(item.get("RegPrice", cur))
    t = data.get("sysdate", "")

    return {"bank": "ICBC", "cur": cur, "buy": buy, "mid": cur, "sell": sell, "time": t, "hint": None}


def fetch_ccb():
    init_url = os.environ.get("VAR_CCB_INIT") or (
        "https://gold3.ccb.com/tran/WCCMainPlatV5?CCB_IBSVersion=V5&SERVLET_NAME=WCCMainPlatV5&TXCODE=NHY000"
    )
    quote_url = os.environ.get("VAR_CCB_QUOTE") or (
        "https://gold3.ccb.com/tran/WCCMainPlatV5?CCB_IBSVersion=V5&SERVLET_NAME=WCCMainPlatV5&TXCODE=NGJS01"
    )

    cj = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cj))

    try:
        _ = http_get(init_url, opener=opener)
    except Exception:
        req = Request(init_url, data=b"", headers={"User-Agent": "Mozilla/5.0"}, method="POST")
        opener.open(req, timeout=HTTP_TIMEOUT).read()

    raw = http_get(quote_url, opener=opener)
    data = parse_json_or_jsonp(raw)

    if not isinstance(data, dict):
        return {"bank": "CCB", "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": "CCB: response not dict"}

    buy = data.get("Cst_Buy_Prc")
    mid = data.get("MdlRate")
    sell = data.get("Cst_Sell_Prc")
    tms = data.get("Tms")

    if buy is None or mid is None or sell is None:
        def find_obj(o):
            if isinstance(o, dict):
                if "Cst_Buy_Prc" in o and "MdlRate" in o and "Cst_Sell_Prc" in o:
                    return o
                for v in o.values():
                    r = find_obj(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for it in o:
                    r = find_obj(it)
                    if r:
                        return r
            return None

        obj = find_obj(data)
        if obj:
            buy = obj.get("Cst_Buy_Prc", buy)
            mid = obj.get("MdlRate", mid)
            sell = obj.get("Cst_Sell_Prc", sell)
            tms = obj.get("Tms", tms)

    if mid is None and buy is None and sell is None:
        return {"bank": "CCB", "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": (raw[:240].replace("\n", " ") + " ...")}

    mid_f = float(mid) if mid is not None else float(buy)
    buy_f = float(buy) if buy is not None else mid_f
    sell_f = float(sell) if sell is not None else mid_f

    return {"bank": "CCB", "cur": mid_f, "buy": buy_f, "mid": mid_f, "sell": sell_f, "time": str(tms or ""), "hint": None}


def fetch_cmb():
    url = "https://m.cmbchina.com/api/rate/gold?no=AU9999"
    raw = http_get(url, headers={"Referer": "https://m.cmbchina.com/"})
    data = parse_json_or_jsonp(raw)

    if data.get("returnCode") != "SUC0000":
        return {"bank": "CMB", "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": f"returnCode={data.get('returnCode')}"}

    body = data.get("body") or {}
    arr = body.get("data") or []
    if not arr:
        return {"bank": "CMB", "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": "no body.data"}

    it = arr[0]
    cur = float(it.get("curPrice"))
    t = body.get("time") or it.get("time") or ""

    return {"bank": "CMB", "cur": cur, "buy": cur, "mid": cur, "sell": cur, "time": str(t), "hint": None}


def fetch_jd():
    """
    京东接口（你给的最新 gw2）：
    https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice?productSku=1961543816

    关键字段：
      resultData.datas.price
      resultData.datas.upAndDownAmt
    """
    url = "https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice?productSku=1961543816"
    raw = http_get(url, headers={"Accept": "application/json"})
    data = parse_json_or_jsonp(raw)

    rd = data.get("resultData") or {}
    datas = rd.get("datas") or {}

    price = None
    up_amt = None
    try:
        price = float(datas.get("price"))
    except Exception:
        price = None

    try:
        up_amt = float(datas.get("upAndDownAmt"))
    except Exception:
        up_amt = None

    if price is None:
        hint = (raw[:240].replace("\n", " ") + " ...") if raw else "JD: empty response"
        return {"bank": "JD", "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": hint}

    # time 字段可能不稳定，尽量取
    t = ""
    for k in ("time", "updateTime", "dateTime", "quoteTime", "ts"):
        if datas.get(k):
            t = str(datas.get(k))
            break
        if rd.get(k):
            t = str(rd.get(k))
            break

    # 把 upAndDownAmt 放在 hint 扩展字段里（菜单子项展示）
    hint = None
    if up_amt is not None:
        hint = f"upAndDownAmt: {up_amt:+.2f}"

    return {"bank": "JD", "cur": price, "buy": price, "mid": price, "sell": price, "time": t, "hint": hint}


# -------------------- helpers --------------------
def fmt(v):
    return "NA" if v is None else f"{v:.2f}"


def arrow_for(delta):
    if delta is None:
        return ""
    if delta > 0:
        return "▲"
    if delta < 0:
        return "▼"
    return "•"


def ansi_colored_change(delta_amt, delta_pct):
    if delta_amt is None or delta_pct is None:
        return ""
    amt_str = f"{delta_amt:+.2f}"
    pct_str = f"{delta_pct:+.2f}%"

    # 关键：这里不能有 " | "
    text = f"({amt_str}, {pct_str})"

    if delta_amt > 0:
        return f"\033[31m{text}\033[0m"  # 红
    if delta_amt < 0:
        return f"\033[32m{text}\033[0m"  # 绿
    return text


# -------------------- main --------------------
def main():
    # 点击切换银行
    if len(sys.argv) >= 3 and sys.argv[1] == "set":
        bank = sys.argv[2]
        if bank in BANKS:
            set_selected_bank(bank)
            set_pending(bank)

    selected = get_selected_bank()
    pending = get_pending()

    state = load_state()
    last = state.get("last", {})
    alert_state = state.get("alert_state", {})

    # 今日基准价：当天第一次运行的价格
    today = time.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"] = today
        state["day_base"] = {}
    day_base = state.setdefault("day_base", {})

    # 报警阈值（可选）
    alert_high = os.environ.get("VAR_ALERT_HIGH", "").strip()
    alert_low = os.environ.get("VAR_ALERT_LOW", "").strip()
    alert_high = float(alert_high) if alert_high else None
    alert_low = float(alert_low) if alert_low else None

    # 拉数据
    results = []
    errors = []
    for fn in (fetch_icbc, fetch_ccb, fetch_cmb, fetch_jd):
        try:
            results.append(fn())
        except Exception as e:
            errors.append(str(e))

    res_map = {r["bank"]: r for r in results}

    # 先更新 day_base（确保今日基准有值）
    for bank in BANKS:
        cur = (res_map.get(bank) or {}).get("cur")
        if cur is not None and day_base.get(bank) is None:
            day_base[bank] = cur

    # 顶栏：极简 + 箭头（相对今日基准）
    cur_item = res_map.get(selected)
    if cur_item and cur_item.get("cur") is not None:
        cur = cur_item["cur"]
        base = day_base.get(selected)
        delta = (cur - base) if (base is not None) else None

        if pending == selected:
            clear_pending()

        print(f"G {cur:.2f} {arrow_for(delta)}")
    else:
        if pending == selected:
            print("G ⏳")
        else:
            print("G NA")

    # 菜单：合并切换+行情，一行显示，只涨跌红绿
    print("---")
    print("Banks (click to switch)")
    script = os.path.abspath(__file__)

    for bank in BANKS:
        r = res_map.get(bank, {"bank": bank, "cur": None, "buy": None, "mid": None, "sell": None, "time": "", "hint": None})
        cur = r.get("cur")

        base = day_base.get(bank)
        delta_amt = None
        delta_pct = None
        if cur is not None and base is not None and base != 0:
            delta_amt = cur - base
            delta_pct = (delta_amt / base) * 100.0

        mark = "✓ " if bank == selected else "  "
        price_str = fmt(cur)

        change_colored = ansi_colored_change(delta_amt, delta_pct)
        if change_colored:
            line = f"{mark}{bank}: {price_str} {change_colored}"
        else:
            line = f"{mark}{bank}: {price_str}"

        print(f"{line} | bash='{script}' param1=set param2={bank} terminal=false refresh=true ansi=true")

        # 详情（子菜单）
        if cur is not None:
            print(f"--base(today): {fmt(base)}")
            if delta_amt is not None and delta_pct is not None:
                print(f"--change: {delta_amt:+.2f} / {delta_pct:+.2f}%")
            print(f"--buy:  {fmt(r.get('buy'))}")
            print(f"--mid:  {fmt(r.get('mid'))}")
            print(f"--sell: {fmt(r.get('sell'))}")
            if r.get("time"):
                print(f"--time: {r['time']}")
        if r.get("hint"):
            print(f"--hint: {r['hint']} | color=yellow")

        # 报警（按当前价，状态变化才弹一次）
        if cur is not None and (alert_high is not None or alert_low is not None):
            prev_state = alert_state.get(bank, "none")
            crossed = "none"

            if alert_high is not None and cur >= alert_high:
                crossed = "high"
            elif alert_low is not None and cur <= alert_low:
                crossed = "low"

            if crossed in ("high", "low") and crossed != prev_state:
                title = f"{bank} Gold Alert"
                if crossed == "high":
                    notify(title, f"{cur:.2f} >= {alert_high:.2f}")
                else:
                    notify(title, f"{cur:.2f} <= {alert_low:.2f}")

            alert_state[bank] = crossed

        # last 仍保存（备用）
        if cur is not None:
            last[bank] = cur

    if errors:
        print("---")
        print("Errors | color=red")
        for e in errors:
            print(f"--{e} | color=red")

    print("---")
    print("Updated: " + time.strftime("%H:%M:%S"))

    state["last"] = last
    state["alert_state"] = alert_state
    state["day_base"] = day_base
    save_state(state)


if __name__ == "__main__":
    main()
