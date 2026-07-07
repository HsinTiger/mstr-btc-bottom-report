#!/usr/bin/env python3
"""
mnav_calc.py — 自算 mNAV 與框架觸發指標
用法:
    python mnav_calc.py                # 讀取 inputs.yaml + 自動抓價
    python mnav_calc.py --offline      # 全手動（inputs.yaml 需含價格）

設計原則（見 metrics-spec.md）:
- 8-K/10-Q 的數字為手動輸入（inputs.yaml），強迫人工核實 EDGAR 原文
- 只有市場價格自動抓取（Yahoo Finance / CoinGecko）
- 官網 mNAV 不參與任何計算，僅供人工對照
"""

import argparse
import json
import sys
import urllib.request

# ---------- 手動輸入區（每週一更新自最新 8-K，每季用 10-Q 回校） ----------
# 也可外部化為 inputs.yaml；為降低依賴，預設內嵌並標註資料日期。
INPUTS = {
    "as_of": "2026-07-05",           # 8-K 資料截止日
    "btc_holdings": 843_775,          # BTC 持倉（顆）
    "usd_reserve_musd": 2_550,        # USD Reserve（百萬美元）
    "cash_other_musd": 0,             # Reserve 以外現金（10-Q）
    "debt_face_musd": 8_214,          # 可轉債面額合計（10-Q 核實）
    "annual_interest_musd": 34,       # 債務年利息（10-Q 核實）
    # 特別股各系列：清算面額總額（musd）與股息率
    "preferred": {
        "STRF": {"notional_musd": 3_700, "rate": 0.10},
        "STRC": {"notional_musd": 7_800, "rate": 0.12},   # 變動率，週更
        "STRK": {"notional_musd": 2_100, "rate": 0.08},
        "STRD": {"notional_musd": 4_200, "rate": 0.10},
    },
    "diluted_shares_m": 285.0,        # assumed diluted 股數（百萬）
    "weekly_btc_sales_musd": 216.0,   # 本週賣幣所得（8-K）
    "prev_pref_notional_musd": 17_800, # 上期特別股面額合計（M3 旗標用）
    "prev_mnav_equity": 0.62,          # 上期自算 equity mNAV（M3 旗標用）
}

WEEKLY_OBLIGATION_MUSD = None  # 由年化義務推導，見下


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get_price_yahoo(ticker: str) -> float:
    d = fetch_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
    )
    return d["chart"]["result"][0]["meta"]["regularMarketPrice"]


def get_btc_price() -> float:
    d = fetch_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    )
    return d["bitcoin"]["usd"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--offline", action="store_true")
    p.add_argument("--mstr", type=float, help="MSTR 價格（offline 用）")
    p.add_argument("--btc", type=float, help="BTC 價格（offline 用）")
    p.add_argument("--strc", type=float, help="STRC 價格（offline 用）")
    a = p.parse_args()

    if a.offline:
        if not all([a.mstr, a.btc, a.strc]):
            sys.exit("offline 模式需 --mstr --btc --strc")
        mstr_px, btc_px, strc_px = a.mstr, a.btc, a.strc
    else:
        mstr_px = get_price_yahoo("MSTR")
        strc_px = get_price_yahoo("STRC")
        btc_px = get_btc_price()

    i = INPUTS
    btc_nav = i["btc_holdings"] * btc_px / 1e6           # musd
    mkt_cap = i["diluted_shares_m"] * mstr_px            # musd
    pref_total = sum(s["notional_musd"] for s in i["preferred"].values())
    cash = i["usd_reserve_musd"] + i["cash_other_musd"]

    # M1 equity mNAV（保守）
    net_to_common = btc_nav + cash - i["debt_face_musd"] - pref_total
    m1 = mkt_cap / net_to_common if net_to_common > 0 else float("nan")

    # M2 enterprise mNAV（官方同構自算版）
    m2 = (mkt_cap + i["debt_face_musd"] + pref_total) / btc_nav

    # M3 特別股稀釋旗標
    pref_increased = pref_total > i["prev_pref_notional_musd"]
    mnav_recovered = (m1 == m1) and m1 > i["prev_mnav_equity"]  # nan-safe
    m3_flag = pref_increased and mnav_recovered

    # M4 覆蓋月數
    annual_div = sum(
        s["notional_musd"] * s["rate"] for s in i["preferred"].values()
    )
    annual_obligation = annual_div + i["annual_interest_musd"]
    m4 = i["usd_reserve_musd"] / (annual_obligation / 12)

    # M5 週賣幣比值
    weekly_need = annual_obligation / 52
    m5 = i["weekly_btc_sales_musd"] / weekly_need

    # M6 sats/股
    m6 = i["btc_holdings"] * 1e8 / (i["diluted_shares_m"] * 1e6)

    # M7 STRC 折價
    m7 = 1 - strc_px / 100

    def status(ok: bool, warn: bool = False) -> str:
        return "[OK]" if ok else ("[WARN]" if warn else "[FAIL]")

    print(f"=== 自算指標 @ prices {i['as_of']}-data ===")
    print(f"BTC ${btc_px:,.0f} | MSTR ${mstr_px:,.2f} | STRC ${strc_px:.2f}")
    print(f"BTC NAV: ${btc_nav/1000:,.1f}B | 特別股面額: ${pref_total/1000:,.1f}B")
    print()
    if net_to_common <= 0:
        print("M1 equity mNAV: N/A(insolvent-to-common) [FAIL]")
    else:
        print(f"M1 equity mNAV: {m1:.2f}x {status(m1 >= 1.0)}")
    print(f"M2 enterprise mNAV: {m2:.2f}x {status(m2 >= 1.0)}")
    print(f"M3 特別股稀釋旗標: {'觸發（mNAV回升訊號打五折）[WARN]' if m3_flag else '未觸發 [OK]'}")
    print(f"M4 覆蓋月數: {m4:.1f} 月 {status(m4 >= 12)}")
    print(f"M5 週賣幣比值: {m5:.1f}x (基準 ${weekly_need:.0f}M/週) "
          f"{status(m5 <= 1.5, warn=m5 <= 2)}")
    print(f"M6 sats/股: {m6:,.0f}")
    print(f"M7 STRC 折價: {m7:.1%} {status(m7 <= 0.01, warn=m7 <= 0.05)}")
    print()
    both_ok = (net_to_common > 0 and m1 >= 1.0 and m2 >= 1.0 and not m3_flag)
    print(f"第2等份3（雙軌 mNAV>=1 且無稀釋旗標）: {status(both_ok)}")
    print("※ 年化義務自算: "
          f"${annual_obligation:,.0f}M（股息 ${annual_div:,.0f}M + 利息 {i['annual_interest_musd']}M）")
    print("※ 官網 mNAV 僅供對照，不參與判定。下單前人工核實 EDGAR。")


if __name__ == "__main__":
    main()
