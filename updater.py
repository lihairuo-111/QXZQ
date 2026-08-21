# -*- coding: utf-8 -*-
"""
A股情绪周期监控 V3 - 数据更新器
数据源: AKShare (东方财富/新浪等)
功能: 取数 -> 计算指标 -> 生成 latest.json / history.json / status.json

设计原则:
- 单个指标失败不影响整体 (每个数据源独立 try/except)
- 非交易日不伪装今日数据
- 同一交易日多次运行只保留一条历史记录
"""
import json
import os
import sys
import traceback
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "site", "data")
LATEST = os.path.join(DATA_DIR, "latest.json")
HISTORY = os.path.join(DATA_DIR, "history.json")
STATUS = os.path.join(DATA_DIR, "status.json")
CONFIG = os.path.join(BASE, "config.json")

with open(CONFIG, "r", encoding="utf-8") as f:
    CFG = json.load(f)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def log(msg):
    print(f"[{now_str()}] {msg}", flush=True)


# ---------------- 交易日判断 ----------------
def is_trading_day(d_str):
    try:
        hist = ak.tool_trade_date_hist_sina()
        dates = set(str(x).replace("-", "") for x in hist["trade_date"].tolist())
        return d_str.replace("-", "") in dates
    except Exception as e:
        log(f"交易日历获取失败({e}), 按周末判断")
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        return dt.weekday() < 5


def last_trade_day(d_str):
    """向前找最近交易日"""
    dt = datetime.strptime(d_str, "%Y-%m-%d")
    for _ in range(15):
        dt -= timedelta(days=1)
        if is_trading_day(dt.strftime("%Y-%m-%d")):
            return dt.strftime("%Y-%m-%d")
    return d_str


# ---------------- 数据源 (每个独立异常处理) ----------------
def fetch_spot():
    """全市场实时行情 -> 涨跌家数 / 成交额 (带重试)"""
    import time
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot_em()
            pct = df["涨跌幅"]
            up = int((pct > 0).sum())
            down = int((pct < 0).sum())
            flat = int((pct == 0).sum())
            total = up + down + flat
            amt = float(df["成交额"].sum())
            return {"up": up, "down": down, "flat": flat, "total": total, "amount": amt, "ok": True}
        except Exception as e:
            log(f"spot 第{attempt+1}次取数失败: {e}")
            if attempt < 2:
                time.sleep(5)
    log("spot 重试用尽, 使用默认值")
    return {"ok": False, "up": 0, "down": 0, "flat": 0, "total": 0, "amount": 0.0}


def fetch_zt_pool(d):
    """涨停池 -> 涨停数 / 最高连板"""
    try:
        df = ak.stock_zt_pool_em(date=d)
        if df is None or len(df) == 0:
            return {"count": 0, "max_board": 0, "ok": True, "sectors": []}
        cnt = len(df)
        mb = 0
        if "连板数" in df.columns:
            mb = int(df["连板数"].max())
        secs = []
        if "所属行业" in df.columns:
            secs = df["所属行业"].value_counts().head(5).to_dict()
        return {"count": cnt, "max_board": mb, "ok": True, "sectors": secs}
    except Exception as e:
        log(f"涨停池取数失败: {e}")
        return {"ok": False, "count": 0, "max_board": 0, "sectors": []}


def fetch_dt_pool(d):
    try:
        df = ak.stock_zt_pool_dtgc_em(date=d)
        return {"count": 0 if df is None else len(df), "ok": True}
    except Exception as e:
        log(f"跌停池取数失败: {e}")
        return {"ok": False, "count": 0}


def fetch_zbgc_pool(d):
    """炸板池 -> 炸板数"""
    try:
        df = ak.stock_zt_pool_zbgc_em(date=d)
        return {"count": 0 if df is None else len(df), "ok": True}
    except Exception as e:
        log(f"炸板池取数失败: {e}")
        return {"ok": False, "count": 0}


def fetch_prev_zt(d):
    """昨日涨停今日表现 -> 平均涨跌幅"""
    try:
        df = ak.stock_zt_pool_previous_em(date=d)
        if df is None or len(df) == 0:
            return {"avg": 0.0, "ok": True}
        return {"avg": round(float(df["涨跌幅"].mean()), 2), "ok": True}
    except Exception as e:
        log(f"昨涨停表现取数失败: {e}")
        return {"ok": False, "avg": 0.0}


def fetch_index_trend():
    """上证指数 MA5/MA10/MA20 趋势"""
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        df = df.tail(25).copy()
        closes = df["close"].values
        ma5 = float(pd.Series(closes).rolling(5).mean().iloc[-1])
        ma10 = float(pd.Series(closes).rolling(10).mean().iloc[-1])
        ma20 = float(pd.Series(closes).rolling(20).mean().iloc[-1])
        last_close = float(closes[-1])
        prev_close = float(closes[-2])
        chg = round((last_close / prev_close - 1) * 100, 2)
        if ma5 > ma10 > ma20:
            trend = "多头排列"
            score = 100
        elif ma5 < ma10 < ma20:
            trend = "空头排列"
            score = 0
        else:
            trend = "震荡"
            score = 50
        return {"close": round(last_close, 2), "chg": chg, "ma5": round(ma5, 2),
                "ma10": round(ma10, 2), "ma20": round(ma20, 2),
                "trend": trend, "score": score, "ok": True}
    except Exception as e:
        log(f"上证趋势取数失败: {e}")
        return {"ok": False, "trend": "未知", "score": 50, "close": 0, "chg": 0}


# ---------------- 指标归一化 0-100 ----------------
def n_breadth(spot):
    if not spot["ok"] or spot["total"] == 0:
        return 50.0
    return round(spot["up"] / spot["total"] * 100, 1)


def n_limit_up(zt):
    if not zt["ok"]:
        return 50.0
    return round(min(zt["count"] / 1.2, 100), 1)


def n_limit_down(dt):
    if not dt["ok"]:
        return 50.0
    return round(max(100 - dt["count"] * 2, 0), 1)


def n_broken(zt, zbgc):
    if not zt["ok"] or not zbgc["ok"]:
        return 50.0
    total = zt["count"] + zbgc["count"]
    if total == 0:
        return 80.0
    rate = zbgc["count"] / total
    return round(max(100 - rate * 200, 0), 1)


def n_prev_feedback(prev):
    if not prev["ok"]:
        return 50.0
    return round(max(0, min(100, (prev["avg"] + 5) / 10 * 100)), 1)


def n_max_board(zt):
    if not zt["ok"]:
        return 50.0
    mb = zt["max_board"]
    return round(max(0, min(100, (mb - 1) / 7 * 100 + 20)), 1)


def n_volume(spot, hist_amounts):
    if not spot["ok"] or not hist_amounts:
        return 50.0
    ma = sum(hist_amounts) / len(hist_amounts) if hist_amounts else spot["amount"]
    if ma == 0:
        return 50.0
    ratio = spot["amount"] / ma
    return round(max(0, min(100, (ratio - 0.5) / 1.5 * 100)), 1)


def n_index(idx):
    if not idx["ok"]:
        return 50.0
    return idx["score"]


# ---------------- 情绪温度 ----------------
def calc_temperature(parts, weights):
    total_w = sum(weights.values())
    t = sum(parts[k] * weights.get(k, 0) for k in parts)
    return round(t / total_w, 1)


# ---------------- 周期阶段 ----------------
def determine_phase(temp):
    c = CFG["cycle"]
    if temp < c["freezing"][1]:
        if temp < c["panic"][1]:
            return "极度恐慌"
        return "情绪冰点"
    if temp < c["repair"][1]:
        return "修复阶段"
    if temp < c["active"][1]:
        return "活跃加速"
    return "亢奋退潮"


# ---------------- 入场机会分 ----------------
def calc_entry_score(temp, chg):
    if temp < 20:
        base = 70 + (20 - temp) * 0.8
        if chg < 0:
            base -= 20
        else:
            base += 10
    elif temp < 35:
        base = 78
        if chg > 0:
            base += 8
        elif chg < 0:
            base -= 12
    elif temp < 55:
        base = 60
    elif temp < 75:
        base = 38
    else:
        base = 15
    return int(max(0, min(100, base)))


# ---------------- 策略信号 ----------------
def strategy(phase, chg):
    if phase == "极度恐慌":
        return "观察为主" if chg < 0 else "试仓窗口"
    if phase == "情绪冰点":
        return "试仓窗口" if chg >= 0 else "重点观察"
    if phase == "修复阶段":
        return "持有为主"
    if phase == "活跃加速":
        return "顺势持有"
    return "控制仓位"


# ---------------- 历史读写 ----------------
def load_history():
    if os.path.exists(HISTORY):
        try:
            with open(HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_history(records):
    keep = CFG.get("history_keep_days", 30)
    records = records[-keep:]
    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def update_history(rec, trade_day):
    records = load_history()
    # 同一交易日只保留一条 (17:10 覆盖 16:20)
    records = [r for r in records if r.get("date") != trade_day]
    records.append(rec)
    records.sort(key=lambda x: x.get("date", ""))
    save_history(records)
    return records


def prev_temp(records, trade_day):
    earlier = [r for r in records if r.get("date") < trade_day]
    if not earlier:
        return None
    return earlier[-1].get("temperature")


def hist_amounts(records, trade_day, window=5):
    earlier = [r for r in records if r.get("date") < trade_day]
    return [r.get("amount", 0) for r in earlier[-window:]]


# ---------------- 主流程 ----------------
def main():
    log("=" * 50)
    log("A股情绪周期监控 V3 - 开始更新")
    t_day = today_str()
    trading = is_trading_day(t_day)
    actual_day = t_day if trading else last_trade_day(t_day)
    d_yyyymmdd = actual_day.replace("-", "")
    log(f"今日 {t_day} 交易日={trading} 取数日={actual_day}")

    status = {
        "today": t_day,
        "trade_day": actual_day,
        "is_trading_day": trading,
        "last_update": now_str(),
        "success": False,
        "error": None,
        "note": "非交易日,使用最近交易日数据" if not trading else "正常更新",
    }

    try:
        spot = fetch_spot()
        zt = fetch_zt_pool(d_yyyymmdd)
        dt = fetch_dt_pool(d_yyyymmdd)
        zbgc = fetch_zbgc_pool(d_yyyymmdd)
        prev = fetch_prev_zt(d_yyyymmdd)
        idx = fetch_index_trend()

        records = load_history()
        pt = prev_temp(records, actual_day)
        h_amt = hist_amounts(records, actual_day, CFG.get("volume_ma_window", 5))

        parts = {
            "breadth": n_breadth(spot),
            "limit_up": n_limit_up(zt),
            "limit_down": n_limit_down(dt),
            "broken_rate": n_broken(zt, zbgc),
            "prev_zt_feedback": n_prev_feedback(prev),
            "max_board": n_max_board(zt),
            "volume_active": n_volume(spot, h_amt),
            "index_trend": n_index(idx),
        }
        temp = calc_temperature(parts, CFG["weights"])
        chg = round(temp - pt, 1) if pt is not None else 0.0
        phase = determine_phase(temp)
        entry = calc_entry_score(temp, chg)
        sig = strategy(phase, chg)

        broken_rate = 0.0
        if zt["ok"] and zbgc["ok"] and (zt["count"] + zbgc["count"]) > 0:
            broken_rate = round(zbgc["count"] / (zt["count"] + zbgc["count"]) * 100, 1)

        latest = {
            "trade_day": actual_day,
            "update_time": now_str(),
            "temperature": temp,
            "prev_temperature": pt,
            "change": chg,
            "phase": phase,
            "entry_score": entry,
            "signal": sig,
            "indicators": {
                "up": spot["up"], "down": spot["down"], "flat": spot["flat"],
                "total": spot["total"],
                "breadth_pct": round(spot["up"] / spot["total"] * 100, 1) if spot["total"] else 0,
                "limit_up": zt["count"], "limit_down": dt["count"],
                "max_board": zt["max_board"],
                "broken_count": zbgc["count"], "broken_rate": broken_rate,
                "prev_zt_feedback": prev["avg"],
                "amount_yi": round(spot["amount"] / 1e8, 0) if spot["ok"] else 0,
                "index_close": idx["close"], "index_chg": idx["chg"],
                "index_trend": idx["trend"], "ma5": idx["ma5"], "ma10": idx["ma10"], "ma20": idx["ma20"],
            },
            "parts": parts,
            "hot_sectors": zt.get("sectors", []),
            "data_source": "AKShare",
        }

        with open(LATEST, "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=2)
        log(f"latest.json 已写入, 温度={temp} 阶段={phase} 入场={entry} 信号={sig}")

        hist_rec = {
            "date": actual_day,
            "temperature": temp,
            "change": chg,
            "phase": phase,
            "entry_score": entry,
            "signal": sig,
            "limit_up": zt["count"], "limit_down": dt["count"],
            "max_board": zt["max_board"], "broken_rate": broken_rate,
            "amount_yi": round(spot["amount"] / 1e8, 0) if spot["ok"] else 0,
            "index_chg": idx["chg"],
        }
        all_rec = update_history(hist_rec, actual_day)
        log(f"history.json 已更新, 共 {len(all_rec)} 条记录")

        status["success"] = True
        status["temperature"] = temp
        status["phase"] = phase
    except Exception as e:
        status["error"] = str(e)
        status["traceback"] = traceback.format_exc()
        log(f"更新失败: {e}")
        log(traceback.format_exc())

    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    log(f"status.json 已写入 success={status['success']}")
    log("=" * 50)
    return 0 if status["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
