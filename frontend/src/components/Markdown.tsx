"use client";

import { Fragment, type ReactNode } from "react";

/**
 * 최소 마크다운 렌더러 (제목·불릿·굵게·인라인 코드).
 *
 * 라이브러리를 넣지 않은 이유:
 *   렌더 대상이 우리가 형식을 지정한 AI 응답 하나뿐입니다. 표도 링크도
 *   이미지도 요구하지 않았습니다. 범용 파서를 넣으면 의존성과 함께 **HTML
 *   주입 경로**가 따라옵니다 -- 모델 출력을 dangerouslySetInnerHTML 로 넣는
 *   구조가 되기 때문입니다. 여기서는 텍스트를 React 노드로만 만들므로
 *   원문에 태그가 섞여 있어도 그대로 글자로 표시됩니다.
 */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let paragraph: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} style={{ margin: "6px 0 10px", paddingLeft: 18 }}>
        {bullets.map((b, i) => (
          <li key={i} style={{ marginBottom: 3 }}>
            {inline(b)}
          </li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  // 마크다운에서 줄바꿈 하나는 줄을 나누지 않습니다. 모델은 문장 중간에
  // 자유롭게 개행하므로, 이어붙이지 않으면 한 문장이 두 문단으로 쪼개져
  // 보입니다 (실제 응답에서 확인).
  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(
      <p key={`p-${blocks.length}`} style={{ margin: "0 0 8px", lineHeight: 1.65 }}>
        {inline(paragraph.join(" "))}
      </p>,
    );
    paragraph = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();

    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      bullets.push(bullet[1]);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushBullets();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushBullets();
      const level = heading[1].length;
      blocks.push(
        <div
          key={`h-${blocks.length}`}
          style={{
            fontWeight: 600,
            fontSize: level <= 2 ? 14 : 13,
            margin: blocks.length ? "16px 0 4px" : "0 0 4px",
            color: "var(--text)",
          }}
        >
          {inline(heading[2])}
        </div>,
      );
      continue;
    }

    flushBullets();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushBullets();

  return <div style={{ fontSize: 13 }}>{blocks}</div>;
}

/** **굵게** 와 `코드` 만 처리합니다. */
function inline(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4)
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2)
      return <code key={i}>{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
