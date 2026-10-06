# 국토부 실거래가 API 응답 기록

> **미검증.** 아직 실호출로 확인하지 않았다. 아래는 공공데이터포털 공개 문서 기준의 태그명이며,
> 인증키를 발급받은 뒤 계획서 Task 3 Step 1을 수행해 실제 응답으로 이 문서를 갱신해야 한다.
> 태그명이 다르면 `src/collect_kor.py`의 `_text()` 호출 문자열만 바꾸면 된다 (테스트는 그대로).

## 엔드포인트

| 자산 | 서비스 | 단지명 태그 |
|---|---|---|
| APT | `1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev` | `아파트` |
| VL  | `1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade` | `연립다세대` |

파라미터: `serviceKey`, `LAWD_CD`(시군구 5자리), `DEAL_YMD`(YYYYMM), `numOfRows`, `pageNo`

## 사용하는 태그

`거래금액`(만원, 콤마 포함) · `년` · `월` · `일` · `전용면적` · `층` · `법정동` ·
`아파트`/`연립다세대` · `거래유형`(중개거래/직거래) · `해제여부`(O 또는 빈값) ·
`해제사유발생일` · `등기일자`

성공 판정: `resultCode`가 `00`. 인증 실패 시 HTTP 200으로 `cmmMsgHeader/returnAuthMsg`가 온다.

## 실호출 방법

`docs/superpowers/plans/2026-09-29-realestate-daily-briefing.md` Task 3 Step 1 참조.
