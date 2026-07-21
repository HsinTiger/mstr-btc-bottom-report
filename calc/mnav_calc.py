#!/usr/bin/env python3
"""
mnav_calc.py — 自算 MSTR 普通股估值與資本結構指標
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
    "as_of": "2026-07-06",           # Strategy holdings 最新官方 ledger 日；各欄位仍須看各自 provenance
    "btc_holdings": 843_775,          # BTC 持倉（顆）
    "usd_reserve_musd": 2_550,        # USD Reserve（百萬美元）
    "cash_other_musd": 0,
    "deferred_tax_liability_musd": 1_922,  # 2025-12-31 SEC DeferredTaxLiabilities；情境中固定、不稱淨額
    "debt_face_musd": 8_214,          # 可轉債面額合計（10-Q 核實）
    "annual_interest_musd": 34,       # 債務年利息（10-Q 核實）
    # 特別股各系列：清算面額總額（musd）與股息率
    "preferred": {
        "STRF": {"notional_musd": 3_700, "rate": 0.10},
        "STRC": {"notional_musd": 7_800, "rate": 0.12},   # 變動率，週更
        "STRK": {"notional_musd": 2_100, "rate": 0.08},
        "STRD": {"notional_musd": 4_200, "rate": 0.10},
    },
    "common_shares_outstanding_m": 350.448,  # 10-Q/10-K 封面實際流通普通股（百萬）
    "weekly_btc_sales_musd": None,    # 最新揭露無法覆蓋最近 7 日；未知不得當 0
    "prev_pref_notional_musd": 17_800, # 上期特別股面額合計（融資扭曲旗標用）
    "prev_mnav_equity": 0.62,          # 上期普通股 Price/NAV（融資扭曲旗標用）
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
    mkt_cap = i["common_shares_outstanding_m"] * mstr_px  # musd
    pref_total = sum(s["notional_musd"] for s in i["preferred"].values())
    cash = i["usd_reserve_musd"] + i["cash_other_musd"]

    # 普通股市值／普通股淨值（保守）
    net_to_common = btc_nav + cash - i["debt_face_musd"] - pref_total - i["deferred_tax_liability_musd"]
    common_price_to_nav = mkt_cap / net_to_common if net_to_common > 0 else float("nan")

    # 企業價值／BTC 總值（官方同構自算版）
    enterprise_value_to_btc_nav = (mkt_cap + i["debt_face_musd"] + pref_total - cash) / btc_nav

    # 特別股融資扭曲旗標
    pref_increased = pref_total > i["prev_pref_notional_musd"]
    price_to_nav_increased = (common_price_to_nav == common_price_to_nav) and common_price_to_nav > i["prev_mnav_equity"]
    preferred_distortion_flag = pref_increased and price_to_nav_increased

    # 明示固定義務覆蓋月數
    annual_div = sum(
        s["notional_musd"] * s["rate"] for s in i["preferred"].values()
    )
    annual_obligation = annual_div + i["annual_interest_musd"]
    coverage_months = i["usd_reserve_musd"] / (annual_obligation / 12)

    # 7 日賣幣壓力倍數
    weekly_need = annual_obligation / 52
    sale_pressure_ratio = i["weekly_btc_sales_musd"] / weekly_need if i["weekly_btc_sales_musd"] is not None else None

    # 每股比特幣含量
    sats_per_share = i["btc_holdings"] * 1e8 / (i["common_shares_outstanding_m"] * 1e6)

    # STRC 優先股折價信任票
    strc_discount = 1 - strc_px / 100

    def status(ok: bool, warn: bool = False) -> str:
        return "[OK]" if ok else ("[WARN]" if warn else "[FAIL]")

    print(f"=== 自算指標 @ prices {i['as_of']}-data ===")
    print(f"BTC ${btc_px:,.0f} | MSTR ${mstr_px:,.2f} | STRC ${strc_px:.2f}")
    print(f"BTC NAV: ${btc_nav/1000:,.1f}B | 特別股面額: ${pref_total/1000:,.1f}B")
    print()
    if net_to_common <= 0:
        print("普通股市值／普通股淨值: N/A(insolvent-to-common) [FAIL]")
    else:
        print(f"普通股市值／普通股淨值: {common_price_to_nav:.2f}x {status(common_price_to_nav <= 1.0)}（愈低愈便宜）")
    print(f"企業價值／BTC 總值: {enterprise_value_to_btc_nav:.2f}x（融資飛輪參考，不是便宜度）")
    print(f"特別股融資扭曲旗標: {'觸發（Price/NAV 上升可能來自分母縮水）[WARN]' if preferred_distortion_flag else '未觸發 [OK]'}")
    print(f"明示固定義務覆蓋月數: {coverage_months:.1f} 月 {status(coverage_months >= 12)}")
    if sale_pressure_ratio is None:
        print(f"每週賣幣壓力倍數: 未知 (基準 ${weekly_need:.0f}M/週) [FAIL CLOSED]")
    else:
        print(f"每週賣幣壓力倍數: {sale_pressure_ratio:.1f}x (基準 ${weekly_need:.0f}M/週) "
              f"{status(sale_pressure_ratio <= 1.5, warn=sale_pressure_ratio <= 2)}")
    print(f"每股比特幣含量: {sats_per_share:,.0f} sats")
    print(f"STRC 優先股折價信任票: {strc_discount:.1%} {status(strc_discount <= 0.01, warn=strc_discount <= 0.05)}")
    print()
    valuation_ok = net_to_common > 0 and common_price_to_nav <= 1.0 and not preferred_distortion_flag
    flywheel_ok = net_to_common > 0 and common_price_to_nav >= 1.0 and enterprise_value_to_btc_nav >= 1.0 and not preferred_distortion_flag
    print(f"普通股折價條件: {status(valuation_ok)}")
    print(f"資本飛輪條件: {status(flywheel_ok)}（不代表普通股便宜）")
    print("※ 年化義務自算: "
          f"${annual_obligation:,.0f}M（股息 ${annual_div:,.0f}M + 利息 {i['annual_interest_musd']}M）")
    print("※ 官網 mNAV 僅供對照，不參與判定。下單前人工核實 EDGAR。")


if __name__ == "__main__":
    main()
