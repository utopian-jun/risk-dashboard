import os
import re
import json
from datetime import datetime

import streamlit as st
import plotly.graph_objects as go

# ----------------------------------------------------------------------
# 4단계: 크롤링/대시보드 분리 구조
#   - 데이터 크롤링은 data_updater.py 가 담당 (GitHub Actions 가 매일 실행)
#   - app.py 는 크롤링하지 않고 market_data.json 을 읽어 화면에만 표시
#   - Put/Call 은 사이드바 수동 입력
#   - Margin Debt 는 margin_history.json 의 월별 Debit Balances 로 MoM 자동 계산
# ----------------------------------------------------------------------

DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "market_data.json"
)
MARGIN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "margin_history.json"
)

# data_updater.py 가 수집하는 라이브 지표 (화면 표시 순서와 무관)
LIVE_LABELS = [
    "Fear & Greed Index",
    "VIX 지수",
    "10년물 국채금리",
    "S&P500 200일 이격도",
    "Market Breadth",
]


def load_market_data():
    """market_data.json 을 읽어 (last_updated, indicators dict) 반환.

    파일이 없거나 손상된 경우 (None, {}) 를 반환해 앱이 죽지 않도록 한다.
    """
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload.get("last_updated"), payload.get("indicators", {})
    except Exception:
        return None, {}


def load_margin_history():
    """margin_history.json 을 읽어 {YYYY-MM: Debit Balances} dict 반환.

    파일이 없거나 손상된 경우 빈 dict 를 반환한다.
    """
    try:
        with open(MARGIN_FILE, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        history = payload.get("history", {})
        return {str(k): float(v) for k, v in history.items()}
    except Exception:
        return {}


def compute_margin_mom(history):
    """월별 Debit Balances dict → (최신 MoM%, 최신월, 직전월).

    데이터가 2개월 미만이거나 직전월 값이 0 이면 (None, ...) 을 반환한다.
    가장 최근 2개 월(키 정렬 기준)을 사용한다.
    """
    months = sorted(history.keys())
    if len(months) < 2:
        return None, None, None
    latest, prev = months[-1], months[-2]
    prev_val = history[prev]
    if prev_val <= 0:
        return None, latest, prev
    mom = (history[latest] - prev_val) / prev_val * 100.0
    return mom, latest, prev


# ======================================================================
# 종합 리스크 점수 산출 알고리즘
#   각 지표를 0~100 위험점수로 정규화 → 가중 합산 → 0~100 클리핑
#   RISK_CONFIG: (라벨, 가중치, at0, at100)
#     at100 값 → 위험점수 100, at0 값 → 위험점수 0 으로 선형 보간
# ======================================================================
RISK_CONFIG = [
    ("Fear & Greed Index",  0.15,   0.0,   100.0),
    ("Put/Call Ratio",      0.15,   1.0,     0.6),
    ("VIX 지수",             0.10,  25.0,    13.0),
    ("10년물 국채금리",       0.15,   3.8,     4.5),
    ("S&P500 200일 이격도",  0.15,   0.0,    15.0),
    ("Market Breadth",      0.15,  40.0,    85.0),
    ("Margin Debt MoM",     0.15,   0.0,    10.0),
]


def _normalize(value, at0, at100):
    """at100 → 100점, at0 → 0점 선형 보간 후 0~100 클리핑.

    데이터가 없으면(None) 중립값 50점을 반환한다.
    """
    if value is None:
        return 50.0
    score = (value - at0) / (at100 - at0) * 100.0
    return max(0.0, min(100.0, score))


def calculate_advanced_risk_score(raw_values):
    """7개 지표 raw 값(dict) → (종합 리스크 점수, 지표별 상세 리스트).

    contribution(기여 점수) = 정규화 점수 * 가중치 이며, 전 지표 기여 점수의
    합이 곧 종합 리스크 점수가 된다.
    """
    details = []
    total = 0.0
    for label, weight, at0, at100 in RISK_CONFIG:
        raw = raw_values.get(label)
        norm = _normalize(raw, at0, at100)
        contribution = norm * weight
        total += contribution
        details.append({
            "label": label, "raw": raw, "norm": norm,
            "weight": weight, "contribution": contribution,
        })
    return max(0.0, min(100.0, total)), details


def _risk_zone(score):
    """점수 → (색상, 라벨). 0~40 안전 / 41~75 주의 / 76~100 위험."""
    if score <= 40:
        return "#2ECC71", "🟢 안전 (Low Risk)"
    if score <= 75:
        return "#F1C40F", "🟡 주의 (Caution)"
    return "#E74C3C", "🔴 위험 (High Risk)"


# ======================================================================
# 페이지 설정
# ======================================================================
st.set_page_config(
    page_title="US Market Multi-Factor Risk Dashboard",
    layout="wide",
)

st.title("📊 US Market Multi-Factor Risk Dashboard")
st.markdown(
    "미국 주식 시장의 **과열 위험도**를 매크로·수급 지표로 종합 진단하는 대시보드입니다. "
    "자산 관리 관점에서 시장 심리, 변동성, 금리, 추세, 수급을 한눈에 점검하여 "
    "**리스크 관리와 비중 조절 의사결정**을 돕는 것을 목적으로 합니다."
)

# ======================================================================
# 사이드바 — 수동 입력 (크롤링 불가/불안정 지표)
# ======================================================================
st.sidebar.header("⚙️ 수동 입력 (Manual Input)")
st.sidebar.caption(
    "크롤링이 막히거나 불안정한 지표는 값을 직접 입력하세요. "
    "입력 즉시 종합 리스크 점수에 반영됩니다."
)

manual_pcr = st.sidebar.number_input(
    "Put/Call Ratio",
    min_value=0.40, max_value=1.50, value=0.55, step=0.01,
    help="CBOE 총 풋/콜 비율. 낮을수록 시장 과열 신호 (0.6 이하 = 위험 100점).",
)

# ----------------------------------------------------------------------
# Margin Debt — 월별 Debit Balances 입력 → 전월 대비 MoM% 자동 계산
# ----------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("📈 Margin Debt (월별 입력)")
st.sidebar.caption(
    "FINRA Margin Statistics 의 **Debit Balances**(고객 신용융자 잔액, 단위: 백만 달러)를 "
    "월별로 입력하면 직전 월 대비 증감률(MoM %)이 자동 계산됩니다."
)

# 세션 상태에 월별 기록 로드 — repo 의 margin_history.json 이 기준값
if "margin_history" not in st.session_state:
    st.session_state.margin_history = load_margin_history()
margin_history = st.session_state.margin_history

with st.sidebar.form("margin_input_form"):
    in_month = st.text_input(
        "기준 월 (YYYY-MM)", value=datetime.now().strftime("%Y-%m")
    )
    in_value = st.number_input(
        "Debit Balances (백만 달러)",
        min_value=0.0, value=0.0, step=1000.0, format="%.0f",
        help="예: 마진 데트 총액이 약 9,000억 달러면 900000 입력.",
    )
    if st.form_submit_button("➕ 이번 달 추가 / 수정"):
        month_key = in_month.strip()
        if not re.fullmatch(r"\d{4}-\d{2}", month_key):
            st.sidebar.error("월 형식이 올바르지 않습니다. 예: 2026-04")
        elif in_value <= 0:
            st.sidebar.error("Debit Balances 값을 0보다 크게 입력하세요.")
        else:
            margin_history[month_key] = float(in_value)
            st.sidebar.success(
                f"{month_key} 저장됨 — 아래 '영구 저장용 JSON' 을 커밋하세요."
            )

# 입력된 월 목록 확인 / 삭제
if margin_history:
    months_desc = sorted(margin_history.keys(), reverse=True)
    with st.sidebar.expander(f"📋 입력된 월 {len(months_desc)}개 보기 / 삭제"):
        for m in months_desc:
            st.write(f"- **{m}** : {margin_history[m]:,.0f} 백만 $")
        del_target = st.selectbox(
            "삭제할 월", ["(선택 안 함)"] + months_desc, key="margin_del"
        )
        if st.button("🗑️ 선택한 월 삭제") and del_target != "(선택 안 함)":
            margin_history.pop(del_target, None)
            st.rerun()

# MoM 자동 계산 결과
margin_mom, mom_latest, mom_prev = compute_margin_mom(margin_history)
if margin_mom is None:
    st.sidebar.warning(
        "MoM 증감률을 계산하려면 서로 다른 2개 월 이상의 데이터가 필요합니다."
    )
else:
    st.sidebar.metric(
        f"Margin Debt MoM ({mom_latest})",
        f"{margin_mom:+.2f}%",
        help=f"{mom_prev} → {mom_latest} Debit Balances 증감률 (자동 계산).",
    )

# repo 영구 저장용 JSON — Streamlit Cloud 는 세션 입력을 영구 저장하지 못함
margin_payload = {
    "unit": "USD millions",
    "source": (
        "FINRA Margin Statistics - "
        "Debit Balances in Customers' Securities Margin Accounts"
    ),
    "history": dict(sorted(margin_history.items())),
}
with st.sidebar.expander("💾 영구 저장용 JSON (복사 → 커밋)"):
    st.caption(
        "Streamlit Cloud 는 입력값을 영구 저장하지 못합니다. 아래 내용을 저장소의 "
        "`margin_history.json` 에 그대로 덮어쓰고 git 커밋하면 다음 접속에도 유지됩니다."
    )
    st.code(
        json.dumps(margin_payload, ensure_ascii=False, indent=2), language="json"
    )

st.sidebar.divider()
st.sidebar.caption(
    "📡 나머지 5개 지표는 GitHub Actions 가 매일 오전 7시(KST) 크롤링해 "
    "`market_data.json` 으로 갱신합니다. 이 앱은 그 파일을 읽어 표시합니다."
)

st.divider()

# ======================================================================
# 데이터 로드 — market_data.json (라이브 5개) + 사이드바 입력 (수동 2개)
# ======================================================================
last_updated, live_raw = load_market_data()

# 라이브 5개: 파일에 없는 지표는 에러 스텁으로 대체 (앱이 죽지 않도록)
live = {}
for label in LIVE_LABELS:
    live[label] = live_raw.get(label) or {
        "value": "N/A", "delta": "(데이터 없음)", "raw": None, "status": "error"
    }

# Put/Call 수동 입력을 동일한 dict 포맷으로 래핑
pcr_indicator = {
    "value": f"{manual_pcr:.2f}", "delta": "✍️ 수동 입력",
    "raw": float(manual_pcr), "status": "manual",
}

# Margin Debt MoM — 월별 입력값으로 자동 계산된 값을 래핑
if margin_mom is not None:
    margin_indicator = {
        "value": f"{margin_mom:+.1f}%",
        "delta": f"📅 {mom_prev} → {mom_latest} 자동 계산",
        "raw": float(margin_mom), "status": "calc",
    }
else:
    margin_indicator = {
        "value": "N/A",
        "delta": "월 데이터 2개 이상 필요",
        "raw": None, "status": "error",
    }

# 화면 표시 순서대로 7개 지표 통합
data = {
    "Fear & Greed Index": live["Fear & Greed Index"],
    "Put/Call Ratio": pcr_indicator,
    "VIX 지수": live["VIX 지수"],
    "10년물 국채금리": live["10년물 국채금리"],
    "S&P500 200일 이격도": live["S&P500 200일 이격도"],
    "Market Breadth": live["Market Breadth"],
    "Margin Debt MoM": margin_indicator,
}

# 종합 리스크 점수 계산
raw_values = {label: d["raw"] for label, d in data.items()}
risk_score, details = calculate_advanced_risk_score(raw_values)
detail_by_label = {d["label"]: d for d in details}

# 데이터 상태 요약 캡션
live_cnt = sum(1 for d in data.values() if d["status"] == "live")
manual_cnt = sum(1 for d in data.values() if d["status"] == "manual")
calc_cnt = sum(1 for d in data.values() if d["status"] == "calc")
error_cnt = sum(1 for d in data.values() if d["status"] == "error")

if last_updated is None:
    st.error(
        "⚠️ `market_data.json` 을 찾을 수 없습니다. "
        "터미널에서 `python data_updater.py` 를 먼저 실행하거나, "
        "GitHub Actions 가 한 번 실행되기를 기다려주세요."
    )
else:
    st.caption(
        f"🟢 실시간 {live_cnt}개 · ✍️ 수동 {manual_cnt}개 · 🧮 자동계산 {calc_cnt}개 · "
        f"🔴 에러 {error_cnt}개  |  "
        f"데이터 수집 시각: {last_updated} (GitHub Actions 매일 갱신)"
    )
if error_cnt:
    st.warning(
        f"⚠️ 지표 {error_cnt}개를 읽지 못했습니다. "
        "해당 지표는 중립값(50점)으로 종합 점수에 반영됩니다. "
        "(Margin Debt MoM 이 N/A 라면 사이드바에서 월별 데이터를 2개 이상 입력하세요.)"
    )

# ======================================================================
# 1) 종합 시장 과열 리스크 점수 — Gauge 차트
# ======================================================================
st.subheader("종합 시장 과열 리스크 점수")

zone_color, zone_label = _risk_zone(risk_score)

gauge_fig = go.Figure(
    go.Indicator(
        mode="gauge+number",
        value=risk_score,
        number={"suffix": " 점", "font": {"size": 48}},
        title={"text": "Composite Overheating Risk", "font": {"size": 20}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": zone_color},
            "steps": [
                {"range": [0, 40],   "color": "#2ECC71"},   # 안전
                {"range": [40, 75],  "color": "#F1C40F"},   # 주의
                {"range": [75, 100], "color": "#E74C3C"},   # 위험
            ],
            "threshold": {
                "line": {"color": "#7B0000", "width": 4},
                "thickness": 0.8,
                "value": risk_score,
            },
        },
    )
)
gauge_fig.update_layout(height=380, margin=dict(l=30, r=30, t=60, b=20))

g_col1, g_col2, g_col3 = st.columns([1, 2, 1])
with g_col2:
    st.plotly_chart(gauge_fig, use_container_width=True)
    st.markdown(
        f"<p style='text-align:center; font-size:20px;'><b>{zone_label}</b></p>",
        unsafe_allow_html=True,
    )

st.divider()

# ======================================================================
# 2) 7대 핵심 지표 — st.metric 카드
# ======================================================================
st.subheader("7대 핵심 지표")

card_order = list(data.keys())


def _render_card(col, label):
    d = data[label]
    norm = detail_by_label[label]["norm"]
    with col:
        st.metric(label=label, value=d["value"], delta=d["delta"], delta_color="off")
        if d["status"] == "manual":
            st.caption(f"✍️ 수동 입력 · 위험점수 {norm:.0f}/100")
        elif d["status"] == "calc":
            st.caption(f"🧮 월별 입력 자동 계산 · 위험점수 {norm:.0f}/100")
        elif d["status"] == "error":
            st.caption("🔴 데이터 오류 · 중립값 50/100 적용")
        else:
            st.caption(f"🟢 실시간 · 위험점수 {norm:.0f}/100")


for col, label in zip(st.columns(4), card_order[:4]):
    _render_card(col, label)
for col, label in zip(st.columns(4), card_order[4:]):
    _render_card(col, label)

st.divider()

# ======================================================================
# 3) 지표별 리스크 점수 기여도 — 가로 바 차트
# ======================================================================
st.subheader("지표별 리스크 점수 기여도")
st.caption(
    "기여 점수 = 정규화 위험점수 × 가중치. 모든 지표의 기여 점수 합이 "
    f"종합 리스크 점수({risk_score:.1f}점)와 같습니다."
)

# 기여도 오름차순 정렬 (가로 바 차트는 아래→위 순서)
sorted_details = sorted(details, key=lambda d: d["contribution"])
bar_labels = [f"{d['label']} (w={d['weight']:.2f})" for d in sorted_details]
bar_values = [d["contribution"] for d in sorted_details]
bar_text = [
    f"{d['contribution']:.1f}점  (정규화 {d['norm']:.0f})" for d in sorted_details
]

bar_fig = go.Figure(
    go.Bar(
        x=bar_values,
        y=bar_labels,
        orientation="h",
        marker=dict(
            color=bar_values,
            colorscale="Reds",
            cmin=0,
            line=dict(color="#7B0000", width=1),
        ),
        text=bar_text,
        textposition="outside",
    )
)
bar_fig.update_layout(
    height=440,
    margin=dict(l=30, r=30, t=30, b=30),
    xaxis_title="리스크 점수 기여도 (점)",
    yaxis_title="",
    xaxis=dict(range=[0, max(bar_values) + 5]),
)
st.plotly_chart(bar_fig, use_container_width=True)
