"""분석·예측 API.

이 앱의 원칙이 API 스키마에 그대로 박혀 있습니다:

    **예측값은 그 예측의 아웃오브샘플 품질과 분리해서 반환하지 않습니다.**

    `ForecastOut` 은 `quality` 필드를 필수로 가집니다. 확률만 떼어내 화면에
    띄우는 것이 구조적으로 불가능하게 만들기 위해서입니다. 보정되지 않은 확률은
    과신을 낳고, 과신하는 예측은 통상적 베팅 규칙 하에서 장기 성장률을 음수로
    만듭니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.indicators import levels as lv
from app.indicators import price as px
from app.markets import MARKETS, get_market
from app.predict import calibration as cal
from app.predict import features as feat
from app.predict import model as mdl
from app.predict import sectors as sec
from app.store import get_store

router = APIRouter(prefix="/api", tags=["analysis"])

MIN_ROWS_FOR_FORECAST = 800


# ── 스키마 ────────────────────────────────────────────────────────────────
class CoverageOut(BaseModel):
    market: str
    n_tickers: int
    first_date: str | None
    last_date: str | None
    n_rows: int
    ready: bool
    needs_credential: str | None


class UniverseItem(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    first_date: str
    last_date: str
    n_days: int


class SeriesPoint(BaseModel):
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


class IndicatorSeries(BaseModel):
    date: list[str]
    values: dict[str, list[float | None]]


class LevelOut(BaseModel):
    price: float
    kind: str  # support | resistance
    touches: int
    distance_pct: float


class StockDetailOut(BaseModel):
    market: str
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    prices: list[SeriesPoint]
    indicators: IndicatorSeries
    interpretation: list[dict]
    levels: list[LevelOut]  # 지지/저항 참고선 (신호 아님 -- caveat 는 해석 카드에)


class ForecastQuality(BaseModel):
    """예측의 아웃오브샘플 품질. 예측값과 항상 함께 반환됩니다.

    타깃별로 판정 잣대가 다릅니다 -- 같은 잣대를 돌려쓰면 잘못된 경고가 납니다
    (실제 배포에서 변동성 R² 78% 에 수익률 기준 누수 경고가 발동한 사례):

        direction  -> skill score (기저율 대비). R² 는 판정 기준이 아님
        return     -> 횡단면 R² (드리프트 제거) + IC. 기준선은 모멘텀 단독 모형
        volatility -> R² 수십%가 정상. 기준선은 지속성(현재 변동성 유지)
    """

    n_folds: int
    n_predictions: int
    # 회귀 타깃용
    oos_r2: float | None  # 기준선 0 대비. 시장 드리프트를 맞힌 몫 포함
    oos_r2_cross: float | None  # 일별 횡단면 평균 대비. 종목 선별력만
    mean_daily_ic: float | None  # 일별 순위상관 평균. 드리프트 무관
    # 분류(방향) 타깃용
    brier: float | None
    reliability: float | None
    resolution: float | None
    skill_score: float | None
    ece: float | None
    reliability_curve: list[dict]
    # 타깃에 맞는 기준선과의 비교
    baseline_label: str | None
    baseline_r2: float | None
    beats_baseline: bool | None
    leakage_warning: str | None
    literature_context: str


class ForecastOut(BaseModel):
    market: str
    target: str
    horizon_days: int
    latest: list[dict]
    quality: ForecastQuality  # 필수 -- 예측만 떼어갈 수 없습니다
    caveat: str


class SectorRow(BaseModel):
    sector: str
    ret_20d: float | None
    ret_60d: float | None
    relative_strength_60d: float | None
    breadth: float | None
    n_constituents: int


# ── 데이터 현황 ───────────────────────────────────────────────────────────
@router.get("/coverage", response_model=list[CoverageOut])
def coverage() -> list[CoverageOut]:
    """시장별 데이터 보유 현황.

    프론트엔드는 이걸 먼저 호출해 '데이터 없음'과 '인증키 없음'을 구분해
    안내합니다. 둘을 같은 오류로 보여주면 사용자가 무엇을 해야 할지 모릅니다.
    """
    store = get_store()
    have = {r["market"]: r for _, r in store.coverage().iterrows()}
    out: list[CoverageOut] = []
    for code in MARKETS:
        row = have.get(code)
        has_rows = row is not None and int(row["n_rows"]) > 0
        out.append(
            CoverageOut(
                market=code,
                n_tickers=int(row["n_tickers"]) if row is not None else 0,
                first_date=str(row["first_date"]) if row is not None else None,
                last_date=str(row["last_date"]) if row is not None else None,
                n_rows=int(row["n_rows"]) if row is not None else 0,
                ready=has_rows,
                needs_credential=(
                    "KRX_AUTH_KEY" if (code == "KR" and not has_rows) else None
                ),
            )
        )
    return out


@router.get("/universe/{market}", response_model=list[UniverseItem])
def universe(market: str) -> list[UniverseItem]:
    _require_market(market)
    df = get_store().universe(market.upper())
    return [
        UniverseItem(
            ticker=r["ticker"],
            name=r["name"],
            sector=r["sector"],
            industry=r.get("industry"),
            first_date=str(r["first_date"]),
            last_date=str(r["last_date"]),
            n_days=int(r["n_days"]),
        )
        for _, r in df.iterrows()
    ]


# ── 검색 (자동완성) ───────────────────────────────────────────────────────
class SearchHit(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    market_cap: float | None
    match: str  # ticker | name | industry -- 어디에 걸렸는지 (UI 강조용)


@router.get("/search/{market}", response_model=list[SearchHit])
def search(
    market: str,
    q: str = Query("", description="종목코드·종목명·업종 일부"),
    limit: int = Query(20, ge=1, le=50),
):
    """종목 검색 (자동완성용).

    전부 입력하지 않아도 되도록 **부분 일치**로 찾고, 관련도 순으로 정렬합니다:

        1. 종목코드 완전 일치      (005930 → 삼성전자)
        2. 종목코드 시작 일치      (0059 → 005930)
        3. 종목명 시작 일치        (삼성 → 삼성전자, 삼성물산)
        4. 종목명 부분 일치        (전자 → 삼성전자, LG전자)
        5. 업종 일치              (반도체 → 해당 업종 종목들)

    동점이면 시가총액 순입니다 -- 사용자가 찾는 건 대개 큰 종목이기 때문입니다.
    빈 질의는 시총 상위를 반환해 검색창을 열자마자 무언가 보이게 합니다.
    """
    _require_market(market)
    market = market.upper()
    store = get_store()
    df = store.universe(market)
    if df.empty:
        return []

    # 시총은 유니버스 뷰에 없으므로 최신 스냅샷에서 가져옵니다.
    caps = _latest_market_caps(market)
    df["market_cap"] = df["ticker"].map(caps)

    needle = q.strip().lower()
    if not needle:
        top = df.nlargest(limit, "market_cap", keep="all").head(limit)
        return [_hit(r, "ticker") for _, r in top.iterrows()]

    scored: list[tuple[int, float, pd.Series, str]] = []
    for _, r in df.iterrows():
        ticker = str(r["ticker"]).lower()
        name = str(r["name"]).lower() if pd.notna(r["name"]) else ""
        industry = str(r["industry"]).lower() if pd.notna(r.get("industry")) else ""
        sector = str(r["sector"]).lower() if pd.notna(r["sector"]) else ""

        if ticker == needle:
            rank, where = 0, "ticker"
        elif ticker.startswith(needle):
            rank, where = 1, "ticker"
        elif name.startswith(needle):
            rank, where = 2, "name"
        elif needle in name:
            rank, where = 3, "name"
        elif needle in industry or needle in sector:
            rank, where = 4, "industry"
        else:
            continue

        cap = r["market_cap"]
        scored.append((rank, -(cap if pd.notna(cap) else 0.0), r, where))

    scored.sort(key=lambda x: (x[0], x[1]))
    return [_hit(r, where) for _, _, r, where in scored[:limit]]


def _hit(row: pd.Series, where: str) -> SearchHit:
    return SearchHit(
        ticker=row["ticker"],
        name=row["name"] if pd.notna(row["name"]) else None,
        sector=row["sector"] if pd.notna(row["sector"]) else None,
        industry=row["industry"] if pd.notna(row.get("industry")) else None,
        market_cap=_f(row.get("market_cap")),
        match=where,
    )


def _latest_market_caps(market: str) -> dict[str, float]:
    """최신 일자의 종목별 시가총액. 검색 결과 정렬에 씁니다."""
    with get_store().cursor() as con:
        df = con.execute(
            """
            SELECT ticker, market_cap FROM prices
            WHERE market = ? AND market_cap IS NOT NULL
              AND date = (SELECT max(date) FROM prices WHERE market = ?)
            """,
            [market, market],
        ).fetchdf()
    return dict(zip(df["ticker"], df["market_cap"], strict=False)) if not df.empty else {}


# ── 종목 상세 ─────────────────────────────────────────────────────────────
@router.get("/stocks/{market}/{ticker}", response_model=StockDetailOut)
def stock_detail(market: str, ticker: str, days: int = Query(500, ge=60, le=5000)):
    _require_market(market)
    market = market.upper()
    store = get_store()
    df = store.prices(market, [ticker])
    if df.empty:
        raise HTTPException(404, f"{market}/{ticker} 데이터가 없습니다. 먼저 수집하십시오.")

    df = df.sort_values("date").tail(days).reset_index(drop=True)
    close = df["close"]

    ind = {
        "sma_20": px.sma(close, 20),
        "sma_60": px.sma(close, 60),
        "rsi_14": px.rsi(close, 14),
        "macd_hist": px.macd(close)["hist"],
        "bollinger_upper": px.bollinger(close)["upper"],
        "bollinger_lower": px.bollinger(close)["lower"],
        "vol_20d": px.realized_volatility(close, 20),
        "pct_from_52w_high": px.pct_from_52w_high(close),
    }

    swing = lv.swing_levels(df["high"], df["low"], close)
    cross_20_60 = lv.ma_cross(close, fast=20, slow=60)
    cross_50_200 = lv.ma_cross(close, fast=50, slow=200)

    return StockDetailOut(
        market=market,
        ticker=ticker,
        name=df["name"].dropna().iloc[-1] if df["name"].notna().any() else None,
        sector=df["sector"].dropna().iloc[-1] if df["sector"].notna().any() else None,
        industry=(
            df["industry"].dropna().iloc[-1]
            if "industry" in df.columns and df["industry"].notna().any()
            else None
        ),
        prices=[
            SeriesPoint(
                date=str(r["date"]),
                open=_f(r["open"]), high=_f(r["high"]),
                low=_f(r["low"]), close=_f(r["close"]), volume=_f(r["volume"]),
            )
            for _, r in df.iterrows()
        ],
        indicators=IndicatorSeries(
            date=[str(d) for d in df["date"]],
            values={k: [_f(v) for v in s] for k, s in ind.items()},
        ),
        interpretation=_interpret(ind) + _interpret_levels(swing, cross_20_60, cross_50_200),
        levels=[
            LevelOut(
                price=round(x.price, 4),
                kind=x.kind,
                touches=x.touches,
                distance_pct=round(x.distance_pct, 4),
            )
            for x in swing
        ],
    )


def _interpret_levels(swing, c1: lv.CrossState, c2: lv.CrossState) -> list[dict]:
    """지지/저항·이동평균 교차 해석 카드.

    이 둘은 이 앱에서 근거가 가장 약한 지표입니다. 해석 문구보다 한계 문구가
    더 중요합니다 -- '골든크로스가 떴으니 매수' 로 읽히지 않게 씁니다.
    """
    out: list[dict] = []

    supports = [x for x in swing if x.kind == "support"]
    resistances = [x for x in swing if x.kind == "resistance"]
    if supports or resistances:
        near_s = max(supports, key=lambda x: x.price) if supports else None
        near_r = min(resistances, key=lambda x: x.price) if resistances else None
        parts = []
        if near_s:
            parts.append(
                f"가까운 지지 후보 {near_s.price:,.0f} ({near_s.distance_pct * 100:+.1f}%, "
                f"터치 {near_s.touches}회)"
            )
        if near_r:
            parts.append(
                f"가까운 저항 후보 {near_r.price:,.0f} ({near_r.distance_pct * 100:+.1f}%, "
                f"터치 {near_r.touches}회)"
            )
        out.append({
            "indicator": "지지/저항 (스윙 클러스터)",
            "value": round(near_s.price if near_s else near_r.price, 2),
            "state": f"수준 {len(swing)}개 감지",
            "reading": ". ".join(parts) + ".",
            "caveat": "지지/저항은 학술 근거가 약한 참고선입니다. 많은 참여자가 "
                      "의식하는 가격대라는 자기실현 가설이 논리의 전부이며, 뚫리면 "
                      "의미가 반전됩니다. 매매 신호가 아니라 차트 참고용입니다.",
        })

    for c, label in ((c1, "20/60일"), (c2, "50/200일")):
        if c.state == "insufficient":
            continue
        state_kr = "정배열 (골든)" if c.state == "golden" else "역배열 (데드)"
        if c.last_cross and c.days_since_cross is not None:
            cross_kr = "골든크로스" if c.last_cross == "golden" else "데드크로스"
            reading = (
                f"현재 {state_kr}. 마지막 교차는 {c.days_since_cross}거래일 전 "
                f"{cross_kr}입니다."
            )
        else:
            reading = f"현재 {state_kr}. 표시 구간 내 교차 없음."
        out.append({
            "indicator": f"이동평균 교차 ({label})",
            "value": 1.0 if c.state == "golden" else -1.0,
            "state": state_kr,
            "reading": reading,
            "caveat": "이동평균 교차는 구조적으로 후행 신호이며, 단독 사용 성과에 "
                      "대한 문헌 근거는 혼재합니다. 추세 확인용 서술이지 진입 "
                      "신호가 아닙니다.",
        })
    return out


def _interpret(ind: dict) -> list[dict]:
    """지표 해석 -- **매매 신호가 아니라 상태 서술**입니다.

    'RSI 30 이하이므로 매수' 같은 문구를 내지 않습니다. 문헌상 단일 기술적
    지표의 예측력은 약하고, 특히 RSI 는 추세장에서 과매수 구간에 머문 채 계속
    오르기 때문입니다. 각 항목에 한계를 함께 실어 보냅니다.
    """
    out: list[dict] = []

    rsi = ind["rsi_14"].dropna()
    if len(rsi):
        v = float(rsi.iloc[-1])
        state = "과매수 구간" if v >= 70 else ("과매도 구간" if v <= 30 else "중립 구간")
        out.append({
            "indicator": "RSI(14)",
            "value": round(v, 1),
            "state": state,
            "reading": f"현재 RSI 는 {v:.1f} 로 {state}입니다.",
            "caveat": "추세장에서는 과매수 구간에 머문 채 계속 오릅니다. "
                      "단독 매매 신호로 쓰면 상승장에서 조기 매도를 반복합니다.",
        })

    sma20, sma60 = ind["sma_20"].dropna(), ind["sma_60"].dropna()
    if len(sma20) and len(sma60):
        above = float(sma20.iloc[-1]) > float(sma60.iloc[-1])
        out.append({
            "indicator": "이동평균 20/60",
            "value": round(float(sma20.iloc[-1]) / float(sma60.iloc[-1]) - 1, 4),
            "state": "단기 우위" if above else "장기 우위",
            "reading": f"20일선이 60일선 {'위' if above else '아래'}에 있습니다.",
            "caveat": "이동평균은 구조적으로 가격을 뒤따릅니다. 추세 판별 필터로는 "
                      "쓸모가 있으나 진입 타이밍 근거로는 약합니다.",
        })

    vol = ind["vol_20d"].dropna()
    if len(vol):
        v = float(vol.iloc[-1])
        out.append({
            "indicator": "실현변동성(20일, 연율)",
            "value": round(v, 4),
            "state": "높음" if v > 0.4 else ("낮음" if v < 0.2 else "보통"),
            "reading": f"연율 환산 변동성은 {v * 100:.1f}% 입니다.",
            "caveat": "변동성은 방향이 아니라 폭입니다. 다만 수익률 방향보다 "
                      "예측 가능성이 훨씬 높아, 포지션 크기 결정에 유용합니다.",
        })

    hi = ind["pct_from_52w_high"].dropna()
    if len(hi):
        v = float(hi.iloc[-1])
        out.append({
            "indicator": "52주 고점 대비",
            "value": round(v, 4),
            "state": "신고가 부근" if v > -0.05 else "고점 대비 하락",
            "reading": f"52주 고점 대비 {v * 100:.1f}% 위치입니다.",
            "caveat": "모멘텀 대용치로 문헌 근거가 있는 편이지만, 국내 시장의 "
                      "모멘텀 효과는 미국 대비 약하다는 연구가 있습니다.",
        })
    return out


# ── 예측 ──────────────────────────────────────────────────────────────────
@router.get("/forecast/{market}", response_model=ForecastOut)
def forecast(
    market: str,
    target: str = Query("direction", pattern="^(direction|return|volatility)$"),
    horizon_days: int = Query(21, ge=5, le=120),
    top_n: int = Query(20, ge=1, le=100),
):
    """워크포워드 예측 + **보정 품질**.

    target:
        direction  -- 상승 확률 (보정된 확률로 제시)
        return     -- 기대 수익률
        volatility -- 미래 변동성 (문헌상 가장 예측 가능한 대상)
    """
    _require_market(market)
    market = market.upper()
    panel = get_store().prices(market)
    if len(panel) < MIN_ROWS_FOR_FORECAST:
        raise HTTPException(
            409,
            f"{market} 데이터가 부족합니다 ({len(panel)}행). 예측에는 최소 "
            f"{MIN_ROWS_FOR_FORECAST}행이 필요합니다. 먼저 수집을 실행하십시오.",
        )

    df = feat.build_features(panel)
    df = feat.make_labels(df, horizon_days=horizon_days)

    label_col = {"direction": "fwd_up", "return": "fwd_return",
                 "volatility": "fwd_vol"}[target]
    task = "classification" if target == "direction" else "regression"
    model_kind = "logistic" if task == "classification" else "ridge"

    cols = [c for c in feat.CORE_FEATURES if c in df.columns and df[c].notna().any()]
    if not cols:
        raise HTTPException(409, "사용 가능한 특성이 없습니다. 데이터 기간이 너무 짧습니다.")
    ranked = feat.cross_sectional_rank(df, cols)

    cfg = mdl.WalkForwardConfig(
        train_days=504, test_days=126, horizon_days=horizon_days,
        model=model_kind, task=task,  # type: ignore[arg-type]
    )
    result = mdl.walk_forward_predict(ranked, cols, label_col, cfg)
    if result.predictions.empty:
        raise HTTPException(409, "워크포워드 구간을 만들 수 없습니다. 데이터 기간이 짧습니다.")

    return ForecastOut(
        market=market,
        target=target,
        horizon_days=horizon_days,
        latest=_latest_scores(result, top_n),
        quality=_quality(result, df, ranked, label_col, target, horizon_days, cfg),
        caveat=get_market(market).flow_caveat,
    )


LITERATURE = {
    "direction": (
        "방향 예측의 판정 기준은 정확도나 R² 가 아니라 skill score(기저율 대비 "
        "개선)입니다. 참고로 대형주 유니버스는 문헌상 방향 예측력이 가장 약한 "
        "영역입니다 -- ML 의 우위는 소형·비유동 종목에 집중됩니다."
    ),
    "return": (
        "문헌(Gu, Kelly, Xiu 2020) 기준 개별종목 월간 아웃오브샘플 R² 는 "
        "0.33~0.40% 가 최고 수준입니다. 전체 R² 에는 시장 전체의 상승(드리프트)을 "
        "맞힌 몫이 포함되므로, 종목 선별력은 횡단면 R² 와 IC 로 판단하십시오."
    ),
    "volatility": (
        "변동성은 강한 자기상관(변동성 군집) 때문에 R² 수십%가 정상이며, 수익률 "
        "기준(0.33~0.40%)을 적용하지 않습니다. 진짜 시험대는 '현재 변동성이 "
        "유지된다'는 지속성 기준선을 이기는가입니다."
    ),
}


def _quality(
    result, raw_df, ranked_df, label_col, target, horizon_days, cfg
) -> ForecastQuality:
    preds = result.predictions
    pred, actual = preds["prediction"], preds["actual"]

    oos_r2 = oos_r2_cross = mean_ic = None
    brier = reliability = resolution = skill = ece = None
    curve: list[dict] = []
    baseline_label = None
    baseline_r2 = None
    beats = None
    warning = None

    if target == "direction":
        d = cal.brier_decomposition(pred, actual)
        brier, reliability, resolution = d.brier, d.reliability, d.resolution
        skill = d.skill_score
        ece = cal.expected_calibration_error(pred, actual)
        curve = (
            cal.reliability_curve(pred, actual)
            .replace({np.nan: None})
            .to_dict("records")
        )
    else:
        oos_r2 = cal.out_of_sample_r2(pred, actual)
        # 드리프트 제거: 같은 날짜 실제값의 횡단면 평균을 기준선으로. "시장이
        # 올랐다"로는 점수를 얻을 수 없고 종목 간 우열을 맞혀야만 양수가 됩니다.
        cross_bench = preds.groupby("date")["actual"].transform("mean")
        oos_r2_cross = cal.out_of_sample_r2(pred, actual, benchmark=cross_bench)
        mean_ic = cal.mean_daily_ic(preds)

        if target == "return":
            baseline_label, baseline_r2 = _momentum_model_baseline(
                ranked_df, label_col, cfg
            )
        else:  # volatility
            baseline_label, baseline_r2 = _persistence_baseline(preds, raw_df)

        if baseline_r2 is not None and oos_r2 is not None:
            beats = bool(oos_r2 > baseline_r2)

        # 누수 경고는 드리프트를 제거한 횡단면 R² 로만 판정합니다. 기준선 0
        # 대비 R² 는 강세장에서 누수 없이도 몇 %가 나옵니다 (실측 확인).
        warning = cal.sanity_check_r2(
            oos_r2_cross if oos_r2_cross is not None else float("nan"),
            horizon_days,
            target=target,
        )

    return ForecastQuality(
        n_folds=result.n_folds,
        n_predictions=len(preds),
        oos_r2=_f(oos_r2),
        oos_r2_cross=_f(oos_r2_cross),
        mean_daily_ic=_f(mean_ic),
        brier=_f(brier),
        reliability=_f(reliability),
        resolution=_f(resolution),
        skill_score=_f(skill),
        ece=_f(ece),
        reliability_curve=curve,
        baseline_label=baseline_label,
        baseline_r2=_f(baseline_r2),
        beats_baseline=beats,
        leakage_warning=warning,
        literature_context=LITERATURE[target],
    )


def _momentum_model_baseline(ranked_df, label_col, cfg) -> tuple[str | None, float | None]:
    """수익률 기준선: 모멘텀 하나만 쓴 동일 조건 워크포워드 모형.

    이전 구현은 0~1 순위값을 수익률 예측값으로 그대로 비교했는데, 그러면
    '월 50% 수익률 예측'이 되어 R² 가 -3000% 같은 무의미한 수치가 나옵니다
    (실제 배포에서 확인). 같은 릿지·같은 구간으로 학습한 모형끼리 비교해야
    공정합니다.
    """
    if "mom_12_1" not in ranked_df.columns:
        return None, None
    baseline = mdl.walk_forward_predict(ranked_df, ["mom_12_1"], label_col, cfg)
    if baseline.predictions.empty:
        return None, None
    r2 = cal.out_of_sample_r2(
        baseline.predictions["prediction"], baseline.predictions["actual"]
    )
    return "모멘텀 단독 모형", _f(r2)


def _persistence_baseline(preds, raw_df) -> tuple[str | None, float | None]:
    """변동성 기준선: 지속성 -- 현재 변동성(vol_20d)이 그대로 유지된다고 예측.

    변동성 예측에서 이기기 가장 어려운 기준선입니다. 모멘텀과 비교하는 것은
    무의미합니다(재는 대상이 다름). raw_df 를 쓰는 이유: ranked_df 의 vol_20d
    는 횡단면 순위(0~1)로 변환되어 있어 변동성 수준이 아닙니다.
    """
    if "vol_20d" not in raw_df.columns:
        return None, None
    merged = preds.merge(
        raw_df[["date", "ticker", "vol_20d"]], on=["date", "ticker"], how="left"
    )
    valid = merged.dropna(subset=["vol_20d", "actual"])
    if valid.empty:
        return None, None
    r2 = cal.out_of_sample_r2(valid["vol_20d"], valid["actual"])
    return "지속성 (현재 변동성 유지)", _f(r2)


def _latest_scores(result, top_n: int) -> list[dict]:
    preds = result.predictions
    last_date = preds["date"].max()
    latest = preds[preds["date"] == last_date].copy()
    latest = latest.sort_values("prediction", ascending=False).head(top_n)
    return [
        {
            "date": str(pd.Timestamp(r["date"]).date()),
            "ticker": r["ticker"],
            "prediction": _f(r["prediction"]),
        }
        for _, r in latest.iterrows()
    ]


# ── 섹터 ──────────────────────────────────────────────────────────────────
@router.get("/sectors/{market}", response_model=list[SectorRow])
def sector_view(
    market: str,
    level: str = Query("sector", pattern="^(sector|industry)$"),
):
    """섹터/세부업종 현황.

    level=sector 는 대분류(미국 11개), level=industry 는 세분류(반도체·은행
    수준)입니다. 대분류만으로는 '반도체'와 '소프트웨어'가 전부 Technology 로
    뭉개지므로 세분류를 함께 제공합니다.

    섹터를 1급으로 다루는 이유: 섹터·국가 단위 아웃오브샘플 R² (0.29~0.95%)가
    개별종목(0.33~0.40%)과 동등하거나 더 높다는 문헌 근거가 있습니다.
    """
    _require_market(market)
    market = market.upper()
    panel = get_store().prices(market)
    if panel.empty:
        raise HTTPException(404, f"{market} 데이터가 없습니다. 먼저 수집하십시오.")
    if level not in panel.columns or panel[level].isna().all():
        raise HTTPException(
            404,
            f"{market} 의 {'세부업종' if level == 'industry' else '섹터'} 정보가 "
            "없습니다. 데이터를 다시 수집하면 채워집니다.",
        )

    agg = sec.aggregate_to_sector(panel, group_col=level)
    rs = sec.relative_strength(agg, window=60)
    breadth = sec.sector_breadth(panel, group_col=level)

    rows: list[SectorRow] = []
    for sector, group in rs.groupby("sector"):
        g = group.sort_values("date")
        b = breadth[breadth["sector"] == sector].sort_values("date")
        rows.append(
            SectorRow(
                sector=str(sector),
                ret_20d=_f(_cum(g["ret"].tail(20))),
                ret_60d=_f(_cum(g["ret"].tail(60))),
                relative_strength_60d=_f(
                    g["rs_60"].dropna().iloc[-1] if g["rs_60"].notna().any() else None
                ),
                breadth=_f(b["advancing"].iloc[-1]) if len(b) else None,
                n_constituents=int(g["n_constituents"].iloc[-1]) if len(g) else 0,
            )
        )
    return sorted(rows, key=lambda r: r.ret_20d if r.ret_20d is not None else -999,
                  reverse=True)


# ── 관찰 목록 (자동 스크리닝) ────────────────────────────────────────────
class WatchSector(BaseModel):
    sector: str
    ret_20d: float | None
    relative_strength_60d: float | None
    breadth: float | None
    n_constituents: int


class WatchCandidate(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    industry: str | None
    close: float | None
    ret_20d: float | None
    pct_from_52w_high: float | None
    score: int
    reasons: list[str]


class WatchlistOut(BaseModel):
    market: str
    as_of: str
    rising_sectors: list[WatchSector]
    candidates: list[WatchCandidate]
    rules: list[str]
    caveat: str


# 스크리닝 규칙. 각 규칙이 왜 들어갔는지가 응답에 그대로 노출됩니다 --
# 근거를 숨긴 "추천"은 이 앱의 원칙(검증 없는 신호 금지)과 충돌하기 때문에,
# 이 목록은 "규칙에 걸린 관찰 후보"로만 제시합니다.
WATCH_RULES = [
    "추세: 종가 > 20일선 > 60일선 (정배열)",
    "최근 골든크로스: 20/60일선이 최근 15거래일 내 상향 교차",
    "52주 고가 근접: 고점 대비 -5% 이내 (모멘텀 대용치, 문헌 근거 있음)",
    "상대강도: 최근 60일 수익률이 유니버스 평균 초과",
    "거래대금 급증: 최근 5일 평균이 60일 평균의 1.5배 이상",
]


@router.get("/watchlist/{market}", response_model=WatchlistOut)
def watchlist(market: str, top_stocks: int = Query(12, ge=3, le=30)):
    """자동 관찰 목록: 상승 추세 업종 + 규칙 기반 종목 후보.

    **매수 추천이 아닙니다.** 위 WATCH_RULES 에 걸린 종목을 점수순으로 보여줄
    뿐이며, 각 후보에 어떤 규칙이 걸렸는지(reasons)를 함께 반환합니다. 이
    규칙들의 예측력은 개별적으로 검증되지 않았고, 모멘텀 계열이라는 공통점만
    문헌 근거가 있습니다. 화면은 반드시 이 한계를 함께 표시해야 합니다.
    """
    _require_market(market)
    market = market.upper()
    panel = get_store().prices(market)
    if panel.empty:
        raise HTTPException(404, f"{market} 데이터가 없습니다. 먼저 수집하십시오.")

    panel = panel.sort_values(["ticker", "date"])
    as_of = str(pd.Timestamp(panel["date"].max()).date())

    # ── 상승 추세 업종 (세분류 우선, 없으면 대분류) ──────────────────────
    level = (
        "industry"
        if "industry" in panel.columns and panel["industry"].notna().any()
        else "sector"
    )
    rising: list[WatchSector] = []
    if panel[level].notna().any():
        agg = sec.aggregate_to_sector(panel, group_col=level)
        rs = sec.relative_strength(agg, window=60)
        breadth = sec.sector_breadth(panel, group_col=level)
        for name_, group in rs.groupby("sector"):
            g = group.sort_values("date")
            b = breadth[breadth["sector"] == name_].sort_values("date")
            rs_val = g["rs_60"].dropna()
            br_val = float(b["advancing"].iloc[-1]) if len(b) else None
            rising.append(
                WatchSector(
                    sector=str(name_),
                    ret_20d=_f(_cum(g["ret"].tail(20))),
                    relative_strength_60d=_f(rs_val.iloc[-1]) if len(rs_val) else None,
                    breadth=_f(br_val),
                    n_constituents=int(g["n_constituents"].iloc[-1]) if len(g) else 0,
                )
            )
        # 상대강도 상위 + 상승 비율 절반 이상 (소수 종목이 끌어올린 업종 제외)
        rising = [
            s for s in sorted(
                rising,
                key=lambda s: s.relative_strength_60d
                if s.relative_strength_60d is not None else -9e9,
                reverse=True,
            )
            if s.relative_strength_60d is not None and s.relative_strength_60d > 0
            and (s.breadth is None or s.breadth >= 0.5)
        ][:5]

    # ── 종목 스크리닝 ────────────────────────────────────────────────────
    candidates: list[WatchCandidate] = []
    ret60_by_ticker: dict[str, float] = {}
    for t, g in panel.groupby("ticker", sort=False):
        c = g["close"].reset_index(drop=True)
        if len(c) >= 61 and np.isfinite(c.iloc[-61]) and c.iloc[-61] > 0:
            ret60_by_ticker[t] = float(c.iloc[-1] / c.iloc[-61] - 1)
    universe_mean_ret60 = (
        float(np.mean(list(ret60_by_ticker.values()))) if ret60_by_ticker else 0.0
    )

    for t, g in panel.groupby("ticker", sort=False):
        g = g.reset_index(drop=True)
        c = g["close"]
        if len(c) < 70 or not np.isfinite(c.iloc[-1]):
            continue

        reasons: list[str] = []
        sma20 = c.rolling(20).mean().iloc[-1]
        sma60 = c.rolling(60).mean().iloc[-1]
        if np.isfinite(sma20) and np.isfinite(sma60) and c.iloc[-1] > sma20 > sma60:
            reasons.append("정배열 (종가>20일선>60일선)")

        cross = lv.recent_cross_tag(c, fast=20, slow=60, within_days=15)
        if cross == "golden":
            reasons.append("최근 골든크로스 (20/60)")

        hi52 = px.pct_from_52w_high(c).iloc[-1]
        if np.isfinite(hi52) and hi52 > -0.05:
            reasons.append("52주 고가 -5% 이내")

        r60 = ret60_by_ticker.get(t)
        if r60 is not None and r60 > universe_mean_ret60:
            reasons.append("60일 상대강도 우위")

        if "value" in g.columns and g["value"].notna().sum() >= 60:
            v5 = g["value"].tail(5).mean()
            v60 = g["value"].tail(60).mean()
            if np.isfinite(v5) and np.isfinite(v60) and v60 > 0 and v5 / v60 >= 1.5:
                reasons.append("거래대금 급증 (5일/60일 ≥1.5배)")

        if not reasons:
            continue
        ret20 = (
            float(c.iloc[-1] / c.iloc[-21] - 1)
            if len(c) >= 21 and c.iloc[-21] > 0
            else None
        )
        candidates.append(
            WatchCandidate(
                ticker=t,
                name=g["name"].dropna().iloc[-1] if g["name"].notna().any() else None,
                sector=g["sector"].dropna().iloc[-1]
                if g["sector"].notna().any() else None,
                industry=g["industry"].dropna().iloc[-1]
                if "industry" in g.columns and g["industry"].notna().any() else None,
                close=_f(c.iloc[-1]),
                ret_20d=_f(ret20),
                pct_from_52w_high=_f(hi52),
                score=len(reasons),
                reasons=reasons,
            )
        )

    candidates.sort(key=lambda x: (-x.score, -(x.ret_20d or -9e9)))
    return WatchlistOut(
        market=market,
        as_of=as_of,
        rising_sectors=rising,
        candidates=candidates[:top_stocks],
        rules=WATCH_RULES,
        caveat=(
            "이 목록은 매수 추천이 아니라 규칙 기반 관찰 후보입니다. 규칙에 몇 개 "
            "걸렸는지(score)를 점수로 쓸 뿐, 이 조합의 예측력은 검증되지 않았습니다. "
            "모멘텀 계열 규칙이라는 공통점만 문헌 근거가 있으며, 예측 화면의 품질 "
            "지표(skill score·IC)가 이 시장에서 낮다면 이 목록도 그만큼 회의적으로 "
            "보아야 합니다."
        ),
    )


# ── 수집 트리거 ───────────────────────────────────────────────────────────
class IngestOut(BaseModel):
    market: str
    rows: int
    tickers: int
    start: str | None
    end: str | None
    warnings: list[str]


@router.post("/ingest/{market}", response_model=IngestOut)
def ingest(
    market: str,
    years: int = Query(10, ge=1, le=25),
    limit: int = Query(300, ge=20, le=2000, description="시총 상위 N종목만 저장"),
):
    """데이터 수집 실행.

    미국은 인증키 없이 즉시 동작합니다. 한국은 KRX 인증키가 필요하며, 없으면
    409 로 '설정 필요'를 명확히 알립니다 -- 일반 오류로 처리하면 사용자가
    무엇을 해야 할지 알 수 없습니다.
    """
    _require_market(market)
    market = market.upper()
    from app.ingest.pipeline import ingest_kr_prices, ingest_us_prices
    from app.providers.base import ProviderError

    try:
        res = (
            ingest_us_prices(years=years)
            if market == "US"
            else ingest_kr_prices(limit=limit)
        )
    except ProviderError as exc:
        raise HTTPException(409, str(exc)) from exc

    return IngestOut(
        market=res.market, rows=res.rows, tickers=res.tickers,
        start=str(res.start) if res.start else None,
        end=str(res.end) if res.end else None,
        warnings=res.warnings,
    )


# ── 유틸 ──────────────────────────────────────────────────────────────────
def _require_market(market: str) -> None:
    if market.upper() not in MARKETS:
        raise HTTPException(404, f"알 수 없는 시장: {market}")


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f


def _cum(returns: pd.Series) -> float | None:
    r = returns.dropna()
    return float((1 + r).prod() - 1) if len(r) else None
