# 관심 단지·구역 실거래 일일 브리핑

매일 아침 국토부 실거래가(체결)와 네이버부동산 호가를 수집해, 전일 대비 달라진 것만
카드형 이미지로 만들어 Slack으로 보낸다.

- 설계: `docs/superpowers/specs/2026-09-29-realestate-daily-briefing-design.md`
- 구현 계획: `docs/superpowers/plans/2026-09-29-realestate-daily-briefing.md`

## 브리핑에 담기는 것

| 섹션 | 내용 |
|---|---|
| 오늘의 스냅샷 | 신규신고 / 신규매물 / 소멸매물 / 가격조정 / 계약파기 |
| 수도권 상세 | 신규 체결 거래 카드 + 신고가·하락 판정 + 특이사항 뱃지 |
| 호가 동향 | 구역별·거래유형별(매매/전세/월세) 매물 증감, 최저 호가 |
| 가격 조정 | 호가를 내린/올린 매물, 조정폭·경과일·누적 횟수 |
| 지방 온도계 | 대장 단지 월별 거래량, 전년 동월 대비, 평당가 추세, 전고점 대비 |

## 설치

```bash
pip install -r requirements.txt
python -m playwright install chromium
git clone https://github.com/HarimxChoi/anti_bot_scraper.git vendor/anti_bot_scraper
```

`vendor/`는 git에 올라가지 않는다. 새 PC에서는 위 clone을 다시 해야 한다.

## 설정

### 1. 인증키 — `.env`

`.env.example`를 `.env`로 복사해 채운다. **git에 올라가지 않는다.**

```
KOR_API_KEY=공공데이터포털에서_발급한_디코딩된_인증키
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#부동산브리핑
```

- **국토부 인증키**: [data.go.kr](https://www.data.go.kr) 가입 → "국토교통부_아파트 매매 실거래가 자료"와
  "국토교통부_연립다세대 매매 실거래가 자료" 활용신청. 무료
- **Slack 봇 토큰**: 본인 워크스페이스에 앱 생성 → 스코프 `chat:write`, `files:write` →
  워크스페이스 설치 → **발송 채널에 봇을 초대**해야 한다

### 2. 감시 대상 — `watchlist.yml`

```yaml
수도권:                         # 구·법정동 전수 + 네이버 호가 감시
  - 이름: 장위 재개발 일대
    유형: 구역
    lawd_cd: "11290"            # 시군구 코드 5자리 (성북구)
    법정동: ["장위동"]
    좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
    자산: "VL"                  # APT=아파트, VL=빌라, APT:VL=둘 다

지방:                           # 대장 단지만, 상승장 온도계용 (호가 감시 안 함)
  - 이름: 대전 둔산 크로바
    lawd_cd: "30170"
    법정동: ["둔산동"]
    단지명: "크로바"
```

**좌표 얻는 법**: [map.naver.com](https://map.naver.com)에서 해당 지점 우클릭 → URL 복사 →
URL의 `lat`, `lng` 값을 쓴다.

**시군구 코드**: 법정동 코드 10자리 중 앞 5자리.

재개발 구역은 국토부 데이터에 "구역" 개념이 없어 법정동 단위로 본다. 구역 정밀 조준은
네이버 호가 쪽이 좌표로 담당한다.

## 실행

```bash
python -m src.run_daily --dry-run        # Slack 발송 없이 콘솔 출력만
python -m src.run_daily                  # 실제 발송
python -m src.run_daily --date 2026-09-28   # 특정 날짜로 실행
```

결과물은 `briefings/YYYY-MM-DD.html`과 `.png`로 남는다.

## 매일 자동 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

매일 08:00에 돈다. PC가 꺼져 있었으면 다음 부팅 때 밀린 분을 실행한다(`-StartWhenAvailable`).

```powershell
Start-ScheduledTask -TaskName "부동산일일브리핑"                    # 즉시 실행
Get-ScheduledTaskInfo -TaskName "부동산일일브리핑"                  # 마지막 실행 결과
Unregister-ScheduledTask -TaskName "부동산일일브리핑" -Confirm:$false   # 해제
```

로그는 `briefings/run.log`에 쌓인다. 인증키는 로그에 `serviceKey=***`로 마스킹된다.

## 동작 원리 한 가지

국토부 API는 **계약년월 단위로만** 조회되고 응답에 "신고일"이 없다. 즉 "오늘 새로 올라온 건"을
API가 알려주지 않는다. 그래서 매일 최근 3개월치를 통째로 다시 받아 SQLite에 쌓인 전일
스냅샷과 대조해 신규 건을 구한다. 신고 기한이 계약 후 30일이라 3개월 창이 필요하다.

## 실패했을 때

- **국토부 실패** → 3회 재시도 후 경고 배너를 달고 브리핑은 나간다
- **네이버 차단** → 해당 구역만 건너뛴다. `last_seen`을 갱신하지 않아 매물이 소멸로 오판되지 않는다.
  재시도하지 않고 그날은 포기한다
- **렌더 실패** → 이미지 없이 텍스트만 보낸다
- **Slack 실패** → 로컬에 HTML·PNG를 남기고 `runs` 테이블에 기록한다

모든 실행은 `data/briefing.db`의 `runs` 테이블에 남는다.

```sql
SELECT ran_at, stage, ok, count, err FROM runs ORDER BY id DESC LIMIT 20;
```

## 개인정보·약관

- 스크레이퍼가 뱉는 `부동산상호`·`중개사이름`·`전화1`·`전화2` 네 컬럼은 **DB 적재 전에 버린다.**
  원본 xlsx도 읽은 직후 삭제한다. 중개사 개인 연락처에 해당하며 매물 판단에 쓰지 않는다
- 수집 용도는 본인의 투자 판단에 한정한다. 제3자 제공·재배포·상업적 이용을 하지 않는다
- 네이버 서비스 약관상의 위험은 사용자에게 있다. vendor 레포의 "교육·연구 목적" 면책이
  약관을 면제하지 않는다
- 동시 워커 4개, 구역 단위 소규모 조회, 1일 1회, 구역 사이 랜덤 지연으로 부하를 최소화한다.
  차단이 발생하면 우회를 강화하지 않고 그날은 포기한다

## 개발

```bash
python -m pytest tests/ -q
```

`tests/conftest.py`가 모든 테스트에서 외부 수집기를 기본 스텁한다. 테스트가 실제로
네이버·국토부에 요청을 보내는 일은 없다.

## 아직 안 된 것

- 국토부 API 응답의 **실제 태그명을 확인하지 않았다.** `docs/api-notes.md` 참조.
  인증키를 발급한 뒤 계획서 Task 3 Step 1을 수행해 실호출 응답으로 갱신해야 한다.
  태그명이 다르면 `src/collect_kor.py`의 `_text()` 인자 문자열만 바꾸면 된다
- 네이버 실수집(`scrape_target`)과 Slack 실발송은 첫 실전 실행에서 검증된다
- 국토부 전월세 **실거래** 자료는 범위 밖. 전월세는 호가 기준 증감으로만 본다
