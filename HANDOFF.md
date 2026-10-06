# 인수인계 — 관심 단지·구역 실거래 일일 브리핑

> 다른 컴퓨터에서 이어서 작업하기 위한 문서. 마지막 갱신: 2026-10-06

## 한 줄

매일 아침 국토부 실거래가(체결)와 네이버부동산 호가를 수집해, **전일 대비 달라진 것만**
카드형 이미지로 만들어 Slack으로 보내는 파이프라인. 전업투자자가 매주 손으로 하던
확인 네 가지를 대체하는 것이 목적이다.

## 현재 상태 — 코드는 완성, 실환경 검증은 미완

| | 상태 |
|---|---|
| 설계 | 확정 (`docs/superpowers/specs/`) |
| 구현 계획 | 15개 태스크 전부 완료 (`docs/superpowers/plans/`) |
| 코드 | 완성. 테스트 145개 통과 |
| 코드 리뷰 | 완료. Critical 5 + Important 14 전부 수정 |
| **실환경 검증** | **미완 — 아래 "다음에 할 일" 참조** |

**중요: 이 시스템은 아직 단 한 번도 실제 데이터로 돌아본 적이 없다.** 인증키와 Slack
토큰이 없어서다. 모든 로직은 픽스처로 검증했지만, 외부와 실제로 말을 섞는 지점(국토부
API 응답 형식, 네이버 스크레이퍼 실행, Slack 업로드)은 첫 실행에서 처음 확인된다.

---

## 새 컴퓨터에서 시작하기

```bash
git clone https://github.com/autumnn11/realestatesearch.git
cd realestatesearch

pip install -r requirements.txt
python -m playwright install chromium

# vendor 는 저장소에 포함되지 않는다 (별도 MIT 레포)
git clone https://github.com/HarimxChoi/anti_bot_scraper.git vendor/anti_bot_scraper

cp .env.example .env        # 아래 "다음에 할 일" 1번을 보고 채운다
python -m pytest tests/ -q  # 145개 통과하면 환경 준비 완료
```

Windows + Python 3.11+ 기준. 테스트는 네트워크를 쓰지 않으므로 키가 없어도 다 돈다.

---

## 다음에 할 일 (순서대로)

### 1. 공공데이터포털 인증키 발급 — 가장 먼저

[data.go.kr](https://www.data.go.kr) 가입 후 두 개를 활용신청한다. 무료.

- 국토교통부_아파트 매매 실거래가 자료
- 국토교통부_연립다세대 매매 실거래가 자료

발급받은 **디코딩된** 키를 `.env`의 `KOR_API_KEY`에 넣는다.

### 2. API 실호출로 XML 태그명 확정 — 유일한 미검증 전제

**이게 첫 실전 관문이다.** `src/collect_kor.py`의 태그명(`거래금액`, `전용면적`,
`해제여부` 등)은 공개 문서 기준 **추정**이고 실호출로 확인한 적이 없다.

```bash
python - <<'PY'
from pathlib import Path
from src.config import load_env
import requests
key = load_env()["KOR_API_KEY"]
url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
r = requests.get(url, params={"serviceKey": key, "LAWD_CD": "11290",
                              "DEAL_YMD": "202609", "numOfRows": 20}, timeout=30)
Path("tests/fixtures/kor_apt_sample.xml").write_text(r.text, encoding="utf-8")
print(r.status_code, r.text[:1500])
PY
```

확인할 것:

- `resultCode`가 성공일 때 무슨 값인지 (`0` / `00` / `000` 전부 성공으로 처리해뒀다)
- 항목 컨테이너가 `item`인지
- `거래금액 / 년 / 월 / 일 / 전용면적 / 층 / 법정동 / 아파트 / 거래유형 / 해제여부 /
  해제사유발생일 / 등기일자`에 해당하는 실제 태그명
- `totalCount`가 있는지 (페이지네이션이 이걸 본다)

연립다세대(`RTMSDataSvcRHTrade`)도 한 번 불러 단지명 태그가 `연립다세대`인지 확인한다.

**태그명이 다르면 `src/collect_kor.py`의 `_text()` 호출 인자 문자열만 바꾸면 된다.
테스트는 건드릴 필요 없다.** 확인 결과는 `docs/api-notes.md`에 기록하고 "미검증" 경고를 지운다.

### 3. Slack 봇 만들기

본인 워크스페이스에 앱 생성 → 스코프 `chat:write` + `files:write` → 워크스페이스 설치 →
**발송할 채널에 봇을 초대**(이거 빼먹으면 `not_in_channel`로 실패한다). 토큰과 채널을
`.env`에 넣는다.

### 4. watchlist.yml 을 실제 관심 대상으로 교체

지금 들어 있는 장위동·대전 둔산은 **형식 예시**다. 실제로 보시는 구역·단지로 바꿔야 한다.
좌표는 [map.naver.com](https://map.naver.com)에서 우클릭 → URL 복사. 형식은 `README.md` 참조.

### 5. 실행해보고 결과 확인

```bash
python -m src.run_daily --dry-run   # Slack 안 보내고 콘솔 출력만
python -m src.run_daily             # 실제 발송
```

여기서 처음으로 드러나는 것들: 국토부 태그명, 네이버 스크레이퍼 실제 동작, Slack 업로드.

### 6. 스케줄러 등록 — 1~5가 다 된 뒤에

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

**4번 전에 등록하면 안 된다.** 예시 구역(장위동)으로 매일 네이버에 실트래픽을 보내게 된다.

---

## 구조

```
watchlist.yml  (내가 보는 대상)
      │
      ├─→ [수집 A] 국토부 API ───┐
      │     체결된 거래           │
      │                           ├─→ [저장·대조] SQLite ─→ [렌더] HTML ─→ PNG ─→ [발송] Slack
      ├─→ [수집 B] 네이버 스크레이퍼 ┘    신규/소멸/가격변동 diff    Playwright
            호가 매물
```

| 파일 | 책임 |
|---|---|
| `src/config.py` | `.env`, `watchlist.yml` 로딩 |
| `src/models.py` | `TradeRow`, `ListingRow`, `Target` 등 공용 타입 |
| `src/collect_kor.py` | 국토부 수집·XML 파싱·페이지네이션·법정동 필터 |
| `src/collect_naver.py` | vendor 스크레이퍼 래퍼, **개인정보 4컬럼 제거** |
| `src/store.py` | SQLite 스키마, 적재, 전일 대비 대조 |
| `src/judge.py` | 신고가·하락 판정, 특이사항 뱃지, 지방 온도계 |
| `src/render.py` | HTML 렌더, PNG 변환, **과거 재렌더 CLI** |
| `src/notify.py` | Slack Block Kit 조립·발송 |
| `src/run_daily.py` | 일일 오케스트레이션 |

**수집 B가 죽어도 A→저장→렌더→발송은 그대로 돈다.** 네이버 차단은 언젠가 날 일이라
이 분리가 설계의 안전장치다.

---

## 알아야 할 설계 결정 다섯

### 1. "오늘 새로 올라온 건"은 API가 안 알려준다

국토부 API는 **계약년월 단위로만** 조회되고 응답에 "신고일"이 없다. 그래서 매일 최근
3개월치를 통째로 다시 받아 SQLite에 쌓인 전일 스냅샷과 해시로 대조해 신규 건을 구한다.
신고 기한이 계약 후 30일이라 3개월 창이 필요하다. **이게 이 시스템의 핵심이고,
DB를 날리면 "오늘의 신규"를 다시 만들 수 없다.**

### 2. 재개발 구역은 법정동 단위로 본다

국토부 데이터에 "구역" 개념이 없어 구역 안팎을 구분할 수 없다. 구역 경계 지번을 조사해
넣는 대신 법정동 전체를 보고, 구역 정밀 조준은 네이버 호가 쪽이 좌표로 담당한다.

### 3. 단지 식별은 이름만으로 하면 안 된다

`(lawd_cd, dong, complex_name)` 셋을 다 쓴다. 이름만 쓰면 성북 래미안과 대전 래미안이
같은 단지로 묶여 신고가·하락이 엉뚱하게 나온다. (리뷰에서 실제로 "하락 -41.7%"가 재현됐다)

### 4. 모르면 말하지 않는다

- 비교 표본 3건 미만 → 신고가 판정 안 함 ("비교 표본 부족")
- 단지 층 표본 5건 미만 → `최상층` 뱃지 안 붙임
- 작년 그 달 데이터 없음 → 전년 동월 대비 안 보여줌 (`―`)

없는 신호를 만들어내느니 빈칸을 두는 쪽을 택했다. 첫 1년은 이력이 얇아서 특히 중요하다.

### 5. 수집 0건은 성공이 아니다

네이버 수집이 0건이면 `NaverEmpty`로 그 구역을 건너뛴다. 빈 결과를 성공으로 치면
전날 매물이 **전량 소멸**로 찍힌다. 차단이 조용한 빈 결과로 나타나는 게 흔하다.

---

## 검증된 것 / 안 된 것

**검증됨 (테스트 145개 + 실제 실행)**

- 전일 대비 대조 로직 (멱등 재실행, 파기 감지, 소멸·가격변동)
- 판정 규칙 전부 (1년 창, 표본 문턱, 뱃지, 온도계)
- XML 파싱 (에러 본문 구분, 지저분한 금액, 연 경계)
- 개인정보 4컬럼 제거
- 인증키 로그 마스킹 — 실제 키를 넣어 `serviceKey=***` 확인
- HTML→PNG 렌더 — 실제 이미지를 눈으로 확인
- PowerShell 스크립트 — 구문·경로 해석·스케줄 객체 생성까지 실행

**검증 안 됨 (키·승인 필요)**

- 국토부 API 실제 응답의 태그명·엔드포인트 (위 2번)
- 네이버 스크레이퍼 실제 실행 (`scrape_target`) — 남의 서비스라 승인 없이 안 돌렸다
- Slack 실제 발송 (`notify.post`)
- 스케줄러 실제 등록

---

## 이월한 Minor 12건

급하지 않지만 알고는 있어야 할 것들. 고칠지는 판단해서 하면 된다.

1. 푸터에 데이터 기준 시각이 없다 (스펙 8절 요구)
2. `보합` 판정 카드에 표시 분기가 없어 판정이 안 보인다
3. `KorApiError` 체이닝 시 원본 예외 메시지의 URL은 마스킹 안 됨 (현재 아무도 로깅 안 함)
4. `TradeDiff.total`, `ListingDiff.counts`가 계산만 되고 안 쓰임
5. `trade_hash`에 `asset`이 빠져 APT/VL 충돌 가능 (확률 낮음)
6. 가격 조정 "누적 횟수"가 인상도 같이 셈 (스펙은 반복 인하를 말함)
7. 같은 날 두 번 가격이 바뀌면 `INSERT OR REPLACE`로 하나만 남음
8. `judge_price`가 거래당 두 번 호출됨 (`_line`, `_card`)
9. `register_task.ps1` 도움말의 `-Confirm:` 표시가 잘림
10. `briefings/` 경로가 CWD 상대 — 다른 디렉터리에서 수동 실행하면 결과가 흩어짐
11. `run.log`가 무한히 커진다 (로테이션 없음)
12. 지하층(`층 = -1`)에 "2층 이하" 뱃지가 붙음

---

## 범위 밖 (의도적으로 안 함)

- 부동산 뉴스·정책 헤드라인
- 재개발 구역 행정 고시·공고 감시
- 등기부등본 열람 (건당 결제 + 본인인증)
- 중개사 연락처 수집 (개인정보)
- 국토부 전월세 **실거래** 자료 — 전월세는 호가 기준 증감으로만 본다

---

## 개인정보·약관 (지켜야 할 선)

- 스크레이퍼 출력의 `부동산상호`·`중개사이름`·`전화1`·`전화2`는 **DB 적재 전에 버린다.**
  원본 xlsx도 읽은 직후 삭제한다. 이 규칙을 되돌리지 말 것
- 수집 용도는 본인 투자 판단에 한정. 제3자 제공·재배포·상업적 이용 안 함
- 네이버 약관상의 위험은 사용자에게 있다. vendor 레포의 "교육·연구 목적" 면책이
  약관을 면제하지 않는다
- **차단당하면 우회를 강화하지 않고 그날은 포기한다.** 동시 워커 4개, 1일 1회,
  구역 사이 랜덤 지연을 유지할 것

---

## 참고 문서

| 문서 | 내용 |
|---|---|
| `README.md` | 설치·설정·실행·운영 |
| `docs/superpowers/specs/2026-09-29-...-design.md` | 설계 (무엇을·왜) |
| `docs/superpowers/plans/2026-09-29-...md` | 구현 계획 15개 태스크 (어떻게) |
| `docs/api-notes.md` | 국토부 API 응답 기록 — **현재 미검증 상태** |

## 테스트

```bash
python -m pytest tests/ -q     # 145개, 약 30초
```

`tests/conftest.py`가 모든 테스트에서 외부 수집기를 기본 스텁한다. 테스트가 실제로
네이버·국토부에 요청을 보내는 일은 없다. **이 가드를 풀지 말 것** — 한 번 실수로
테스트가 네이버를 긁어 파일 하나에 192초를 쓴 적이 있다.
