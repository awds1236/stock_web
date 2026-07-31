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

/**
 * yfinance 세부업종(industry) 한글화.
 *
 * 대분류 11개로는 '반도체'와 '소프트웨어'가 전부 Technology 로 뭉개져 실사용에
 * 불편합니다. 여기서는 대형주 유니버스에 실제로 등장하는 업종을 우선 담았고,
 * 매핑에 없으면 영문 원문을 그대로 노출합니다 -- 억지 번역보다 낫습니다.
 */
const INDUSTRY_KO: Record<string, string> = {
  // 기술
  Semiconductors: "반도체",
  "Semiconductor Equipment & Materials": "반도체 장비·소재",
  "Software - Infrastructure": "소프트웨어 (인프라)",
  "Software - Application": "소프트웨어 (응용)",
  "Consumer Electronics": "가전·소비자 전자",
  "Information Technology Services": "IT 서비스",
  "Communication Equipment": "통신장비",
  "Computer Hardware": "컴퓨터 하드웨어",
  "Electronic Components": "전자부품",
  // 금융
  "Banks - Diversified": "종합은행",
  "Banks - Regional": "지역은행",
  "Capital Markets": "증권·자본시장",
  "Financial Data & Stock Exchanges": "금융정보·거래소",
  "Credit Services": "신용·결제",
  "Asset Management": "자산운용",
  "Insurance - Diversified": "종합보험",
  "Insurance - Property & Casualty": "손해보험",
  // 헬스케어
  Biotechnology: "바이오테크",
  "Drug Manufacturers - General": "제약 (종합)",
  "Drug Manufacturers - Specialty & Generic": "제약 (특화·제네릭)",
  "Medical Devices": "의료기기",
  "Diagnostics & Research": "진단·연구",
  "Healthcare Plans": "건강보험",
  "Medical Instruments & Supplies": "의료장비·소모품",
  // 경기소비재 / 필수소비재
  "Internet Retail": "인터넷 리테일",
  "Auto Manufacturers": "완성차",
  Restaurants: "외식",
  "Footwear & Accessories": "신발·액세서리",
  "Home Improvement Retail": "홈임프루브먼트 리테일",
  "Discount Stores": "할인점",
  "Beverages - Non-Alcoholic": "음료 (비주류)",
  "Household & Personal Products": "생활용품·퍼스널케어",
  Confectioners: "제과",
  // 산업재
  "Aerospace & Defense": "항공우주·방산",
  "Farm & Heavy Construction Machinery": "농기계·중장비",
  "Specialty Industrial Machinery": "산업기계",
  "Integrated Freight & Logistics": "물류",
  Railroads: "철도",
  Conglomerates: "복합기업",
  // 에너지 / 소재 / 유틸리티 / 통신
  "Oil & Gas Integrated": "석유·가스 (종합)",
  "Oil & Gas E&P": "석유·가스 (탐사·생산)",
  "Oil & Gas Midstream": "석유·가스 (중류)",
  "Specialty Chemicals": "특수화학",
  Chemicals: "화학",
  "Utilities - Regulated Electric": "전력 (규제)",
  "Telecom Services": "통신서비스",
  "Entertainment": "엔터테인먼트",
  "Internet Content & Information": "인터넷 콘텐츠·정보",
  "Advertising Agencies": "광고",
  "REIT - Specialty": "리츠 (특수)",
};

function withEnglish(ko: string, original: string): string {
  return `${ko} (${original})`;
}

export function sectorLabel(sector: string | null | undefined): string {
  if (!sector) return "—";
  const ko = SECTOR_KO[sector];
  return ko ? withEnglish(ko, sector) : sector;
}

/**
 * 세부업종 라벨. 한국(KRX) 업종명은 이미 한글이므로 매핑에 없으면 원문 그대로
 * 나가고, 그게 올바른 동작입니다.
 */
export function industryLabel(industry: string | null | undefined): string {
  if (!industry) return "—";
  const ko = INDUSTRY_KO[industry];
  return ko ? withEnglish(ko, industry) : industry;
}

/** 섹터/세부업종 중 무엇이든 적절히 한글화. */
export function groupLabel(
  value: string | null | undefined,
  level: "sector" | "industry",
): string {
  return level === "industry" ? industryLabel(value) : sectorLabel(value);
}
