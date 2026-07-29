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


class StockDetailOut(BaseModel):
    market: str
    ticker: str
    name: str | None
    sector: str | None
    prices: list[SeriesPoint]
    indicators: IndicatorSeries
    interpretation: list[dict]


class ForecastQuality(BaseModel):
    """예측의 아웃오브샘플 품질. 예측값과 항상 함께 반환됩니다."""

    n_folds: int
    n_predictions: int
    oos_r2: float | None
    brier: float | None
    reliability: float | None
    resolution: float | None
    skill_score: float | None
    ece: float | None
    reliability_curve: list[dict]
    baseline_momentum_r2: float | None
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
            first_date=str(r["first_date"]),
            last_date=str(r["last_date"]),
            n_days=int(r["n_days"]),
        )
        for _, r in df.iterrows()
    ]


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

    return StockDetailOut(
        market=market,
        ticker=ticker,
        name=df["name"].dropna().iloc[-1] if df["name"].notna().any() else None,
        sector=df["sector"].dropna().iloc[-1] if df["sector"].notna().any() else None,
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
        interpretation=_interpret(ind),
    )


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
        quality=_quality(result, ranked, label_col, target, horizon_days),
        caveat=get_market(market).flow_caveat,
    )


def _quality(result, df, label_col, target, horizon_days) -> ForecastQuality:
    pred = result.predictions["prediction"]
    actual = result.predictions["actual"]

    oos_r2 = None if target == "direction" else cal.out_of_sample_r2(pred, actual)
    brier = reliability = resolution = skill = ece = None
    curve: list[dict] = []

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

    # 모멘텀 단독 기준선 -- 복잡한 모형이 이걸 못 이기면 복잡도를 정당화할 수 없습니다
    baseline_r2 = None
    beats = None
    if target != "direction" and "mom_12_1" in df.columns:
        merged = result.predictions.merge(
            df[["date", "ticker", "mom_12_1"]], on=["date", "ticker"], how="left"
        )
        if merged["mom_12_1"].notna().any():
            baseline_r2 = cal.out_of_sample_r2(merged["mom_12_1"], merged["actual"])
            beats = bool(
                oos_r2 is not None and baseline_r2 is not None and oos_r2 > baseline_r2
            )

    warning = cal.sanity_check_r2(oos_r2, horizon_days) if oos_r2 is not None else None

    return ForecastQuality(
        n_folds=result.n_folds,
        n_predictions=len(result.predictions),
        oos_r2=_f(oos_r2),
        brier=_f(brier),
        reliability=_f(reliability),
        resolution=_f(resolution),
        skill_score=_f(skill),
        ece=_f(ece),
        reliability_curve=curve,
        baseline_momentum_r2=_f(baseline_r2),
        beats_baseline=beats,
        leakage_warning=warning,
        literature_context=(
            "문헌(Gu, Kelly, Xiu 2020) 기준 개별종목 월간 아웃오브샘플 R² 는 "
            "0.33~0.40% 가 최고 수준입니다. 이보다 크게 높은 값은 성능이 아니라 "
            "데이터 누수를 의심해야 합니다."
        ),
    )


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
def sector_view(market: str):
    """섹터 현황.

    섹터를 1급으로 다루는 이유: 섹터·국가 단위 아웃오브샘플 R² (0.29~0.95%)가
    개별종목(0.33~0.40%)과 동등하거나 더 높다는 문헌 근거가 있습니다.
    """
    _require_market(market)
    market = market.upper()
    panel = get_store().prices(market)
    if panel.empty or panel["sector"].isna().all():
        raise HTTPException(404, f"{market} 섹터 정보가 없습니다. 먼저 수집하십시오.")

    agg = sec.aggregate_to_sector(panel)
    rs = sec.relative_strength(agg, window=60)
    breadth = sec.sector_breadth(panel)

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


# ── 수집 트리거 ───────────────────────────────────────────────────────────
class IngestOut(BaseModel):
    market: str
    rows: int
    tickers: int
    start: str | None
    end: str | None
    warnings: list[str]


@router.post("/ingest/{market}", response_model=IngestOut)
def ingest(market: str, years: int = Query(10, ge=1, le=25)):
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
        res = ingest_us_prices(years=years) if market == "US" else ingest_kr_prices()
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
