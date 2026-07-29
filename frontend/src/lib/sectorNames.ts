/**
 * 섹터명 한글화: "에너지 (Energy)" 형식.
 *
 * yfinance 가 주는 미국 섹터 분류(11개)만 매핑합니다. 한국(KRX) 섹터는 이미
 * 한글로 들어오므로 매핑에 없으면 원문을 그대로 보여줍니다 -- 모르는 값을
 * 억지로 번역하지 않습니다.
 */
const SECTOR_KO: Record<string, string> = {
  Technology: "기술",
  "Financial Services": "금융",
  Healthcare: "헬스케어",
  "Communication Services": "통신서비스",
  Industrials: "산업재",
  "Consumer Defensive": "필수소비재",
  "Consumer Cyclical": "경기소비재",
  Energy: "에너지",
  "Basic Materials": "소재",
  "Real Estate": "부동산",
  Utilities: "유틸리티",
};

export function sectorLabel(sector: string | null | undefined): string {
  if (!sector) return "—";
  const ko = SECTOR_KO[sector];
  return ko ? `${ko} (${sector})` : sector;
}
