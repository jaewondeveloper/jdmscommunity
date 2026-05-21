# 중동중학교(JDMS) 커뮤니티

중동중학교 학생·교사를 위한 커뮤니티 홈페이지입니다.

## 실행 방법 (서버 없이)

**`index.html`을 더블클릭**하거나, 탐색기에서 `index.html` → 우클릭 → **연결 프로그램** → Chrome/Edge 로 열면 됩니다.

Python 서버는 필요 없습니다. 페이지 이동 시 Google 스타일 로딩 애니메이션이 표시됩니다.

## UI

- **Toss 스타일**: 밝은 회색 배경, 흰 카드, **검정 강조 버튼**, 높은 곡률(둥근 사각형)
- **모바일**: 하단 탭바(홈·게시판·반별·글쓰기), 햄버거 메뉴(드로어), 터치 영역 44px+

## 기능

- 홈: 인기글 카드, 최근 게시글, 반별 바로가기
- 전체 게시판 / 반별 게시판
- 글쓰기, 댓글
- 데이터는 브라우저 `localStorage`에 저장

## 폴더 구조

```
jdms community/
├── assets/jdmslogo.png
├── css/styles.css
├── js/
│   ├── storage.js
│   ├── components.js
│   └── pages/        ← 페이지별 스크립트
├── index.html        ← 여기서 시작
├── board.html
├── class-board.html
├── post.html
└── write.html
```

## 참고

- 같은 폴더 안의 HTML·JS·이미지 경로를 그대로 쓰므로, **폴더 통째로** 옮겨도 됩니다.
- 일부 브라우저는 `file://`에서 localStorage를 제한할 수 있습니다. 그때는 Edge/Chrome 최신 버전을 사용하세요.
