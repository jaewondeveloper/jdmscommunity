# 중동중학교(JDMS) 커뮤니티

## 구조 (분리 호스팅)

| 역할 | 호스팅 | 비용 |
|------|--------|------|
| **페이지** (HTML/CSS/JS) | **GitHub Pages** | 무료 |
| **API + DB + 로그인** | **Cloudflare Workers + D1** | 무료 티어 |

PC에서 Python 서버를 켤 필요 없습니다. GitHub에 HTML만 올리고, API는 Cloudflare에서만 돌아갑니다.

---

## 1. GitHub Pages (프론트)

1. 이 폴더를 `jaewondeveloper/jdmscommunity` 에 push
2. GitHub → **Settings → Pages** → Source: `main` / `/ (root)`
3. 주소 예: `https://jaewondeveloper.github.io/jdmscommunity/`

### config.js 설정 (필수)

`js/config.example.js` 를 참고해 `js/config.js` 의 `API_BASE` 를 Cloudflare Worker URL 로 바꿉니다.

```javascript
window.JDMS_CONFIG = {
  API_BASE: "https://jdms-community-api.계정.workers.dev",
};
```

모든 HTML은 `config.js` → `api.js` 순으로 불러옵니다.

---

## 2. Cloudflare Workers (API 서버)

### 무료인가?

- **Workers**: 일 10만 요청 무료
- **D1** (DB): 읽기/쓰기 무료 한도 넉넉 (소규모 커뮤니티 충분)
- 카드 등록 없이도 Workers 무료 플랜 사용 가능

### 배포 순서

```bash
npm install
npx wrangler login
npx wrangler d1 create jdms-community
# wrangler.toml 의 database_id 를 출력된 ID 로 교체
npm run db:remote
npx wrangler secret put JWT_SECRET
npx wrangler secret put RESEND_API_KEY   # 인증 메일 (선택)
npx wrangler deploy
```

배포 후 나온 URL (예: `https://jdms-community-api.xxx.workers.dev`) 을 `js/config.js` 에 넣고 GitHub에 다시 push.

### 환경 변수 (Cloudflare 대시보드 또는 secret)

| 이름 | 설명 |
|------|------|
| `JWT_SECRET` | JWT 비밀키 |
| `FRONTEND_URL` | `https://jaewondeveloper.github.io/jdmscommunity` (wrangler.toml 기본값) |
| `RESEND_API_KEY` | 인증 메일 발송 |
| `FROM_EMAIL` | 발신 주소 |

---

## 로그인

- `@joongdong.ms.kr` 이메일 인증 링크 (기존 `app.py` 방식 → Worker로 이전)
- 인증 링크는 **Worker URL** (`/api/auth/verify`) → 완료 후 **GitHub Pages** 로 리다이렉트

---

## 로컬 개발 (선택)

- API만: `npm run dev:api`
- 프론트: GitHub Pages와 동일하게 정적 파일 열기 (API는 `config.js`를 로컬 Worker URL로)

`server.py` 는 로컬 테스트용이며, 배포에는 사용하지 않습니다.
