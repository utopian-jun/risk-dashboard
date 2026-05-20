"""data_updater.py — 시장 데이터를 크롤링해 market_data.json 으로 저장.

GitHub Actions 가 매일 실행하며, app.py(Streamlit 대시보드)는 이 결과 파일만
읽어서 화면에 표시한다. 따라서 이 스크립트는 streamlit 의존성이 없으며
CI(ubuntu) 환경에서 단독 실행 가능하다.

수집 지표(라이브 5개):
    VIX, 10년물 국채금리, S&P500 200일 이격도, Fear & Greed Index, Market Breadth
    (Put/Call Ratio, Margin Debt MoM 은 app.py 사이드바 수동 입력 → 여기서 제외)
"""

import os
import re
import json
import platform
import subprocess
import tempfile
from datetime import datetime, timezone

import requests

# 결과 파일은 이 스크립트와 같은 디렉터리에 저장 (CI/로컬 모두 동일하게 동작)
OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "market_data.json"
)


def _setup_ca_bundle():
    """macOS 키체인의 루트 CA 인증서를 PEM 으로 추출해 CURL_CA_BUNDLE 로 지정.

    yfinance 의 HTTP 백엔드(curl_cffi)는 기본 번들로는 일부 사내망/보안 환경의
    TLS 검사 루트 CA 를 신뢰하지 못해 SSL 인증서 에러가 난다. macOS 에서만
    동작하며, GitHub Actions(ubuntu)에서는 즉시 반환되어 영향이 없다.
    """
    if platform.system() != "Darwin" or os.environ.get("CURL_CA_BUNDLE"):
        return
    try:
        bundle = os.path.join(tempfile.gettempdir(), "risk_dashboard_ca.pem")
        keychains = [
            "/System/Library/Keychains/SystemRootCertificates.keychain",
            "/Library/Keychains/System.keychain",
        ]
        pem_parts = []
        for kc in keychains:
            out = subprocess.run(
                ["security", "find-certificate", "-a", "-p", kc],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode == 0 and out.stdout.strip():
                pem_parts.append(out.stdout)
        if pem_parts:
            with open(bundle, "w") as fp:
                fp.write("\n".join(pem_parts))
            os.environ["CURL_CA_BUNDLE"] = bundle
    except Exception:
        pass


_setup_ca_bundle()

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}


# ======================================================================
# 공통 헬퍼
# ======================================================================
def _yahoo_chart(symbol, rng="5d"):
    """Yahoo Finance v8 차트 API 호출 (yfinance 실패 시 폴백 경로)."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={rng}&interval=1d"
    )
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()["chart"]["result"][0]


def _na(extra_delta=None):
    """에러 시 공통 N/A 반환 형식 (raw=None)."""
    return {"value": "N/A", "delta": extra_delta, "raw": None, "status": "error"}


# ======================================================================
# 라이브 데이터 크롤링 함수 (5개)
# ======================================================================
def get_vix():
    """VIX 지수 — yfinance '^VIX'."""
    try:
        import yfinance as yf

        closes = yf.Ticker("^VIX").history(period="5d")["Close"].dropna().tolist()
        cur, prev = closes[-1], closes[-2]
        return {"value": f"{cur:.2f}", "delta": f"{cur - prev:+.2f} (전일 대비)",
                "raw": float(cur), "status": "live"}
    except Exception:
        pass
    try:
        res = _yahoo_chart("%5EVIX", "5d")
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        cur = res["meta"]["regularMarketPrice"]
        prev = closes[-2] if len(closes) >= 2 else cur
        return {"value": f"{cur:.2f}", "delta": f"{cur - prev:+.2f} (전일 대비)",
                "raw": float(cur), "status": "live"}
    except Exception:
        return _na()


def get_us10y():
    """10년물 국채금리 — yfinance '^TNX' (값 자체가 % 수익률)."""
    try:
        import yfinance as yf

        closes = yf.Ticker("^TNX").history(period="5d")["Close"].dropna().tolist()
        cur, prev = closes[-1], closes[-2]
        return {"value": f"{cur:.2f}%", "delta": f"{cur - prev:+.2f}%p (전일 대비)",
                "raw": float(cur), "status": "live"}
    except Exception:
        pass
    try:
        res = _yahoo_chart("%5ETNX", "5d")
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        cur = res["meta"]["regularMarketPrice"]
        prev = closes[-2] if len(closes) >= 2 else cur
        return {"value": f"{cur:.2f}%", "delta": f"{cur - prev:+.2f}%p (전일 대비)",
                "raw": float(cur), "status": "live"}
    except Exception:
        return _na()


def _disparity(closes, cur):
    """현재가 / 200일 이동평균 - 1 (%)."""
    ma200 = sum(closes[-200:]) / len(closes[-200:])
    return (cur / ma200 - 1) * 100


def get_disparity():
    """S&P500 200일 이격도 — SPY 기준."""
    try:
        import yfinance as yf

        closes = yf.Ticker("SPY").history(period="2y")["Close"].dropna().tolist()
        if len(closes) >= 201:
            today = _disparity(closes, closes[-1])
            yest = _disparity(closes[:-1], closes[-2])
            return {"value": f"{today:.2f}%", "delta": f"{today - yest:+.2f}%p (전일 대비)",
                    "raw": float(today), "status": "live"}
    except Exception:
        pass
    try:
        res = _yahoo_chart("SPY", "2y")
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        cur = res["meta"]["regularMarketPrice"]
        if len(closes) >= 201:
            today = _disparity(closes, cur)
            yest = _disparity(closes[:-1], closes[-2])
            return {"value": f"{today:.2f}%", "delta": f"{today - yest:+.2f}%p (전일 대비)",
                    "raw": float(today), "status": "live"}
        return _na()
    except Exception:
        return _na()


def get_fear_and_greed():
    """Fear & Greed Index — fear_and_greed 패키지(CNN), 실패 시 CNN API 직접 호출."""
    try:
        import fear_and_greed

        r = fear_and_greed.get()
        return {"value": f"{r.value:.0f}", "delta": r.description.title(),
                "raw": float(r.value), "status": "live"}
    except Exception:
        pass
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        data = requests.get(url, headers=BROWSER_HEADERS, timeout=15).json()
        fng = data["fear_and_greed"]
        return {"value": f"{fng['score']:.0f}", "delta": str(fng["rating"]).title(),
                "raw": float(fng["score"]), "status": "live"}
    except Exception:
        return _na()


def get_market_breadth():
    """Market Breadth — Finviz 스크리너로 S&P500 중 50일선 상회 비율(%)."""
    try:

        def _count(filt):
            url = f"https://finviz.com/screener.ashx?v=111&f={filt}"
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
            resp.raise_for_status()
            m = re.search(r'"result_count":(\d+)', resp.text)
            if not m:
                m = re.search(r"/\s*(\d+)\s*Total", resp.text)
            if not m:
                raise ValueError("count not found")
            return int(m.group(1))

        above_50ma = _count("idx_sp500,ta_sma50_pa")
        total = _count("idx_sp500")
        pct = above_50ma / total * 100
        return {"value": f"{pct:.1f}%",
                "delta": f"{above_50ma} / {total} 종목 (50일선 상회)",
                "raw": float(pct), "status": "live"}
    except Exception:
        return _na("(크롤링 실패)")


# ======================================================================
# 메인 — 크롤링 후 market_data.json 저장
# ======================================================================
def main():
    print("[data_updater] 시장 데이터 크롤링 시작...")

    indicators = {
        "Fear & Greed Index": get_fear_and_greed(),
        "VIX 지수": get_vix(),
        "10년물 국채금리": get_us10y(),
        "S&P500 200일 이격도": get_disparity(),
        "Market Breadth": get_market_breadth(),
    }

    payload = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_updated_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "indicators": indicators,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    # 실행 로그
    for label, d in indicators.items():
        print(f"  - {label}: {d['value']}  [{d['status']}]")
    print(f"[data_updater] 저장 완료 -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
