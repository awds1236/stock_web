export const metadata = { title: "방법론 — stock_web" };

export default function MethodologyPage() {
  return (
    <>
      <h2>방법론</h2>
      <p className="sub">
        이 앱이 예측을 어디까지 할 수 있다고 주장하는지, 그 근거는 무엇인지.
        전체 문서는 저장소의 <code>docs/methodology.md</code> 에 있습니다.
      </p>

      <h3>예측 성능의 현실적 상한</h3>
      <div className="card">
        <p style={{ marginTop: 0 }}>
          Gu, Kelly, Xiu (2020)가 900개 이상의 예측변수로 측정한 개별종목{" "}
          <strong>월간</strong> 아웃오브샘플 R²:
        </p>
        <table>
          <thead>
            <tr>
              <th>모형</th>
              <th className="num">월간 OOS R²</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>정규화 없는 선형</td>
              <td className="num neg">음수 (파국적 과적합)</td>
            </tr>
            <tr>
              <td>정규화 선형 (Ridge/Lasso/PCR)</td>
              <td className="num">약 0.26%</td>
            </tr>
            <tr>
              <td>트리·신경망</td>
              <td className="num pos">약 0.33 ~ 0.40%</td>
            </tr>
            <tr>
              <td>섹터·국가 단위</td>
              <td className="num pos">0.29 ~ 0.95%</td>
            </tr>
          </tbody>
        </table>
        <div className="caveat" style={{ marginTop: 10 }}>
          <strong>월간 R² 0.4%가 최첨단입니다.</strong> 이 앱에서 그보다 훨씬 높은
          값이 나오면 축하할 일이 아니라 데이터 누수를 의심할 신호이며, 자동으로
          경고합니다.
        </div>
        <div className="caveat">
          정규화 없는 모형은 R²가 음수이므로 이 앱은 그런 모형을{" "}
          <strong>아예 제공하지 않습니다</strong>. 기본값을 바꿀 문제가 아니라
          선택지에서 빼야 할 문제입니다.
        </div>
      </div>

      <h3>어떤 변수가 실제로 예측력을 갖는가</h3>
      <div className="card">
        <p style={{ marginTop: 0 }}>
          900개가 넘는 후보를 넣어도, 방법론과 무관하게 상위 예측변수는 일관되게
          셋입니다:
        </p>
        <p style={{ fontSize: 17, fontWeight: 600, margin: "10px 0" }}>
          모멘텀 · 유동성 · 변동성
        </p>
        <div className="caveat">
          그래서 이 앱의 핵심 특성은 이 세 축입니다. 수급(한국 연기금 / 미국
          내부자)은 <strong>보조</strong>이며, 더했을 때 실제로 개선이 있는지를
          가설 P6에서 검증합니다. 개선이 없으면 빼는 것이 맞습니다.
        </div>
        <div className="caveat">
          ML의 우위는 거래하기 어려운 종목에서 더 큽니다 — 전체 횡단면 월 0.6~0.7%p
          개선이 상위 10% 대형주만 보면 0.1%p로 줄어듭니다. 문헌 성과의 상당
          부분이 실제로는 체결하기 힘든 곳에 있다는 뜻이며, 백테스트 거래량 제약이
          타협 불가인 이유입니다.
        </div>
      </div>

      <h3>왜 정확도가 아니라 보정인가</h3>
      <div className="card">
        <p style={{ marginTop: 0 }}>
          상승 확률이 52%인 시장에서 &quot;항상 상승&quot;이라 답하면 정확도 52%가
          나오지만 정보량은 0입니다. 중요한 것은{" "}
          <strong>&quot;70%라고 말한 사건이 실제로 70% 빈도로 일어나는가&quot;</strong>
          입니다.
        </p>
        <div className="banner warn" style={{ marginTop: 10 }}>
          <strong>확신-실력 격차 (confidence–competence gap)</strong>
          우연보다 약간 나은 수준인데 일관되게 과신하는 모형은, 통상적인 베팅 규모
          규칙 하에서 장기 성장률이 <strong>음수</strong>가 됩니다. 살짝 더 맞히는
          것보다 확률을 정직하게 말하는 것이 중요합니다.
        </div>
        <p>Brier 점수 분해:</p>
        <p>
          <code>Brier = 신뢰도(reliability) − 분해능(resolution) + 불확실성</code>
        </p>
        <table>
          <thead>
            <tr>
              <th>항</th>
              <th>방향</th>
              <th>의미</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>reliability</td>
              <td>낮을수록 좋음</td>
              <td>예측 확률과 실제 빈도의 괴리 = 보정 오차</td>
            </tr>
            <tr>
              <td>resolution</td>
              <td>높을수록 좋음</td>
              <td>기저율과 다른 예측을 하는 능력 = 변별력</td>
            </tr>
            <tr>
              <td>uncertainty</td>
              <td>줄일 수 없음</td>
              <td>데이터 고유의 불확실성. 모형과 무관</td>
            </tr>
          </tbody>
        </table>
        <div className="caveat" style={{ marginTop: 10 }}>
          skill score = (resolution − reliability) / uncertainty. 이 값이 음수면
          기저율을 그냥 답하는 것보다 못하다는 뜻이며, 그 경우 앱은 그대로
          보고합니다.
        </div>
      </div>

      <h3>파이프라인이 지키는 규칙</h3>
      <div className="card">
        <ol style={{ margin: 0, paddingLeft: 18 }}>
          <li>정규화 필수 — 없는 모형은 제공하지 않음</li>
          <li>워크포워드 재학습 — 전체 기간 일괄 학습은 미래 정보 누수</li>
          <li>
            <strong>엠바고</strong> — 라벨이 h일 미래를 보므로 학습 끝과 예측 시작
            사이를 최소 h+1일 비움. 시계열 교차검증에서 가장 흔한 누수
          </li>
          <li>횡단면 순위 정규화는 같은 날짜 안에서만</li>
          <li>
            기준선 필수 비교 — 무조건부 평균과 모멘텀 단독. 못 이기면 그렇게 보고
          </li>
          <li>T+1 시가 체결 — 종가 체결은 지원하지 않음</li>
        </ol>
      </div>

      <h3>분할 주문 계획을 어떻게 계산하는가</h3>
      <div className="card">
        <div>
          종목 화면의 분할 주문 계획은 지지/저항 수준을 가격순으로 늘어놓고, 비중을
          배분하고, <strong>거래비용을 물린 실효 단가와 손절 손실액</strong>까지
          계산한 것입니다. 판단에 쓰라고 만든 화면이므로 무엇을 가정했는지 적어둡니다.
        </div>
        <ul style={{ marginTop: 8, paddingLeft: 18 }}>
          <li>
            <strong>R 이 먼저입니다.</strong> 손절 손실 = 무효화 가격에 전량
            정리했을 때의 손실이며 매도 수수료·슬리피지·거래세를 뺀 값입니다.
            포지션 크기는 이 숫자와 감수 비율에서 역산됩니다.
          </li>
          <li>
            <strong>수량은 내림합니다.</strong> 그래서 실현 리스크는 목표 비율을
            조금 밑돕니다 — 화면은 목표치가 아니라 실현값을 적습니다. 내림은 초과가
            아닌 미달 방향이라 안전한 쪽으로만 어긋납니다.
          </li>
          <li>
            <strong>수준이 모자라면 지어내지 않습니다.</strong> 그 방향의 지지/저항이
            모자라면 ATR 등간격으로 채우고 그 회차를 다른 배지로 표시합니다. 없는
            지지선을 있는 것처럼 두면 그 가격에 실제로 주문을 넣게 됩니다.
          </li>
          <li>
            <strong>지지/저항은 뚫리면 의미가 반전됩니다.</strong> 그래서 무효화
            가격이 계획의 일부이고, 이 조합의 예측력은 백테스트로 검증되지
            않았습니다 — 손익비가 낮으면 그 자체가 진입하지 않을 이유입니다.
          </li>
          <li>
            <strong>비용 가정은 자리표시자입니다.</strong> 증권거래세율은 해마다
            바뀝니다. 화면 상단에 지금 쓰는 값을 적어두었으니, 실제 계좌와 다르면{" "}
            <code>COST_KR_SELL_TAX</code> 같은 환경변수로 바꾸십시오.
          </li>
        </ul>
      </div>

      <h3>데이터의 한계</h3>
      <div className="card">
        <div className="caveat">
          <strong>한국</strong> — 연기금 종목별 수급은 장 마감 후 확정치입니다
          (18:00 이후). 장중 &quot;실시간 기관 수급&quot;은 창구분석 추정치이며
          연기금 단독으로 분리되지 않습니다. &apos;연기금등&apos;은 국민연금 단독
          수치가 아닙니다.
        </div>
        <div className="caveat">
          <strong>미국</strong> — 종목별·일별 투자자 유형별 수급이 공개 자료로
          존재하지 않습니다. 무료 대체물은 SEC Form 4 내부자 매매이며, 이는 기관
          수급이 아니라 임원·이사 본인의 거래입니다. 두 시장의 시그널을 나란히
          비교하지 마십시오.
        </div>
        <div className="caveat">
          <strong>미국 생존편향</strong> — 무료 소스(yfinance)는 상장폐지 종목을
          제공하지 않습니다. 엔진은 상폐를 -100%로 인식하도록 만들어져 있지만
          데이터에 없으면 인식할 대상이 없습니다. 미국 백테스트 성과는 낙관적으로
          편향됩니다.
        </div>
      </div>

      <h3>AI 서술 분석은 예측 엔진이 아닙니다</h3>
      <div className="card">
        <div>
          확률과 품질 지표는 전적으로 예측 엔진이 만듭니다. AI 는 그 결과를 포함한{" "}
          <strong>이미 계산된 숫자를 읽고 서술할 뿐</strong>이며, 검색하지도
          기억에 의존하지도 않습니다.
        </div>
        <div className="caveat" style={{ marginTop: 8 }}>
          <strong>프롬프트에 담기는 것</strong> — 이 앱이 계산한 지표 JSON 뿐입니다.
          실적·뉴스·공시·목표주가는 넘기지 않으며, 없는 것은 &quot;데이터에
          없다&quot;고 쓰도록 지시합니다. 점 예측과 매수/매도 추천은 시스템
          프롬프트에서 금지합니다.
        </div>
        <div className="caveat">
          <strong>없앨 수 없는 위험</strong> — 지어내기를 막는 것과 올바르게
          추론하게 하는 것은 다른 문제이며, 후자는 프롬프트로 보장되지 않습니다.
          숫자의 원본은 항상 지표 화면이며, <strong>AI 문장이 그것과 어긋나면
          지표 화면이 맞습니다.</strong>
        </div>
        <div className="caveat">
          <strong>비결정성</strong> — 같은 데이터로 다시 물어도 같은 문장이 나오지
          않습니다. 이것은 결함이 아니라 도구의 성질이며, 그래서 실행한 분석을
          그때 넘긴 숫자와 함께 보관합니다(AI 기록 화면).
        </div>
      </div>

      <h3>근거 문헌</h3>
      <div className="card">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            <a href="https://www.nber.org/system/files/working_papers/w25398/w25398.pdf" target="_blank" rel="noreferrer">
              Gu, Kelly, Xiu — Empirical Asset Pricing via Machine Learning
            </a>
          </li>
          <li>
            <a href="https://www.nber.org/system/files/working_papers/w23394/w23394.pdf" target="_blank" rel="noreferrer">
              Hou, Xue, Zhang — Replicating Anomalies (452개 중 65%가 t&gt;1.96 미달)
            </a>
          </li>
          <li>
            <a href="https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249" target="_blank" rel="noreferrer">
              Jensen, Kelly, Pedersen — Is There a Replication Crisis in Finance? (반론)
            </a>
          </li>
          <li>
            <a href="https://www.tandfonline.com/doi/full/10.1080/0015198X.2023.2208028" target="_blank" rel="noreferrer">
              Time-Series Predictability for Sector Investing
            </a>
          </li>
          <li>
            <a href="https://www.sciencedirect.com/science/article/abs/pii/S1059056010000249" target="_blank" rel="noreferrer">
              Herding by foreign investors — Evidence from Korea
            </a>
          </li>
        </ul>
      </div>

      <div className="banner info">
        <strong>면책</strong>
        투자자문이 아닙니다. 과거 성과는 미래 수익을 보장하지 않으며, 백테스트
        결과는 아무리 엄정하게 검증해도 실제 매매 성과와 다릅니다.
      </div>
    </>
  );
}
