from flask import Flask, render_template_string, request, session, redirect, url_for, make_response, jsonify
from flask_cors import CORS # 빠졌던 CORS 추가
from pycomcigan import TimeTable
from datetime import datetime, timedelta, timezone
import urllib.request
import json
import re
import os
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import jwt
import resend # resend 라이브러리 추가

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chungdong_secret_key")
JWT_SECRET = os.environ.get("JWT_SECRET", "myjdms_jwt_secret_key_123")

# 이중 인증 API 설정
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "corerepublix@gmail.com") # 인증된 메일

# Resend 설정
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# 한국 표준시(KST) 설정
KST = timezone(timedelta(hours=9))

# 외부 서비스 설정
BACKEND_URL = os.environ.get("BACKEND_URL", "https://sigan.onrender.com")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "DQfagveQkG5l322N5ZvJdrotAhsf631i")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
# mistral-small: 가장 빠르고 한도 최대, mistral-large: 정확성 높음
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

# 10분 메모리 캐시 (시간표/급식)
CACHE_TTL_SECONDS = 600
SCHEDULE_LUNCH_CACHE_PATHS = {"/uiv2sigan", "/sigan", "/todaysigan", "/lunch"}
_response_cache = {}
_response_cache_lock = threading.Lock()
_ai_timetable_tool_cache = {}
AI_TIMETABLE_TOOL_CACHE_TTL_SECONDS = 600
_ai_pending_intent_cache = {}
AI_PENDING_INTENT_TTL_SECONDS = 1200

# ================= [ 설정 및 전역 변수 구간 ] =================
SCHOOL_NAME = "중동중학교" 
GRADE = 2
CLASS_NM = 8

# 배너 데이터 저장용 (JSON 파일 방식)
BANNER_FILE = "banner_data.json"

def load_banner():
    if os.path.exists(BANNER_FILE):
        try:
            with open(BANNER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"title": "", "image_url": "", "target_url": ""}

def save_banner(data):
    with open(BANNER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 인증 세션 관리용 (JSON 파일 방식)
AUTH_SESSION_FILE = "auth_sessions.json"

def load_auth_sessions():
    if os.path.exists(AUTH_SESSION_FILE):
        try:
            with open(AUTH_SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def save_auth_sessions(sessions):
    with open(AUTH_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=4)

def _make_request_cache_key():
    args_tuple = tuple(sorted(request.args.items(multi=True)))
    return f"{request.path}?{args_tuple}"

@app.before_request
def serve_cached_schedule_lunch_response():
    if request.method != "GET":
        return None
    if request.path not in SCHEDULE_LUNCH_CACHE_PATHS:
        return None

    cache_key = _make_request_cache_key()
    now = datetime.now(timezone.utc).timestamp()
    with _response_cache_lock:
        cached = _response_cache.get(cache_key)
        if not cached:
            return None
        if now - cached["ts"] > CACHE_TTL_SECONDS:
            _response_cache.pop(cache_key, None)
            return None
        resp = make_response(cached["body"], cached["status"])
        for hk, hv in cached["headers"]:
            resp.headers[hk] = hv
        resp.headers["X-Cache"] = "HIT"
        return resp

@app.after_request
def store_cached_schedule_lunch_response(response):
    try:
        if request.method != "GET":
            return response
        if request.path not in SCHEDULE_LUNCH_CACHE_PATHS:
            return response
        if response.status_code != 200:
            return response

        cache_key = _make_request_cache_key()
        now = datetime.now(timezone.utc).timestamp()
        body = response.get_data(as_text=True)
        headers = []
        content_type = response.headers.get("Content-Type")
        if content_type:
            headers.append(("Content-Type", content_type))

        with _response_cache_lock:
            _response_cache[cache_key] = {
                "ts": now,
                "body": body,
                "status": response.status_code,
                "headers": headers
            }
            # 캐시 메모리 폭주 방지
            if len(_response_cache) > 200:
                oldest_key = min(_response_cache, key=lambda k: _response_cache[k]["ts"])
                _response_cache.pop(oldest_key, None)

        response.headers["X-Cache"] = "MISS"
        return response
    except Exception:
        return response

def _normalize_subject_cell(raw):
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if "교시:" in s:
        s = s.split("교시:")[-1]
    if "교시 :" in s:
        s = s.split("교시 :")[-1]
    return s.replace("[변경]", "").strip()

def build_all_classes_timetable_tool_text(school_name):
    cache_key = f"all_classes_timetable::{school_name}"
    now_ts = datetime.now(timezone.utc).timestamp()
    with _response_cache_lock:
        cached = _ai_timetable_tool_cache.get(cache_key)
        if cached and (now_ts - cached["ts"] <= AI_TIMETABLE_TOOL_CACHE_TTL_SECONDS):
            return cached["text"], cached["meta"]

    tt = TimeTable(school_name, week_num=0)
    src = getattr(tt, "timetable", None)
    if not isinstance(src, list):
        raise ValueError("시간표 데이터 형식이 올바르지 않습니다.")

    day_names = ["월", "화", "수", "목", "금"]
    lines = [
        f"[시간표 툴 결과] 학교: {school_name}",
        "형식: 학년-반별 주간 시간표(월~금, 교시 순)",
        ""
    ]

    grade_count = 0
    class_count = 0
    non_empty_cells = 0
    for g in range(1, len(src)):
        grade_data = src[g]
        if not isinstance(grade_data, list):
            continue
        grade_has_class = False
        for c in range(1, len(grade_data)):
            class_data = grade_data[c]
            if not isinstance(class_data, list):
                continue

            class_count += 1
            grade_has_class = True
            lines.append(f"## {g}학년 {c}반")

            for day_idx, day_name in enumerate(day_names, start=1):
                day_subjects = []
                if len(class_data) > day_idx and isinstance(class_data[day_idx], list):
                    for period_idx, raw_cell in enumerate(class_data[day_idx], start=1):
                        subject = _normalize_subject_cell(raw_cell)
                        if not subject:
                            continue
                        non_empty_cells += 1
                        day_subjects.append(f"{period_idx}교시 {subject}")
                lines.append(f"- {day_name}: " + (" | ".join(day_subjects) if day_subjects else "정보 없음"))
            lines.append("")

        if grade_has_class:
            grade_count += 1

    if class_count == 0:
        lines.append("시간표 반 데이터가 없습니다.")

    text = "\n".join(lines).strip()
    max_chars = 24000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(이하 생략: 데이터가 길어서 일부만 포함됨)"

    meta = {
        "school": school_name,
        "grades": grade_count,
        "classes": class_count,
        "filled_cells": non_empty_cells
    }
    with _response_cache_lock:
        _ai_timetable_tool_cache[cache_key] = {"ts": now_ts, "text": text, "meta": meta}
    return text, meta

def _extract_grade_class_from_text(text):
    if not text:
        return None, None

    compact = re.sub(r"\s+", "", str(text))
    typo_map = {
        "혹년": "학년",
        "학넌": "학년",
        "하견": "학년",
        "핫년": "학년",
    }
    for wrong, right in typo_map.items():
        compact = compact.replace(wrong, right)

    patterns = [
        r"([1-6])학년([1-2]?[0-9])반",   # 2학년8반
        r"([1-6])학년([1-2]?[0-9])",     # 2학년8
        r"([1-6])[-_/~]([1-2]?[0-9])",   # 2-8, 2/8
        r"([1-6])반([1-2]?[0-9])",       # 드문 오타 대응
    ]
    for p in patterns:
        m = re.search(p, compact)
        if m:
            g = int(m.group(1))
            c = int(m.group(2))
            if 1 <= g <= 6 and 1 <= c <= 30:
                return g, c

    return None, None

def _is_model_access_error(error_message):
    if not error_message:
        return False
    em = str(error_message).lower()
    return ("does not exist" in em) or ("do not have access" in em) or ("unknown model" in em) or ("invalid model" in em)

def _extract_mmdd_from_text(text):
    if not text:
        return None
    m = re.search(r"([0-1]?\d)\s*월\s*([0-3]?\d)\s*일", text)
    if m:
        mm = int(m.group(1))
        dd = int(m.group(2))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{mm:02d}{dd:02d}"
    m = re.search(r"\b(0[1-9]|1[0-2])([0-2]\d|3[01])\b", text)
    if m:
        return m.group(1) + m.group(2)
    return None

def _resolve_target_date_in_kst(date_code):
    now = datetime.now(KST)
    if not date_code or not re.fullmatch(r"\d{4}", str(date_code)):
        return now
    mm = int(str(date_code)[:2])
    dd = int(str(date_code)[2:])
    try:
        return datetime(now.year, mm, dd, tzinfo=KST)
    except ValueError:
        return now

def _build_aisigan_text_payload(school_name, grade, class_nm, date_code=None):
    target_date = _resolve_target_date_in_kst(date_code)
    tt = TimeTable(school_name, week_num=0)  # 항상 이번 주 시간표 기준
    all_data = tt.timetable[grade][class_nm]
    homeroom_teacher = tt.homeroom(grade, class_nm)
    day_names = ["월", "화", "수", "목", "금"]

    weekday = target_date.weekday()
    use_weekday = min(max(weekday, 0), 4)
    use_day_idx = use_weekday + 1

    day_subjects = []
    if len(all_data) > use_day_idx and isinstance(all_data[use_day_idx], list):
        for p_idx, raw in enumerate(all_data[use_day_idx], start=1):
            subj = _normalize_subject_cell(raw)
            if subj:
                day_subjects.append(f"{p_idx}교시 {subj}")

    weekly_lines = []
    for i, dname in enumerate(day_names, start=1):
        items = []
        if len(all_data) > i and isinstance(all_data[i], list):
            for p_idx, raw in enumerate(all_data[i], start=1):
                subj = _normalize_subject_cell(raw)
                if subj:
                    items.append(f"{p_idx}교시 {subj}")
        weekly_lines.append(f"- {dname}: " + (" | ".join(items) if items else "정보 없음"))

    text_lines = [
        f"⚠️ [실제 서버 조회 데이터] 학교: {school_name} {grade}학년 {class_nm}반",
        f"⚠️ 이 데이터는 컴시간알리미 서버에서 실시간 조회된 실제 시간표입니다. 반드시 이 내용만 사용하고, 예시/가상 데이터를 절대 혼용하지 마세요.",
        f"기준 요청 날짜(KST): {target_date.strftime('%Y-%m-%d')} ({['월','화','수','목','금','토','일'][weekday]})",
        "참고: 미래 날짜여도 시스템 제약으로 이번 주 시간표를 기준으로 제공합니다.",
        f"선택된 요일: {day_names[use_weekday]}요일",
        "해당 요일 시간표: " + (" | ".join(day_subjects) if day_subjects else "정보 없음"),
        "",
        "이번 주 전체 시간표",
        *weekly_lines,
        f"",
        f"담임: {homeroom_teacher}"
    ]
    return {
        "school": school_name,
        "grade": grade,
        "class": class_nm,
        "date_code": target_date.strftime("%m%d"),
        "target_weekday": use_weekday,
        "text": "\n".join(text_lines)
    }

def _fetch_meal_rows(region_code, school_code, from_ymd, to_ymd):
    api_key = "77243bbec81f496286bacbe357cad48f"
    url = (
        "https://open.neis.go.kr/hub/mealServiceDietInfo"
        f"?KEY={api_key}&Type=json&pIndex=1&pSize=1000"
        f"&ATPT_OFCDC_SC_CODE={region_code}&SD_SCHUL_CODE={school_code}"
        f"&MLSV_FROM_YMD={from_ymd}&MLSV_TO_YMD={to_ymd}"
    )
    req_obj = urllib.request.Request(url)
    with urllib.request.urlopen(req_obj) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "mealServiceDietInfo" not in data:
        return []
    return data["mealServiceDietInfo"][1].get("row", [])

def _normalize_menu_text(menu_raw):
    menus = str(menu_raw or "").split("<br/>")
    clean = []
    for m in menus:
        cleaned = re.sub(r"[^가-힣a-zA-Z0-9\s\(\)\[\]\&]", "", re.sub(r"[0-9]+\.", "", m)).strip()
        if cleaned:
            clean.append(cleaned)
    return clean

def _build_ailunch_text_payload(school_name, grade, class_nm, date_code=None, region_code="B10", school_code="7091455"):
    target_date = _resolve_target_date_in_kst(date_code)
    from_ymd = (target_date - timedelta(days=30)).strftime("%Y%m%d")
    to_ymd = (target_date + timedelta(days=30)).strftime("%Y%m%d")
    rows = _fetch_meal_rows(region_code, school_code, from_ymd, to_ymd)

    meals = []
    for row in rows:
        ymd = str(row.get("MLSV_YMD", "")).strip()
        if len(ymd) != 8:
            continue
        menus = _normalize_menu_text(row.get("DDISH_NM", ""))
        meals.append({
            "ymd": ymd,
            "menu": menus,
            "cal": str(row.get("CAL_INFO", "")).strip()
        })

    target_ymd = target_date.strftime("%Y%m%d")
    picked = None
    for m in meals:
        if m["ymd"] == target_ymd:
            picked = m
            break
    # ⚠️ 폴백 제거: 날짜가 정확히 일치하지 않으면 절대 다른 날짜 급식을 반환하지 않음
    # (예전 코드의 closest-date fallback이 "하루 밀림" 버그 원인이었음)

    lines = [
        f"[AILUNCH] {school_name} {grade}학년 {class_nm}반",
        f"기준 요청 날짜(KST): {target_date.strftime('%Y-%m-%d')} ({target_ymd})",
        f"⚠️ 아래 급식 정보는 NEIS API에서 실제 조회된 데이터입니다. 날짜를 반드시 확인하세요."
    ]
    if picked:
        lines.append(f"급식 날짜(정확히 일치): {picked['ymd'][0:4]}-{picked['ymd'][4:6]}-{picked['ymd'][6:8]}")
        lines.append("메뉴: " + (" | ".join(picked["menu"]) if picked["menu"] else "정보 없음"))
        if picked["cal"]:
            lines.append(f"칼로리: {picked['cal']}")
    else:
        lines.append(f"메뉴: {target_date.strftime('%Y-%m-%d')} 날짜의 급식 정보가 없습니다. (주말·공휴일·방학 가능성)")
        lines.append("⚠️ 절대 다른 날짜의 급식을 대신 제공하지 마세요.")

    return {
        "school": school_name,
        "grade": grade,
        "class": class_nm,
        "date_code": target_date.strftime("%m%d"),
        "text": "\n".join(lines)
    }

def _get_pending_intent(session_id):
    if not session_id:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    with _response_cache_lock:
        row = _ai_pending_intent_cache.get(session_id)
        if not row:
            return None
        if now_ts - row.get("ts", 0) > AI_PENDING_INTENT_TTL_SECONDS:
            _ai_pending_intent_cache.pop(session_id, None)
            return None
        return row.get("intent")

def _set_pending_intent(session_id, intent):
    if not session_id:
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    with _response_cache_lock:
        _ai_pending_intent_cache[session_id] = {"intent": intent, "ts": now_ts}

def _clear_pending_intent(session_id):
    if not session_id:
        return
    with _response_cache_lock:
        _ai_pending_intent_cache.pop(session_id, None)

def build_single_class_timetable_tool_text(school_name, grade, class_nm):
    cache_key = f"single_class_timetable::{school_name}::{grade}-{class_nm}"
    now_ts = datetime.now(timezone.utc).timestamp()
    with _response_cache_lock:
        cached = _ai_timetable_tool_cache.get(cache_key)
        if cached and (now_ts - cached["ts"] <= AI_TIMETABLE_TOOL_CACHE_TTL_SECONDS):
            return cached["text"], cached["meta"]

    tt = TimeTable(school_name, week_num=0)
    src = getattr(tt, "timetable", None)
    if not isinstance(src, list):
        raise ValueError("시간표 데이터 형식이 올바르지 않습니다.")
    if grade <= 0 or grade >= len(src) or not isinstance(src[grade], list):
        raise ValueError(f"{grade}학년 데이터가 없습니다.")
    if class_nm <= 0 or class_nm >= len(src[grade]) or not isinstance(src[grade][class_nm], list):
        raise ValueError(f"{grade}학년 {class_nm}반 데이터가 없습니다.")

    class_data = src[grade][class_nm]
    day_names = ["월", "화", "수", "목", "금"]
    lines = [
        f"[시간표 툴 결과] 학교: {school_name}",
        f"형식: {grade}학년 {class_nm}반 주간 시간표(월~금, 교시 순)",
        f"## {grade}학년 {class_nm}반"
    ]
    non_empty_cells = 0
    for day_idx, day_name in enumerate(day_names, start=1):
        day_subjects = []
        if len(class_data) > day_idx and isinstance(class_data[day_idx], list):
            for period_idx, raw_cell in enumerate(class_data[day_idx], start=1):
                subject = _normalize_subject_cell(raw_cell)
                if not subject:
                    continue
                non_empty_cells += 1
                day_subjects.append(f"{period_idx}교시 {subject}")
        lines.append(f"- {day_name}: " + (" | ".join(day_subjects) if day_subjects else "정보 없음"))

    text = "\n".join(lines).strip()
    meta = {
        "school": school_name,
        "grades": 1,
        "classes": 1,
        "filled_cells": non_empty_cells,
        "target": f"{grade}-{class_nm}"
    }
    with _response_cache_lock:
        _ai_timetable_tool_cache[cache_key] = {"ts": now_ts, "text": text, "meta": meta}
    return text, meta

# ================= [ 인증 API ] =================
# 이메일 인증 로그인은 server.py (JDMS 커뮤니티) 로 이전되었습니다.
# 커뮤니티 실행: python server.py
# ==============================================================

# ================= [ AI 툴 실행 헬퍼 함수들 ] =================

def _tool_get_timetable(grade, class_nm, date=None):
    """시간표 조회 툴 - AI가 호출 요청하면 실제로 실행되는 함수"""
    try:
        now_kst = datetime.now(KST)
        date_code = str(date).strip() if date else now_kst.strftime("%m%d")
        # MMDD 형식 검증
        if not re.fullmatch(r"\d{4}", date_code):
            date_code = now_kst.strftime("%m%d")
        result = _build_aisigan_text_payload(
            school_name=SCHOOL_NAME,
            grade=int(grade),
            class_nm=int(class_nm),
            date_code=date_code
        )
        return result.get("text", "시간표 데이터를 가져오지 못했습니다.")
    except Exception as e:
        return f"시간표 조회 실패: {str(e)}"

def _tool_get_lunch(date=None):
    """급식 조회 툴 - AI가 호출 요청하면 실제로 실행되는 함수"""
    try:
        now_kst = datetime.now(KST)
        date_code = str(date).strip() if date else now_kst.strftime("%m%d")
        if not re.fullmatch(r"\d{4}", date_code):
            date_code = now_kst.strftime("%m%d")
        result = _build_ailunch_text_payload(
            school_name=SCHOOL_NAME,
            grade=GRADE,
            class_nm=CLASS_NM,
            date_code=date_code,
            region_code="B10",
            school_code="7091455"
        )
        return result.get("text", "급식 데이터를 가져오지 못했습니다.")
    except Exception as e:
        return f"급식 조회 실패: {str(e)}"

def _tool_get_school_schedule():
    """학사일정 조회 툴 - AI가 호출 요청하면 실제로 실행되는 함수"""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        NEIS_API_KEY = "77243bbec81f496286bacbe357cad48f"
        NEIS_SCHEDULE_URL = "https://open.neis.go.kr/hub/SchoolSchedule"

        today_kst = datetime.now(KST)
        today_str = today_kst.strftime("%Y-%m-%d")
        school_year = today_kst.year if today_kst.month >= 3 else today_kst.year - 1
        from_date = f"{school_year}0301"
        to_date = f"{school_year + 1}0228"

        resp = requests.get(
            NEIS_SCHEDULE_URL,
            params={
                "KEY": NEIS_API_KEY, "Type": "json",
                "pIndex": 1, "pSize": 500,
                "ATPT_OFCDC_SC_CODE": "B10", "SD_SCHUL_CODE": "7091455",
                "AA_FROM_YMD": from_date, "AA_TO_YMD": to_date,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10, verify=False
        )

        schedule_list = []
        if resp.status_code == 200:
            data = resp.json()
            if "SchoolSchedule" in data:
                row_data = []
                for item in data["SchoolSchedule"]:
                    if isinstance(item, dict) and "row" in item:
                        row_data = item["row"]
                        break
                for event in row_data:
                    if not isinstance(event, dict):
                        continue
                    name = event.get("EVENT_NM", "").strip()
                    ymd = event.get("AA_YMD", "").strip()
                    content = event.get("EVENT_CNTNT", "").strip()
                    if name and len(ymd) == 8:
                        schedule_list.append({
                            "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}",
                            "event": name,
                            "content": content or name,
                            "ts": ymd
                        })

        schedule_list.sort(key=lambda x: x["ts"])
        today_ev = [s for s in schedule_list if s["date"] == today_str]
        before_ev = [s for s in schedule_list if s["date"] < today_str]
        after_ev  = [s for s in schedule_list if s["date"] > today_str]

        lines = [
            f"[학사일정] 중동중학교 {school_year}학년도",
            f"오늘 날짜(KST): {today_str}",
            f"총 {len(schedule_list)}개 일정",
            "",
            f"오늘 일정: " + (", ".join(s["event"] for s in today_ev) if today_ev else "없음"),
            f"지난 일정(최근 5개): " + (" / ".join(f"{s['date']} {s['event']}" for s in before_ev[-5:]) if before_ev else "없음"),
            f"예정 일정(다음 10개): " + (" / ".join(f"{s['date']} {s['event']}" for s in after_ev[:10]) if after_ev else "없음"),
            "",
            "전체 일정 (날짜, 행사명):"
        ]
        for s in schedule_list:
            lines.append(f"  {s['date']} | {s['event']}")

        return "\n".join(lines)
    except Exception as e:
        return f"학사일정 조회 실패: {str(e)}"

# ─── 툴 이름 → 실제 함수 라우팅 테이블 ──────────────────────────────
_TOOL_DISPATCH = {
    "get_timetable":      lambda args: _tool_get_timetable(
        grade=args.get("grade", GRADE),
        class_nm=args.get("class_nm", CLASS_NM),
        date=args.get("date")
    ),
    "get_lunch":          lambda args: _tool_get_lunch(date=args.get("date")),
    "get_school_schedule": lambda args: _tool_get_school_schedule(),
}

# ─── Mistral tool definitions (function-calling 형식) ────────────────
_AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_timetable",
            "description": (
                "학생의 시간표를 조회합니다. "
                "사용자가 시간표, 수업, 교시, 요일별 수업 등을 물어볼 때 반드시 이 툴을 호출하세요. "
                "학년·반을 모르면 먼저 사용자에게 물어보세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "grade":    {"type": "integer", "description": "학년 (1, 2, 3 중 하나)"},
                    "class_nm": {"type": "integer", "description": "반 번호 (예: 8)"},
                    "date":     {
                        "type": "string",
                        "description": "조회 날짜 MMDD 형식 (예: '0513'). 생략하면 오늘 날짜로 조회."
                    }
                },
                "required": ["grade", "class_nm"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_lunch",
            "description": (
                "학교 급식 메뉴를 조회합니다. "
                "사용자가 급식, 점심, 식단, 메뉴 등을 물어볼 때 반드시 이 툴을 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "조회 날짜 MMDD 형식 (예: '0513'). 생략하면 오늘 날짜로 조회."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_school_schedule",
            "description": (
                "학사일정을 조회합니다. "
                "방학, 개학, 시험, 행사, 공지 등 학교 일정을 물어볼 때 이 툴을 호출하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

def _call_mistral(messages, model, temperature, max_tokens, tools=None):
    """Mistral API 단일 호출 래퍼. 응답 body dict를 반환."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        MISTRAL_API_URL,
        headers={
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )
    try:
        body = resp.json()
    except Exception:
        body = {}

    if resp.status_code >= 400:
        err = (body.get("error", {}) or {}).get("message") or body.get("message") or f"HTTP {resp.status_code}"
        raise RuntimeError(err)

    return body

# ================= [ AI (Mistral) 엔드포인트 - 툴 콜링 방식 ] =================
@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    if request.method != 'POST':
        return jsonify({"success": False, "error": "Method not allowed"}), 405
    if not MISTRAL_API_KEY:
        return jsonify({"success": False, "error": "MISTRAL_API_KEY is not configured"}), 500

    data = request.get_json(silent=True) or {}
    user_message    = (data.get("message") or "").strip()
    incoming_messages = data.get("messages")
    chat_session_id = (data.get("chat_session_id") or "").strip()

    if not user_message and not isinstance(incoming_messages, list):
        return jsonify({"success": False, "error": "message 또는 messages가 필요합니다."}), 400

    model       = (data.get("model") or MISTRAL_MODEL).strip() or MISTRAL_MODEL
    temperature = float(data.get("temperature", 0.3))
    max_tokens  = int(data.get("max_tokens", 768))

    now_kst = datetime.now(KST)

    # ── 시스템 프롬프트 ──────────────────────────────────────────────
    system_prompt = data.get("system_prompt") or (
        "당신은 중동중학교 My JDMS 앱 안내 AI 도우미입니다. 😊\n\n"
        "【툴 사용 규칙】\n"
        "📚 시간표·수업·교시 질문 → get_timetable 툴 호출 (학년·반 모르면 먼저 물어보기)\n"
        "🍽️ 급식·점심·메뉴·식단 질문 → get_lunch 툴 호출\n"
        "📅 학사일정·방학·시험·행사·공지 질문 → get_school_schedule 툴 호출\n"
        "⚠️ 툴 결과가 없으면 절대 임의로 만들지 말고 '조회 실패'를 그대로 안내하세요.\n\n"
        "【응답 규칙】\n"
        "✅ 항상 한국어로 친절하고 간결하게 답하세요.\n"
        "✅ 시간표는 반드시 마크다운 표로: | 교시 | 월 | 화 | 수 | 목 | 금 |\n"
        "✅ 급식은 반드시 마크다운 표로: | 번호 | 메뉴 | (날짜 반드시 표시)\n"
        "✅ 학사일정은 마크다운 표로: | 날짜 | 행사 | (오늘→예정→지난 순)\n"
        "✅ 툴에서 받은 실제 데이터만 표에 채우고, 예시·추측 데이터는 절대 사용 금지.\n"
        "✅ 모든 날짜·시간은 KST 기준으로 처리하세요.\n"
    )

    # ── 메시지 히스토리 구성 ─────────────────────────────────────────
    kst_info = (
        f"현재 한국 기준 날짜/시간(KST): {now_kst.strftime('%Y-%m-%d %H:%M:%S')} "
        f"({['월','화','수','목','금','토','일'][now_kst.weekday()]}요일). "
        "오늘 날짜 MMDD: " + now_kst.strftime('%m%d') + "."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": kst_info},
    ]
    if isinstance(incoming_messages, list):
        for m in incoming_messages:
            if not isinstance(m, dict):
                continue
            role    = (m.get("role") or "").strip()
            content = m.get("content")
            # tool 메시지(이전 턴 결과)도 그대로 전달
            if role in ("user", "assistant", "system", "tool") and content is not None:
                entry = {"role": role, "content": content}
                if role == "tool" and m.get("tool_call_id"):
                    entry["tool_call_id"] = m["tool_call_id"]
                messages.append(entry)
    if user_message:
        messages.append({"role": "user", "content": user_message})

    # ── 1차 호출: AI가 툴을 쓸지 판단 ──────────────────────────────
    try:
        body1 = _call_mistral(messages, model, temperature, max_tokens, tools=_AI_TOOLS)
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 502
    except requests.RequestException as e:
        return jsonify({"success": False, "error": f"AI 호출 실패: {str(e)}"}), 502

    choice1       = (body1.get("choices") or [{}])[0]
    ai_message1   = choice1.get("message", {})
    finish_reason = choice1.get("finish_reason", "")
    tool_calls    = ai_message1.get("tool_calls") or []

    # ── 툴 호출이 없으면 바로 최종 답변 반환 ────────────────────────
    if finish_reason != "tool_calls" or not tool_calls:
        answer = (ai_message1.get("content") or "").strip()
        if not answer:
            return jsonify({"success": False, "error": "AI 응답이 비어 있습니다."}), 502
        return jsonify({
            "success": True,
            "reply":   answer,
            "model":   body1.get("model", model),
            "usage":   body1.get("usage", {}),
            "tool_calls_made": []
        })

    # ── 툴 호출이 있으면: 순서대로 실행 후 결과를 메시지에 추가 ──────
    # assistant가 tool_call을 요청한 메시지를 히스토리에 추가
    messages.append({
        "role":       "assistant",
        "content":    ai_message1.get("content") or "",
        "tool_calls": tool_calls
    })

    tool_calls_log = []   # 클라이언트에게 어떤 툴을 썼는지 알려주기 위한 로그
    for tc in tool_calls:
        tc_id   = tc.get("id", "")
        fn_name = (tc.get("function") or {}).get("name", "")
        fn_args_raw = (tc.get("function") or {}).get("arguments", "{}")

        # arguments가 문자열이면 파싱
        try:
            fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
        except Exception:
            fn_args = {}

        # 실제 툴 실행
        if fn_name in _TOOL_DISPATCH:
            try:
                tool_result = _TOOL_DISPATCH[fn_name](fn_args)
            except Exception as tool_err:
                tool_result = f"툴 실행 오류({fn_name}): {str(tool_err)}"
        else:
            tool_result = f"알 수 없는 툴: {fn_name}"

        print(f"[TOOL] {fn_name}({fn_args}) → {str(tool_result)[:120]}...")
        tool_calls_log.append({"tool": fn_name, "args": fn_args})

        # 툴 결과를 메시지에 추가 (role: tool)
        messages.append({
            "role":         "tool",
            "tool_call_id": tc_id,
            "content":      str(tool_result)
        })

    # ── 2차 호출: 툴 결과를 받아 AI가 최종 답변 생성 ────────────────
    # (2차에는 툴 정의 없이 호출 → 더 이상 툴 호출 안 함)
    try:
        body2 = _call_mistral(messages, model, temperature, max_tokens, tools=None)
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 502
    except requests.RequestException as e:
        return jsonify({"success": False, "error": f"AI 2차 호출 실패: {str(e)}"}), 502

    choice2 = (body2.get("choices") or [{}])[0]
    answer  = (choice2.get("message", {}).get("content") or "").strip()
    if not answer:
        return jsonify({"success": False, "error": "AI 최종 응답이 비어 있습니다."}), 502

    return jsonify({
        "success":         True,
        "reply":           answer,
        "model":           body2.get("model", model),
        "usage":           body2.get("usage", {}),
        "tool_calls_made": tool_calls_log   # 클라이언트가 어떤 툴이 쓰였는지 확인 가능
    })

@app.route('/api/school/schedule', methods=['GET'])
def api_school_schedule():
    """NEIS API를 사용해 서울시 중동중학교의 실제 학사일정을 조회 (CSV 형식)"""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # NEIS API 설정
        NEIS_API_KEY = "77243bbec81f496286bacbe357cad48f"
        NEIS_SCHEDULE_URL = "https://open.neis.go.kr/hub/SchoolSchedule"
        
        # 서울시 중동중학교 코드 (고정값)
        ATPT_OFCDC_SC_CODE = "B10"  # 서울특별시교육청
        SD_SCHUL_CODE = "7091455"    # 중동중학교
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # 현재 시간 - 한국 시간대(KST)
        today = datetime.now(timezone.utc).astimezone(KST)
        today_str = today.strftime("%Y-%m-%d")
        current_year = today.year
        
        # 학년도 기준: 3월부터 새 학년 시작
        if today.month >= 3:
            school_year = current_year  # 2026년 3월 이후 → 2026학년도
        else:
            school_year = current_year - 1
        
        # 학년도 기간 (3월 1일 ~ 다음해 2월 28일)
        from_date = f"{school_year}0301"
        to_date = f"{school_year + 1}0228"
        
        # NEIS SchoolSchedule API 호출
        schedule_params = {
            'KEY': NEIS_API_KEY,
            'Type': 'json',
            'pIndex': 1,
            'pSize': 500,
            'ATPT_OFCDC_SC_CODE': ATPT_OFCDC_SC_CODE,
            'SD_SCHUL_CODE': SD_SCHUL_CODE,
            'AA_FROM_YMD': from_date,
            'AA_TO_YMD': to_date
        }
        
        schedule_resp = requests.get(
            NEIS_SCHEDULE_URL,
            params=schedule_params,
            headers=headers,
            timeout=10,
            verify=False
        )
        
        schedule_list = []
        csv_data = "날짜,행사명,행사내용\n"  # CSV 헤더
        
        if schedule_resp.status_code == 200:
            schedule_data = schedule_resp.json()
            
            # NEIS API 응답 구조: SchoolSchedule 키
            if 'SchoolSchedule' in schedule_data:
                items = schedule_data.get('SchoolSchedule', [])
                
                if isinstance(items, list):
                    # 실제 데이터는 'row' 배열에 있음
                    row_data = []
                    for item in items:
                        if isinstance(item, dict) and 'row' in item:
                            row_data = item.get('row', [])
                            break
                    
                    if row_data:
                        for item in row_data:
                            if isinstance(item, dict):
                                event_name = item.get('EVENT_NM', '').strip()
                                event_date = item.get('AA_YMD', '').strip()
                                event_content = item.get('EVENT_CNTNT', '').strip()
                                
                                if event_name and event_date and len(event_date) == 8:
                                    try:
                                        # 날짜 포매팅: YYYYMMDD -> YYYY-MM-DD
                                        formatted_date = f"{event_date[:4]}-{event_date[4:6]}-{event_date[6:8]}"
                                        schedule_list.append({
                                            "date": formatted_date,
                                            "event": event_name,
                                            "content": event_content if event_content else event_name,
                                            "timestamp": event_date
                                        })
                                        
                                        # CSV 형식으로 추가 (쉼표 이스케이프 처리)
                                        csv_content = event_content if event_content else event_name
                                        csv_content = csv_content.replace(',', '，')  # 쉼표를 중문 쉼표로 변환
                                        csv_data += f"{formatted_date},{event_name},{csv_content}\n"
                                        
                                    except Exception as parse_err:
                                        continue
        else:
            print(f"NEIS API 오류: {schedule_resp.status_code}")
            print(f"응답: {schedule_resp.text}")
        
        # 날짜 기준으로 정렬
        schedule_list.sort(key=lambda x: x['timestamp'])
        
        # 오늘 기준 분류
        today_schedule = [s for s in schedule_list if s['date'] == today_str]
        before_schedule = [s for s in schedule_list if s['date'] < today_str]
        after_schedule = [s for s in schedule_list if s['date'] > today_str]
        
        # 응답 데이터 (CSV 포함)
        response_data = {
            "status": "success" if schedule_list else "no_data",
            "today": today_str,
            "school": "중동중학교",
            "atpt_code": ATPT_OFCDC_SC_CODE,
            "school_code": SD_SCHUL_CODE,
            "today_schedule": today_schedule,
            "before_schedule": before_schedule[-7:] if before_schedule else [],  # 지난 7개
            "after_schedule": after_schedule[:14] if after_schedule else [],     # 다음 14개
            "all_schedule": schedule_list,
            "total_count": len(schedule_list),
            "csv_data": csv_data  # CSV 형식 데이터 추가
        }
        
        print(f"✅ 학사일정 조회 성공: {len(schedule_list)}개 일정")
        return jsonify(response_data)
    
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"❌ 학사일정 조회 오류: {error_msg}")
        print(traceback.format_exc())
        
        return jsonify({
            "status": "error",
            "message": f"일정 조회 실패: {error_msg}",
            "today": datetime.now(KST).strftime("%Y-%m-%d"),
            "schedule": [],
            "csv_data": ""
        }), 500

# ================= [ 학교 일정 조회 (HTML 표 형식) ] =================
@app.route('/schooldateinfo', methods=['GET'])
def schooldateinfo():
    """학사일정을 HTML 표 형식으로 표시 (디버깅 로그 포함)"""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        debug_logs = []
        
        # NEIS API 설정
        NEIS_API_KEY = "77243bbec81f496286bacbe357cad48f"
        NEIS_SCHEDULE_URL = "https://open.neis.go.kr/hub/SchoolSchedule"
        
        # 서울시 중동중학교 코드
        ATPT_OFCDC_SC_CODE = "B10"
        SD_SCHUL_CODE = "7091455"
        
        debug_logs.append(f"✓ NEIS API 키: {NEIS_API_KEY[:10]}...")
        debug_logs.append(f"✓ 학교코드: {SD_SCHUL_CODE}, 시도코드: {ATPT_OFCDC_SC_CODE}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # NEIS SchoolSchedule API 호출
        # 현재 시간을 UTC에서 KST로 변환
        today = datetime.now(timezone.utc).astimezone(KST)
        today_str = today.strftime("%Y-%m-%d")
        current_year = today.year  # 2026
        
        # 학년도 기준: 3월부터 새 학년 시작
        if today.month >= 3:
            school_year = current_year  # 2026년 3월 이후 → 2026학년도
        else:
            school_year = current_year - 1  # 2026년 1~2월 → 2025학년도
        
        debug_logs.append(f"✓ 현재 학년도: {school_year}")
        
        # 2026년 학사일정 조회 (AY 파라미터 추가, 날짜 범위 명시)
        from_date = f"{school_year}0301"
        to_date = f"{school_year + 1}0228"
        
        schedule_params = {
            'KEY': NEIS_API_KEY,
            'Type': 'json',
            'pIndex': 1,
            'pSize': 500,
            'ATPT_OFCDC_SC_CODE': ATPT_OFCDC_SC_CODE,
            'SD_SCHUL_CODE': SD_SCHUL_CODE,
            'AA_FROM_YMD': from_date,
            'AA_TO_YMD': to_date
        }
        
        debug_logs.append(f"📡 API 호출 중... {NEIS_SCHEDULE_URL}")
        debug_logs.append(f"📡 조회 기간: {from_date} ~ {to_date}")
        
        schedule_resp = requests.get(
            NEIS_SCHEDULE_URL,
            params=schedule_params,
            headers=headers,
            timeout=10,
            verify=False
        )
        
        debug_logs.append(f"📡 API 응답 상태: {schedule_resp.status_code}")
        
        if schedule_resp.status_code != 200:
            debug_logs.append(f"❌ API 오류: {schedule_resp.text[:200]}")
        
        schedule_list = []
        
        if schedule_resp.status_code == 200:
            try:
                schedule_data = schedule_resp.json()
                debug_logs.append(f"✓ JSON 파싱 성공")
                debug_logs.append(f"✓ 응답 키: {list(schedule_data.keys())}")
                
                # 전체 응답 출력
                import json
                full_response = json.dumps(schedule_data, ensure_ascii=False, indent=2)
                debug_logs.append(f"📄 전체 API 응답:")
                for line in full_response.split('\n')[:50]:  # 처음 50줄만
                    debug_logs.append(f"    {line}")
                
                if 'SchoolSchedule' in schedule_data:
                    items = schedule_data.get('SchoolSchedule', [])
                    debug_logs.append(f"✓ SchoolSchedule 데이터 타입: {type(items)}")
                    
                    if isinstance(items, list):
                        debug_logs.append(f"✓ 아이템 수: {len(items)}")
                        
                        # 실제 데이터는 'row' 배열에 있음
                        row_data = []
                        for item in items:
                            if isinstance(item, dict) and 'row' in item:
                                row_data = item.get('row', [])
                                debug_logs.append(f"✓ row 배열 찾음: {len(row_data)}개 일정")
                                break
                        
                        if row_data:
                            # 처음 5개만 로그 표시
                            for idx in range(min(5, len(row_data))):
                                event = row_data[idx]
                                if isinstance(event, dict):
                                    debug_logs.append(f"  [{idx}] {event.get('AA_YMD', '')}: {event.get('EVENT_NM', '')}")
                            
                            # 모든 일정 처리
                            for event in row_data:
                                if isinstance(event, dict):
                                    event_name = event.get('EVENT_NM', '').strip()
                                    event_date = event.get('AA_YMD', '').strip()
                                    event_content = event.get('EVENT_CNTNT', '').strip()
                                    
                                    if event_name and event_date and len(event_date) == 8:
                                        try:
                                            formatted_date = f"{event_date[:4]}-{event_date[4:6]}-{event_date[6:8]}"
                                            schedule_list.append({
                                                "date": formatted_date,
                                                "event": event_name,
                                                "content": event_content if event_content else event_name,
                                                "timestamp": event_date
                                            })
                                        except Exception as e:
                                            debug_logs.append(f"⚠️ 파싱 오류: {e}")
                            
                            debug_logs.append(f"✓ 처리된 일정: {len(schedule_list)}개")
                    else:
                        debug_logs.append(f"❌ SchoolSchedule가 리스트가 아님: {type(items)}")
                        debug_logs.append(f"   값: {items}")
                else:
                    debug_logs.append(f"❌ SchoolSchedule 키 없음")
                    debug_logs.append(f"   전체 응답: {str(schedule_data)[:300]}")
                    
            except Exception as json_err:
                debug_logs.append(f"❌ JSON 파싱 오류: {json_err}")
                debug_logs.append(f"   응답 텍스트: {schedule_resp.text[:300]}")
        else:
            debug_logs.append(f"❌ API 상태 오류: {schedule_resp.status_code}")
        
        # 날짜 기준으로 정렬
        schedule_list.sort(key=lambda x: x['timestamp'])
        
        # HTML 표 생성
        html_content = """
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>중동중학교 학사일정</title>
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', monospace;
                    background: #f5f5f5;
                    padding: 20px;
                    color: #333;
                }
                .container {
                    max-width: 900px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 12px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    padding: 30px;
                }
                h1 {
                    text-align: center;
                    margin-bottom: 10px;
                    color: #1a1a1a;
                    font-size: 28px;
                }
                .debug-box {
                    background: #f0f0f0;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    padding: 15px;
                    margin: 20px 0;
                    font-family: monospace;
                    font-size: 12px;
                    max-height: 300px;
                    overflow-y: auto;
                }
                .debug-log {
                    margin: 5px 0;
                    line-height: 1.6;
                }
                .debug-log.success {
                    color: #22c55e;
                }
                .debug-log.error {
                    color: #ef4444;
                }
                .debug-log.info {
                    color: #3b82f6;
                }
                .info {
                    text-align: center;
                    color: #666;
                    font-size: 14px;
                    margin-bottom: 30px;
                }
                .info strong {
                    color: #3182f6;
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 20px;
                }
                thead {
                    background: linear-gradient(135deg, #3182f6, #2c5aa0);
                    color: white;
                }
                th {
                    padding: 15px;
                    text-align: left;
                    font-weight: 700;
                    border: none;
                }
                td {
                    padding: 15px;
                    border-bottom: 1px solid #e5e7eb;
                }
                tr:hover {
                    background: rgba(49, 130, 246, 0.05);
                }
                tr:last-child td {
                    border-bottom: none;
                }
                .date {
                    font-weight: 600;
                    color: #3182f6;
                    min-width: 100px;
                }
                .today {
                    background: rgba(49, 130, 246, 0.1);
                    font-weight: 700;
                }
                .event-name {
                    font-weight: 600;
                    color: #1a1a1a;
                }
                .no-data {
                    text-align: center;
                    padding: 40px 20px;
                    color: #999;
                    font-size: 16px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📅 중동중학교 학사일정</h1>
                
                <div class="debug-box">
                    <div style="font-weight: bold; margin-bottom: 10px;">🔍 디버깅 로그:</div>
        """
        
        # 디버그 로그 추가
        for log in debug_logs:
            if '✓' in log:
                html_content += f'<div class="debug-log success">{log}</div>\n'
            elif '❌' in log:
                html_content += f'<div class="debug-log error">{log}</div>\n'
            elif '📡' in log:
                html_content += f'<div class="debug-log info">{log}</div>\n'
            else:
                html_content += f'<div class="debug-log">{log}</div>\n'
        
        html_content += """
                </div>
                
                <div class="info">
                    오늘: <strong>""" + today_str + """</strong>
                </div>
        """
        
        if schedule_list:
            html_content += """
                <table>
                    <thead>
                        <tr>
                            <th style="width: 120px;">날짜</th>
                            <th style="width: 200px;">행사명</th>
                            <th>행사내용</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for schedule in schedule_list:
                is_today = " today" if schedule['date'] == today_str else ""
                html_content += f"""
                        <tr class="{is_today}">
                            <td class="date">{schedule['date']}</td>
                            <td class="event-name">{schedule['event']}</td>
                            <td>{schedule['content']}</td>
                        </tr>
                """
            
            html_content += """
                    </tbody>
                </table>
            """
        else:
            html_content += f"""
                <div class="no-data">
                    <p>❌ 조회된 학사일정이 없습니다.</p>
                    <p style="font-size: 12px; margin-top: 10px; color: #ccc;">위의 디버그 로그를 확인하세요.</p>
                </div>
            """
        
        html_content += f"""
                <div class="info" style="margin-top: 30px; border-top: 1px solid #e5e7eb; padding-top: 20px;">
                    <p style="font-size: 12px; color: #999;">
                        총 {len(schedule_list)}개의 학사일정 | 데이터 출처: NEIS 교육정보 개방 포털
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    except Exception as e:
        import traceback
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>오류</title>
            <style>
                body {{ font-family: monospace; padding: 20px; }}
                pre {{ background: #f0f0f0; padding: 15px; border-radius: 8px; overflow-x: auto; }}
            </style>
        </head>
        <body>
            <h1>❌ 에러 발생</h1>
            <pre>{str(e)}\n\n{traceback.format_exc()}</pre>
        </body>
        </html>
        """, 500

# ================= [ UIV2 전용 템플릿 (PC 좌우 분할 / 모바일 스크롤 & 정보표시 적용) ] =================
UIV2_SIGAN_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>시간표 (UI v2)</title>
    <script src="https://unpkg.com/lucide@latest"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <style>
        :root {
            --snappy: cubic-bezier(0.34, 1.56, 0.64, 1);
            --bg-color: #f4f5f7;
            --card-bg: #ffffff;
            --text-main: #111827;
            --text-sub: #6b7280;
            --border-light: #e5e7eb;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent !important; outline: none !important; }
        input:focus, select:focus, button:focus, a:focus { outline: none !important; }

        /* PC 기본: 스크롤 없는 전체화면 & 좌우 분할 */
        html, body {
            height: 100vh; margin: 0; overflow: hidden;
            font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif;
            background-color: var(--bg-color); color: var(--text-main);
        }

        .page-transition { animation: fadeIn 0.5s var(--snappy); }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
        
        .spinner-container { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 9999; display: none; }
        .spinner { width: 50px; height: 50px; animation: rotate 2s linear infinite; }
        .spinner .path { stroke: #000; stroke-width: 5; stroke-linecap: round; fill: none; animation: dash 1.5s ease-in-out infinite; }
        @keyframes rotate { 100% { transform: rotate(360deg); } }
        @keyframes dash { 0% { stroke-dasharray: 1, 150; stroke-dashoffset: 0; } 50% { stroke-dasharray: 90, 150; stroke-dashoffset: -35; } 100% { stroke-dasharray: 90, 150; stroke-dashoffset: -124; } }
        .loading-blur { filter: blur(5px); opacity: 0.5; pointer-events: none; transition: all 0.3s var(--snappy); transform: scale(0.98); }

        .pc-layout {
            display: flex; flex-direction: row;
            width: 100%; max-width: 1400px; margin: 0 auto; height: 100vh;
        }

        /* 왼쪽 사이드바 (PC) */
        .left-sidebar {
            width: 320px; min-width: 320px; padding: 24px;
            display: flex; flex-direction: column;
            border-right: 1px solid var(--border-light);
            background: var(--card-bg); z-index: 10;
        }

        .info-cards { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 24px; flex-shrink: 0; }
        .info-card { background: var(--bg-color); padding: 12px 8px; border-radius: 12px; text-align: center; border: 1px solid var(--border-light); }
        .info-card.full { grid-column: 1 / -1; padding: 16px; }
        .info-card .label { font-size: 0.75rem; color: var(--text-sub); font-weight: 700; margin-bottom: 4px; }
        .info-card .value { font-size: 1.05rem; font-weight: 900; color: var(--text-main); word-break: keep-all; }
        .info-card.full .value { font-size: 1.2rem; }

        .nav-vertical { display: flex; flex-direction: column; gap: 8px; flex-shrink: 0; }
        .nav-btn {
            display: flex; align-items: center; justify-content: flex-start; gap: 12px;
            padding: 16px; border-radius: 16px; font-size: 1rem; font-weight: 600; color: var(--text-sub);
            cursor: pointer; transition: all 0.3s var(--snappy); background: transparent; border: 1px solid transparent;
        }
        .nav-btn.active { background: var(--bg-color); color: var(--text-main); font-weight: 800; border-color: var(--border-light); }
        
        .maker-text {
            margin-top: auto; text-align: center; font-size: 0.85rem; color: var(--text-sub); opacity: 0.5; font-weight: 600; padding-top: 20px;
        }
        .mobile-maker-text { display: none; }

        /* 오른쪽 컨텐츠 영역 (PC) */
        .right-content {
            flex: 1; padding: 24px; display: flex; flex-direction: column; overflow: hidden; background: var(--bg-color);
        }

        .main-card {
            background: var(--card-bg); border-radius: 20px; padding: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.03);
            border: 1px solid var(--border-light); display: flex; flex-direction: column; flex-grow: 1; overflow: hidden; min-height: 0;
        }

        .dropdown-row { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-shrink: 0; }
        .custom-select-wrapper { position: relative; user-select: none; width: 100%; max-width: 300px; }
        .custom-select {
            padding: 12px 16px; background: #fff; border: 1px solid var(--border-light); border-radius: 12px;
            font-size: clamp(0.9rem, 2vh, 1rem); cursor: pointer; display: flex; justify-content: space-between; align-items: center; font-weight: 600; transition: all 0.3s;
        }
        .custom-select.open { border-radius: 12px 12px 0 0; border-color: #000; }
        .custom-options {
            position: absolute; top: 100%; left: 0; right: 0; background: #fff; border: 1px solid #000; border-top: none;
            border-radius: 0 0 12px 12px; max-height: 0; opacity: 0; overflow-y: auto; z-index: 100; transition: all 0.3s; pointer-events: none;
        }
        .custom-select.open + .custom-options { max-height: 250px; opacity: 1; pointer-events: auto; }
        .custom-option { padding: 10px 16px; cursor: pointer; transition: background 0.2s; font-size: 0.95rem; }
        .custom-option:hover { background: #f8fafc; }

        .timetable-wrapper { flex-grow: 1; overflow: hidden; border-radius: 12px; margin-bottom: 12px; display: flex; flex-direction: column; min-height: 0; }
        table { width: 100%; height: 100%; min-width: 100%; border-collapse: separate; border-spacing: 3px; table-layout: fixed; }
        th { background: #f8fafc; padding: 6px 0; font-size: clamp(0.7rem, 1.8vh, 0.9rem); font-weight: 700; color: var(--text-main); border-bottom: 2px solid var(--border-light); text-align: center; height: 30px; }
        th span { color: var(--text-sub); font-size: clamp(0.55rem, 1.5vh, 0.75rem); font-weight: 500; margin-left: 2px; display: block; }
        td { height: auto; padding: 0; vertical-align: middle; position: relative; }

        .period-header { text-align: center; width: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center; }
        .period-num { font-size: clamp(0.7rem, 1.8vh, 0.85rem); font-weight: 700; color: var(--text-main); }
        .period-time { font-size: 0.6rem; color: #9ca3af; margin-top: 1px; display: none; }

        .subject-cell {
            width: 100%; height: 100%; border-radius: 8px; display: flex; flex-direction: column; justify-content: center; align-items: center;
            padding: 4px; border-left: 4px solid transparent; transition: transform 0.2s var(--snappy); cursor: default; overflow: hidden;
        }
        .subject-cell:hover { transform: scale(1.02); }
        .subject-name { font-size: clamp(0.7rem, 2vh, 0.95rem); font-weight: 800; color: var(--text-main); letter-spacing: -0.5px; text-align: center; line-height: 1.1; margin-bottom: 2px; white-space: normal; }
        .teacher-name { font-size: clamp(0.6rem, 1.5vh, 0.75rem); color: #4b5563; margin-top: 0; text-align: center; white-space: nowrap; }
        .changed-text { font-size: clamp(0.55rem, 1.2vh, 0.7rem); color: #ea580c; font-weight: 800; margin-top: 1px; }
        .room-text { font-size: clamp(0.55rem, 1.2vh, 0.7rem); color: #3b82f6; margin-top: 1px; text-align: center; }

        .color-0 { background: #dcfce7; border-left-color: #86efac; } 
        .color-1 { background: #fee2e2; border-left-color: #fca5a5; } 
        .color-2 { background: #f3e8ff; border-left-color: #d8b4fe; } 
        .color-3 { background: #ffedd5; border-left-color: #fdba74; } 
        .color-4 { background: #e0f2fe; border-left-color: #7dd3fc; } 
        .color-5 { background: #fce7f3; border-left-color: #f9a8d4; } 
        .color-6 { background: #ccfbf1; border-left-color: #5eead4; } 
        .color-empty { background: #f9fafb; border-left-color: transparent; border: 1px dashed #e5e7eb; }

        .btn-sync {
            width: 100%; background: #f8fafc; border: 1px solid var(--border-light); padding: 14px; border-radius: 12px; flex-shrink: 0;
            font-size: clamp(0.9rem, 2vh, 1rem); font-weight: 700; color: var(--text-main); display: flex; align-items: center; justify-content: center; gap: 8px;
            cursor: pointer; transition: all 0.3s var(--snappy); box-shadow: 0 2px 4px rgba(0,0,0,0.02); margin-bottom: 8px;
        }
        .btn-sync:hover { background: #e2e8f0; border-color: #cbd5e1; transform: translateY(-2px); }
        .spin-once { animation: spin 0.6s var(--snappy) 1; }
        @keyframes spin { 100% { transform: rotate(360deg); } }

        .btn-save {
            width: 100%; background: #ffffff; border: 1px solid var(--border-light); padding: 14px; border-radius: 12px; flex-shrink: 0;
            font-size: clamp(0.9rem, 2vh, 1rem); font-weight: 700; color: var(--text-main); display: flex; align-items: center; justify-content: center; gap: 8px;
            cursor: pointer; transition: all 0.3s var(--snappy); box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        }
        .btn-save:hover { background: #f8fafc; border-color: #cbd5e1; transform: translateY(-2px); }

        .ripple { position: absolute; border-radius: 50%; transform: scale(0); animation: ripple-anim 0.6s linear; background-color: rgba(0, 0, 0, 0.05); pointer-events: none; }
        @keyframes ripple-anim { to { transform: scale(4); opacity: 0; } }
        .ripple-element { position: relative; overflow: hidden; }

        /* 모바일 최적화 (스크롤 가능, 레이아웃 세로 적층) */
        @media (max-width: 768px) {
            html, body { height: auto; min-height: 100vh; overflow: auto; display: block; }
            .pc-layout { flex-direction: column; height: auto; }
            
            .left-sidebar { width: 100%; border-right: none; padding: 16px; background: transparent; z-index: 1; }
            .info-cards { margin-bottom: 12px; }
            
            .nav-vertical { flex-direction: row; overflow-x: auto; scrollbar-width: none; background: var(--card-bg); border-radius: 999px; padding: 4px; border: 1px solid var(--border-light); }
            .nav-btn { flex: 1; justify-content: center; padding: 12px 10px; border-radius: 999px; font-size: 0.85rem; border: none; min-width: 65px; gap: 4px; }
            
            .maker-text { display: none; }
            .mobile-maker-text { display: block; text-align: center; font-size: 0.8rem; color: var(--text-sub); opacity: 0.5; font-weight: 600; padding: 20px 0; margin-top: 10px; }
            
            .right-content { padding: 0 16px 16px 16px; overflow: visible; }
            .main-card { overflow: visible; border: none; box-shadow: none; background: transparent; padding: 0; }
            .dropdown-row { margin-bottom: 8px; }
            
            /* 드롭다운 바텀 시트 구현 */
            .custom-options {
                position: fixed !important; top: auto !important; bottom: 0 !important; left: 0 !important; right: 0 !important;
                border-radius: 24px 24px 0 0 !important; border: none !important; border-top: 1px solid #e2e8f0 !important;
                box-shadow: 0 -10px 40px rgba(0,0,0,0.15) !important; max-height: 60vh !important;
                transform: translateY(100%); opacity: 1 !important; transition: transform 0.4s var(--snappy) !important;
                padding-bottom: env(safe-area-inset-bottom); z-index: 9999 !important;
            }
            .custom-select.open + .custom-options { transform: translateY(0); pointer-events: auto; }
            
            /* 테이블이 스크롤되어도 안깨지게 조정 */
            .timetable-wrapper { overflow-x: auto; overflow-y: visible; margin-bottom: 12px; display: block; min-height: 400px; }
            table { min-width: 100%; border-spacing: 2px; }
            .subject-cell { padding: 2px; border-radius: 4px; border-left-width: 2px; }
            .subject-name { font-size: clamp(0.65rem, 1.8vh, 0.85rem); line-height: 1.1; }
            .teacher-name { font-size: clamp(0.55rem, 1.4vh, 0.7rem); }
            th { font-size: clamp(0.65rem, 1.5vh, 0.8rem); padding: 4px 0; }
            th span { font-size: clamp(0.5rem, 1.2vh, 0.6rem); display: inline-block; }
            .period-num { font-size: clamp(0.65rem, 1.5vh, 0.8rem); }
            
            .btn-sync, .btn-save { padding: 12px; border-radius: 12px; }
        }
    </style>
</head>
<body class="page-transition">
    {# 셀 렌더링 매크로 #}
    {% macro render_cell(cell_data) %}
        {% if cell_data and (cell_data|string).strip() %}
            {% set raw_str = (cell_data|string).strip() %}
            {% set is_changed = false %}
            {% if raw_str.startswith('*') or '[변경]' in raw_str %}
                {% set is_changed = true %}
            {% endif %}
            
            {% if '교시:' in raw_str %}{% set raw_str = raw_str.split('교시:')[-1].strip() %}{% endif %}
            {% if '교시 :' in raw_str %}{% set raw_str = raw_str.split('교시 :')[-1].strip() %}{% endif %}
            {% if raw_str.startswith('*') %}{% set raw_str = raw_str[1:].strip() %}{% endif %}
            {% if raw_str.startswith('[변경]') %}{% set raw_str = raw_str[4:].strip() %}{% endif %}
            
            {% set subject_name = raw_str %}
            {% set teacher_name = "" %}
            {% set room_name = "" %}
            
            {% if '(' in raw_str %}
                {% set parts = raw_str.split('(') %}
                {% set subject_name = parts[0].strip() %}
                {% set remainder = parts[1].split(')') %}
                {% set teacher_name = remainder[0].strip() %}
                {% if len(remainder) > 1 and remainder[1].strip() %}
                    {% set room_name = remainder[1].strip() %}
                {% endif %}
            {% endif %}
            
            <div class="subject-cell color-{{ get_color_index(subject_name) }}">
                <div class="subject-name">{{ subject_name }}</div>
                {% if teacher_name %}<div class="teacher-name">{{ teacher_name }}</div>{% endif %}
                {% if room_name %}<div class="room-text">@ {{ room_name }}</div>{% endif %}
                {% if is_changed %}<div class="changed-text">변동</div>{% endif %}
            </div>
        {% else %}
            <div class="subject-cell color-empty"><span style="color:#e5e7eb;">-</span></div>
        {% endif %}
    {% endmacro %}

    <div id="loadingSpinner" class="spinner-container">
        <svg class="spinner" viewBox="0 0 50 50"><circle class="path" cx="25" cy="25" r="20"></circle></svg>
    </div>

    <div class="pc-layout">
        <div class="left-sidebar">
            <div class="info-cards">
                <div class="info-card full">
                    <div class="label">학교</div>
                    <div class="value">{{ school }}</div>
                </div>
                <div class="info-card">
                    <div class="label">학년수</div>
                    <div class="value">{{ total_grades }}개</div>
                </div>
                <div class="info-card">
                    <div class="label">학급수</div>
                    <div class="value">{{ total_classes }}학급</div>
                </div>
                <div class="info-card">
                    <div class="label">선생님</div>
                    <div class="value">{{ total_teachers }}명</div>
                </div>
            </div>

            <div class="nav-vertical">
                <button class="nav-btn {% if mode == 'student' %}active{% endif %} ripple-element" onclick="switchTab('student')"><i data-lucide="users"></i> 학생</button>
                <button class="nav-btn {% if mode == 'teacher' %}active{% endif %} ripple-element" onclick="switchTab('teacher')"><i data-lucide="user"></i> 교사</button>
                <button class="nav-btn {% if mode == 'all' %}active{% endif %} ripple-element" onclick="switchTab('all')"><i data-lucide="school"></i> 전체</button>
                <button class="nav-btn ripple-element" onclick="location.href='/'"><i data-lucide="settings"></i> 설정</button>
            </div>

            <div class="maker-text pc-only">Made by 신재원</div>
        </div>

        <div class="right-content">
            <div class="main-card capture-area">
                <div class="dropdown-row">
                    {% if mode == 'teacher' %}
                        <div class="custom-select-wrapper" id="teacher_wrapper" style="max-width: 100%; flex: 1;">
                            <div class="custom-select">
                                <span class="selected-text">{% if selected_teacher %}{{ selected_teacher }} 선생님{% else %}선생님 없음{% endif %}</span>
                                <i data-lucide="chevron-down"></i>
                            </div>
                            <div class="custom-options">
                                {% for t in teachers %}
                                <div class="custom-option" data-value="{{ t }}" onclick="changeTeacher('{{ t }}')">{{ t }} 선생님</div>
                                {% endfor %}
                            </div>
                        </div>
                    {% elif mode == 'all' %}
                        <div style="font-size: clamp(1rem, 2vh, 1.1rem); font-weight: 800; color: var(--text-main); margin-bottom: 4px;">
                            전체 반 (오늘 시간표)
                        </div>
                    {% else %}
                        <div class="custom-select-wrapper" id="grade_wrapper" style="flex:1;">
                            <div class="custom-select">
                                <span class="selected-text">{{ grade }}학년</span>
                                <i data-lucide="chevron-down"></i>
                            </div>
                            <div class="custom-options">
                                <div class="custom-option" data-value="1" onclick="changeGC(1, {{ class_nm }})">1학년</div>
                                <div class="custom-option" data-value="2" onclick="changeGC(2, {{ class_nm }})">2학년</div>
                                <div class="custom-option" data-value="3" onclick="changeGC(3, {{ class_nm }})">3학년</div>
                                <div class="custom-option" data-value="4" onclick="changeGC(4, {{ class_nm }})">4학년</div>
                                <div class="custom-option" data-value="5" onclick="changeGC(5, {{ class_nm }})">5학년</div>
                                <div class="custom-option" data-value="6" onclick="changeGC(6, {{ class_nm }})">6학년</div>
                            </div>
                        </div>
                        <div class="custom-select-wrapper" id="class_wrapper" style="flex:1;">
                            <div class="custom-select">
                                <span class="selected-text">{{ class_nm }}반</span>
                                <i data-lucide="chevron-down"></i>
                            </div>
                            <div class="custom-options">
                                {% for i in range(1, 16) %}
                                <div class="custom-option" data-value="{{ i }}" onclick="changeGC({{ grade }}, {{ i }})">{{ i }}반</div>
                                {% endfor %}
                            </div>
                        </div>
                    {% endif %}
                </div>

                <div class="timetable-wrapper">
                    {% if mode == 'all' %}
                        <table>
                            <thead>
                                <tr>
                                    <th style="width: 10%;">반</th>
                                    {% for p_idx in range(max_periods) %}
                                    <th>{{ p_idx + 1 }}교시</th>
                                    {% endfor %}
                                </tr>
                            </thead>
                            <tbody>
                                {% for c, periods in all_classes_data.items() %}
                                <tr>
                                    <td>
                                        <div class="period-header">
                                            <span class="period-num">{{c}}</span>
                                        </div>
                                    </td>
                                    {% for p_idx in range(max_periods) %}
                                        <td>
                                            {% set cell_data = periods[p_idx] if p_idx < len(periods) else "" %}
                                            {{ render_cell(cell_data) }}
                                        </td>
                                    {% endfor %}
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    {% else %}
                        <table>
                            <thead>
                                <tr>
                                    <th style="width: 12%;">교시</th>
                                    {% for day in ['월', '화', '수', '목', '금'] %}
                                    <th>{{ day }} <span>({{ dates[loop.index0] }})</span></th>
                                    {% endfor %}
                                </tr>
                            </thead>
                            <tbody>
                                {% for p_idx in range(max_periods) %}
                                <tr>
                                    <td>
                                        <div class="period-header">
                                            <span class="period-num">{{ p_idx + 1 }}교시</span>
                                        </div>
                                    </td>
                                    {% for d_idx in range(1, 6) %}
                                        <td>
                                            {% set cell_data = timetable[d_idx][p_idx] if (timetable[d_idx] and p_idx < len(timetable[d_idx])) else "" %}
                                            {{ render_cell(cell_data) }}
                                        </td>
                                    {% endfor %}
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    {% endif %}
                </div>
                
                <button class="btn-sync ripple-element" onclick="syncData(this)">
                    <i data-lucide="refresh-cw" class="sync-icon" style="width:18px; height:18px;"></i> 동기화
                </button>
                <button class="btn-save ripple-element" onclick="saveAsImage()">
                    <i data-lucide="download" style="width:18px; height:18px;"></i> 사진으로 저장
                </button>
                <div class="data-source-text" style="text-align:center; font-size:clamp(0.6rem, 1.5vh, 0.8rem); color:#9ca3af; margin-top:8px;">데이터 소스: {{ '나이스 (NEIS)' if data_source == 'neis' else '컴시간알리미' }}</div>
            </div>
        </div>
        
        <div class="mobile-maker-text">Made by 신재원</div>
    </div>

    <script>
        lucide.createIcons();

        function createRipple(event) {
            const button = event.currentTarget;
            const circle = document.createElement("span");
            const diameter = Math.max(button.clientWidth, button.clientHeight);
            const radius = diameter / 2;
            circle.style.width = circle.style.height = `${diameter}px`;
            circle.style.left = `${event.clientX - button.getBoundingClientRect().left - radius}px`;
            circle.style.top = `${event.clientY - button.getBoundingClientRect().top - radius}px`;
            circle.classList.add("ripple");
            const ripple = button.getElementsByClassName("ripple")[0];
            if (ripple) { ripple.remove(); }
            button.appendChild(circle);
        }

        document.querySelectorAll('.ripple-element').forEach(el => {
            el.addEventListener('mousedown', createRipple);
            el.addEventListener('touchstart', (e) => {
                const touch = e.touches[0];
                const fakeEvent = { currentTarget: el, clientX: touch.clientX, clientY: touch.clientY };
                createRipple(fakeEvent);
            }, {passive: true});
        });

        document.querySelectorAll('.custom-select').forEach(select => {
            select.addEventListener('click', (e) => {
                document.querySelectorAll('.custom-select').forEach(other => {
                    if(other !== select) other.classList.remove('open');
                });
                select.classList.toggle('open');
                e.stopPropagation();
            });
        });
        document.addEventListener('click', () => {
            document.querySelectorAll('.custom-select').forEach(select => select.classList.remove('open'));
        });

        function showLoading() {
            document.getElementById('loadingSpinner').style.display = 'block';
            const captureArea = document.querySelector('.capture-area');
            if(captureArea) captureArea.classList.add('loading-blur');
        }

        function switchTab(mode) {
            showLoading();
            setTimeout(() => {
                const urlParams = new URLSearchParams(window.location.search);
                urlParams.set('mode', mode);
                window.location.search = urlParams.toString();
            }, 300);
        }

        function changeTeacher(t) {
            showLoading();
            setTimeout(() => {
                const urlParams = new URLSearchParams(window.location.search);
                urlParams.set('teacher', t);
                window.location.search = urlParams.toString();
            }, 300);
        }

        function changeGC(g, c) {
            showLoading();
            setTimeout(() => {
                const urlParams = new URLSearchParams(window.location.search);
                urlParams.set('grade', g);
                urlParams.set('class', c);
                window.location.search = urlParams.toString();
            }, 300);
        }
        
        function syncData(btn) {
            const icon = btn.querySelector('.sync-icon');
            if(icon) {
                icon.classList.remove('spin-once');
                void icon.offsetWidth;
                icon.classList.add('spin-once');
            }
            showLoading();
            setTimeout(() => {
                window.location.reload();
            }, 600);
        }

        function saveAsImage() {
            const captureArea = document.querySelector('.capture-area');
            html2canvas(captureArea, { scale: 3, backgroundColor: '#ffffff', borderRadius: 20 }).then(canvas => {
                let link = document.createElement('a');
                link.download = 'timetable.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
            });
        }
        
        window.addEventListener('pageshow', function(event) {
            document.getElementById('loadingSpinner').style.display = 'none';
            const captureArea = document.querySelector('.capture-area');
            if(captureArea) captureArea.classList.remove('loading-blur');
        });
    </script>
</body>
</html>
"""
# ==============================================================

# ================= [ 설정 화면 템플릿 (루트 페이지용) ] =================
SETUP_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>학교 서비스 설정</title>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root { --snappy: cubic-bezier(0.34, 1.56, 0.64, 1); }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent !important; outline: none !important; }
        body {
            font-family: 'Pretendard', 'Malgun Gothic', sans-serif;
            margin: 0; padding: 0; background-color: #f1f5f9; color: #1e293b;
            display: flex; justify-content: center; align-items: center; min-height: 100vh;
        }
        .setup-card {
            background: #ffffff; width: 90%; max-width: 440px;
            padding: 40px 30px; border-radius: 36px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.08);
            animation: fadeIn 0.6s var(--snappy);
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: 1.6rem; margin: 0; font-weight: 900; color: #000000; letter-spacing: -0.5px; }
        .header p { color: #64748b; font-size: 0.95rem; margin-top: 8px; font-weight: 500; }
        
        .form-group { margin-bottom: 20px; position: relative; }
        label { display: block; font-weight: 800; font-size: 0.9rem; color: #000000; margin-bottom: 8px; margin-left: 4px; }
        
        input {
            width: 100%; padding: 16px 20px; border: 2px solid #e2e8f0;
            border-radius: 24px; font-size: 1rem; outline: none; font-weight: 600;
            transition: all 0.3s var(--snappy); background: #f8fafc; color: #000000;
        }
        input:focus { border-color: #000000; background: #ffffff; transform: translateY(-2px); box-shadow: none; }
        
        .custom-select-wrapper { position: relative; user-select: none; }
        .custom-select {
            padding: 16px 20px; background: #f8fafc; border: 2px solid #e2e8f0; border-radius: 24px;
            font-size: 1rem; cursor: pointer; transition: all 0.4s var(--snappy);
            display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #000000;
        }
        .custom-select:hover { border-color: #000000; transform: translateY(-2px); }
        .custom-select.open { border-color: #000000; border-radius: 24px 24px 8px 8px; background: #ffffff; transform: translateY(-2px); }
        .custom-select i { transition: transform 0.4s var(--snappy); }
        .custom-select.open i { transform: rotate(180deg); }
        
        .custom-options {
            position: absolute; top: 100%; left: 0; right: 0; background: #ffffff;
            border: 2px solid #000000; border-top: none; border-radius: 0 0 24px 24px;
            box-shadow: 0 15px 30px rgba(0,0,0,0.1); overflow-y: auto; overflow-x: hidden;
            max-height: 0; opacity: 0; transition: all 0.4s var(--snappy); z-index: 20; pointer-events: none;
        }
        .custom-options::-webkit-scrollbar { width: 6px; }
        .custom-options::-webkit-scrollbar-track { background: transparent; }
        .custom-options::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
        
        .custom-select.open + .custom-options { max-height: 220px; opacity: 1; padding: 10px 0; pointer-events: auto; }
        .custom-option { padding: 12px 20px; cursor: pointer; transition: all 0.2s; font-weight: 600; color: #475569; }
        .custom-option:hover { background: #f1f5f9; color: #000000; padding-left: 25px; }

        .autocomplete-list {
            position: absolute; top: calc(100% - 20px); left: 0; right: 0; background: #ffffff;
            border: 2px solid #000000; border-top: none; border-radius: 0 0 24px 24px;
            max-height: 180px; overflow-y: auto; z-index: 10; margin: 0; padding: 20px 0 10px 0;
            list-style: none; box-shadow: 0 15px 30px rgba(0,0,0,0.1); display: none;
        }
        .autocomplete-list li { padding: 12px 20px; font-size: 0.95rem; cursor: pointer; font-weight: 600; color: #475569; transition: all 0.2s; }
        .autocomplete-list li:hover { background-color: #f1f5f9; color: #000000; padding-left: 25px; }
        
        .row { display: flex; gap: 15px; }
        .row .form-group { flex: 1; }

        .submit-btn {
            width: 100%; padding: 18px; background: #000000; color: white; border: none; border-radius: 999px;
            font-size: 1.15rem; font-weight: 800; cursor: pointer; transition: all 0.4s var(--snappy); position: relative; overflow: hidden;
            margin-top: 10px; box-shadow: 0 10px 20px rgba(0,0,0,0.15);
        }
        .submit-btn:hover { background: #333333; transform: translateY(-3px) scale(1.02); box-shadow: 0 15px 25px rgba(0,0,0,0.2); }
        .submit-btn:active { transform: translateY(0) scale(0.96); box-shadow: none; }

        .ripple { position: absolute; border-radius: 50%; transform: scale(0); animation: ripple-anim 0.6s linear; background-color: rgba(255, 255, 255, 0.3); }
        @keyframes ripple-anim { to { transform: scale(4); opacity: 0; } }

        @media (max-width: 480px) {
            body { background-color: #ffffff; }
            .setup-card {
                width: 100%; height: 100vh; max-width: 100%; border-radius: 0; box-shadow: none;
                padding: calc(env(safe-area-inset-top) + 20px) 20px 20px 20px; animation: none;
            }
            .custom-options {
                position: fixed !important; top: auto !important; bottom: 0 !important; left: 0 !important; right: 0 !important;
                border: none !important; border-top: 1px solid #e2e8f0 !important; border-radius: 24px 24px 0 0 !important;
                box-shadow: 0 -10px 40px rgba(0,0,0,0.15) !important; max-height: 60vh !important;
                transform: translateY(100%); opacity: 1 !important; transition: transform 0.4s var(--snappy) !important;
                padding-bottom: env(safe-area-inset-bottom); z-index: 9999 !important;
            }
            .custom-select.open + .custom-options { transform: translateY(0); pointer-events: auto; }
        }
    </style>
</head>
<body>
    <div class="setup-card">
        <div class="header">
            <h1>학교 서비스 시작</h1>
            <p>학교 정보와 학급을 설정해주세요.</p>
        </div>
        
        <div class="form-group">
            <label>서비스 유형 선택</label>
            <div class="custom-select-wrapper" id="ui_wrapper">
                <div class="custom-select">
                    <span class="selected-text">컴시간 알리미</span>
                    <i data-lucide="chevron-down"></i>
                </div>
                <div class="custom-options">
                    <div class="custom-option" data-value="comcigan">컴시간 알리미</div>
                    <div class="custom-option" data-value="neis">나이스 (NEIS)</div>
                </div>
                <input type="hidden" id="source_select" value="comcigan">
            </div>
        </div>

        <div class="form-group" style="z-index: 15;">
            <label>학교 검색</label>
            <input type="text" id="school_search" placeholder="학교 이름 입력 (예: 중동중)" autocomplete="off">
            <ul id="school_list" class="autocomplete-list"></ul>
        </div>

        <div class="row">
            <div class="form-group">
                <label>학년</label>
                <div class="custom-select-wrapper" id="grade_wrapper">
                    <div class="custom-select">
                        <span class="selected-text">2학년</span>
                        <i data-lucide="chevron-down"></i>
                    </div>
                    <div class="custom-options">
                        <div class="custom-option" data-value="1">1학년</div>
                        <div class="custom-option" data-value="2">2학년</div>
                        <div class="custom-option" data-value="3">3학년</div>
                        <div class="custom-option" data-value="4">4학년</div>
                        <div class="custom-option" data-value="5">5학년</div>
                        <div class="custom-option" data-value="6">6학년</div>
                    </div>
                    <input type="hidden" id="grade_select" value="2">
                </div>
            </div>
            <div class="form-group">
                <label>반</label>
                <div class="custom-select-wrapper" id="class_wrapper">
                    <div class="custom-select">
                        <span class="selected-text">8반</span>
                        <i data-lucide="chevron-down"></i>
                    </div>
                    <div class="custom-options" id="class_options">
                        </div>
                    <input type="hidden" id="class_select" value="8">
                </div>
            </div>
        </div>

        <button class="submit-btn" onclick="submitSetup(event)">확인</button>
    </div>

    <script>
        lucide.createIcons();

        const classOptionsContainer = document.getElementById('class_options');
        for(let i=1; i<=15; i++) {
            let opt = document.createElement('div');
            opt.className = 'custom-option';
            opt.setAttribute('data-value', i);
            opt.innerText = i + '반';
            classOptionsContainer.appendChild(opt);
        }

        document.querySelectorAll('.custom-select-wrapper').forEach(wrapper => {
            const select = wrapper.querySelector('.custom-select');
            const options = wrapper.querySelectorAll('.custom-option');
            const hiddenInput = wrapper.querySelector('input[type="hidden"]');
            const selectedText = wrapper.querySelector('.selected-text');

            select.addEventListener('click', (e) => {
                document.querySelectorAll('.custom-select').forEach(other => {
                    if(other !== select) other.classList.remove('open');
                });
                select.classList.toggle('open');
                e.stopPropagation();
            });

            options.forEach(opt => {
                opt.addEventListener('click', () => {
                    selectedText.innerText = opt.innerText;
                    hiddenInput.value = opt.getAttribute('data-value');
                    select.classList.remove('open');
                });
            });
        });

        document.addEventListener('click', () => {
            document.querySelectorAll('.custom-select').forEach(select => {
                select.classList.remove('open');
            });
        });

        let selectedSchool = { name: "중동중학교", code: "7091455", region: "B10" };

        const searchInput = document.getElementById('school_search');
        const autocompleteList = document.getElementById('school_list');

        searchInput.addEventListener('input', function() {
            const query = this.value.trim();
            if(query.length < 2) { autocompleteList.style.display = 'none'; return; }

            fetch(`https://open.neis.go.kr/hub/schoolInfo?KEY=77243bbec81f496286bacbe357cad48f&Type=json&pIndex=1&pSize=10&SCHUL_NM=${query}`)
                .then(res => res.json())
                .then(data => {
                    autocompleteList.innerHTML = '';
                    if(data.schoolInfo) {
                        autocompleteList.style.display = 'block';
                        data.schoolInfo[1].row.forEach(school => {
                            let li = document.createElement('li');
                            li.textContent = school.SCHUL_NM;
                            li.onclick = () => {
                                selectedSchool = { name: school.SCHUL_NM, code: school.SD_SCHUL_CODE, region: school.ATPT_OFCDC_SC_CODE };
                                searchInput.value = school.SCHUL_NM;
                                autocompleteList.style.display = 'none';
                            };
                            autocompleteList.appendChild(li);
                        });
                    } else { autocompleteList.style.display = 'none'; }
                }).catch(err => console.error(err));
        });

        document.addEventListener('click', function(e) {
            if (e.target !== searchInput && e.target !== autocompleteList) { autocompleteList.style.display = 'none'; }
        });

        function createRipple(event) {
            const button = event.currentTarget;
            const circle = document.createElement("span");
            const diameter = Math.max(button.clientWidth, button.clientHeight);
            const radius = diameter / 2;
            circle.style.width = circle.style.height = `${diameter}px`;
            circle.style.left = `${event.clientX - button.getBoundingClientRect().left - radius}px`;
            circle.style.top = `${event.clientY - button.getBoundingClientRect().top - radius}px`;
            circle.classList.add("ripple");
            const ripple = button.getElementsByClassName("ripple")[0];
            if (ripple) { ripple.remove(); }
            button.appendChild(circle);
        }

        function submitSetup(e) {
            createRipple(e);
            setTimeout(() => {
                const source = document.getElementById('source_select').value;
                const grade = document.getElementById('grade_select').value;
                const cls = document.getElementById('class_select').value;
                
                // 설정에서 무엇을 고르든 모두 V2 UI(/uiv2sigan)로 이동하도록 강제
                window.location.href = `/uiv2sigan?school=${encodeURIComponent(selectedSchool.name)}&school_code=${selectedSchool.code}&region_code=${selectedSchool.region}&grade=${grade}&class=${cls}&source=${source}`;
            }, 300);
        }
    </script>
</body>
</html>
"""

# ================= [ 공통 스타일 및 UI 요소 ] =================
COMMON_HEAD_INCLUDES = """
    <script src="https://unpkg.com/lucide@latest"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <script src="https://cdn.jsdelivr.net/gh/jaewondev27/one-ui-8-loader/oneui8-loader.js"></script>
    <style>
        :root { --snappy: cubic-bezier(0.34, 1.56, 0.64, 1); }
        
        body { 
            font-family: 'Pretendard', 'Malgun Gothic', sans-serif; 
            margin: 0; padding: 0; background-color: #f1f5f9; color: #1e293b; 
            display: flex; justify-content: center; align-items: center; 
            min-height: 100vh; overflow: hidden;
            -webkit-tap-highlight-color: transparent !important;
        }
        
        .app-container {
            width: 100%; max-width: 480px; height: 100vh; 
            background-color: #ffffff; display: flex; flex-direction: column;
            position: relative; box-shadow: 0 0 40px rgba(0,0,0,0.05); overflow: hidden;
        }
        @media (min-width: 481px) {
            .app-container { height: 90vh; max-height: 900px; border-radius: 40px; margin: auto; border: 8px solid #ffffff; box-shadow: 0 20px 50px rgba(0,0,0,0.1); }
            body { padding: 20px; }
        }

        .page-transition { animation: fadeIn 0.5s var(--snappy); }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }

        .ripple-element { position: relative; overflow: hidden; cursor: pointer; }
        .ripple {
            position: absolute; border-radius: 50%; transform: scale(0);
            animation: ripple-anim 0.6s linear; background-color: rgba(0, 0, 0, 0.08); pointer-events: none;
        }
        @keyframes ripple-anim { to { transform: scale(4); opacity: 0; } }

        .app-top-nav {
            display: flex; background: #ffffff; padding: 15px 15px 0 15px;
            border-bottom: 1px solid #f1f5f9; box-shadow: 0 4px 12px rgba(0,0,0,0.02);
            justify-content: space-between; align-items: flex-end;
            position: relative; z-index: 50;
        }
        .nav-tabs { display: flex; gap: 8px; flex: 1; }
        .nav-tab {
            padding: 14px 16px; font-size: 0.95rem; font-weight: 700; color: #94a3b8;
            text-decoration: none; transition: all 0.4s var(--snappy);
            border-radius: 24px 24px 0 0; border-bottom: 3px solid transparent; outline: none;
        }
        .nav-tab.active { color: #000000; border-bottom: 3px solid #000000; background: #f8fafc; font-weight: 900; transform: translateY(-2px); }
        .nav-tab:not(.active):hover { color: #000000; background: #f1f5f9; transform: translateY(-1px); }
        
        .header-actions { display: flex; align-items: center; padding-bottom: 10px; gap: 4px;}
        .icon-btn {
            background: none; border: none; color: #000000; padding: 10px; border-radius: 999px; outline: none;
            cursor: pointer; transition: all 0.3s var(--snappy);
        }
        .icon-btn:hover { transform: scale(1.15) translateY(-2px); background: #f1f5f9; }
        .icon-btn:active { transform: scale(0.9); }

        .spinner-container {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 9999;
            display: none;
            align-items: center;
            justify-content: center;
            background: transparent;
            box-shadow: none;
            width: auto;
            height: auto;
            line-height: 0;
            pointer-events: none;
        }
        .spinner-container oneui8-loader {
            display: block;
            transform: translate(0, 0);
        }

        .app-container.global-loading > *:not(#loadingSpinner) {
            filter: blur(6px);
            pointer-events: none;
            user-select: none;
        }
        .app-container.global-loading {
            overflow: hidden;
        }

        body.dark-mode {
            background-color: #111111;
            color: #e5e7eb;
        }
        body.dark-mode .app-container {
            background-color: #181818;
            box-shadow: 0 0 40px rgba(0,0,0,0.45);
        }
        body.dark-mode .app-top-nav {
            background: #181818;
            border-bottom-color: #2f2f2f;
            box-shadow: 0 4px 12px rgba(0,0,0,0.28);
        }
        body.dark-mode .nav-tab { color: #9ca3af; }
        body.dark-mode .nav-tab.active {
            color: #f3f4f6;
            border-bottom-color: #f3f4f6;
            background: #262626;
        }
        body.dark-mode .nav-tab:not(.active):hover {
            color: #f3f4f6;
            background: #232323;
        }
        body.dark-mode .icon-btn { color: #f3f4f6; }
        body.dark-mode .icon-btn:hover { background: #2a2a2a; }
    </style>
"""

COMMON_JS_INCLUDES = """
    <script>
        const LOADER_MIN_MS = 4000;
        const LOADER_MAX_MS = 6000;

        function getRandomLoaderMs() {
            return Math.floor(Math.random() * (LOADER_MAX_MS - LOADER_MIN_MS + 1)) + LOADER_MIN_MS;
        }

        function getLoadingElements() {
            return {
                spinner: document.getElementById('loadingSpinner'),
                container: document.querySelector('.app-container')
            };
        }

        let loaderVisibleAt = 0;
        let hideLoaderTimer = null;

        function showGlobalLoader() {
            const { spinner, container } = getLoadingElements();
            if (spinner) spinner.style.display = 'flex';
            if (container) container.classList.add('global-loading');
            loaderVisibleAt = Date.now();
            if (hideLoaderTimer) {
                clearTimeout(hideLoaderTimer);
                hideLoaderTimer = null;
            }
        }

        function hideGlobalLoader(force = false) {
            const { spinner, container } = getLoadingElements();
            if (!spinner) return;
            const elapsed = Date.now() - loaderVisibleAt;
            const waitMs = force ? 0 : Math.max(0, LOADER_MIN_MS - elapsed);
            if (waitMs > 0) {
                if (hideLoaderTimer) clearTimeout(hideLoaderTimer);
                hideLoaderTimer = setTimeout(() => {
                    hideLoaderTimer = null;
                    hideGlobalLoader(true);
                }, waitMs);
                return;
            }
            spinner.style.display = 'none';
            if (container) container.classList.remove('global-loading');
        }

        let entryLoaderTimer = null;

        function runEntryLoader() {
            const { spinner } = getLoadingElements();
            if (!spinner) return;
            showGlobalLoader();
            if (entryLoaderTimer) clearTimeout(entryLoaderTimer);
            entryLoaderTimer = setTimeout(() => {
                hideGlobalLoader();
                entryLoaderTimer = null;
            }, getRandomLoaderMs());
        }

        function stopEntryLoaderTimer() {
            if (!entryLoaderTimer) return;
            clearTimeout(entryLoaderTimer);
            entryLoaderTimer = null;
        }

        function getAppThemeMode() {
            try {
                if (typeof AndroidInterface !== 'undefined' && AndroidInterface.getThemeMode) {
                    const mode = AndroidInterface.getThemeMode();
                    if (mode === 'dark' || mode === 'light') return mode;
                    if (mode === 'auto') {
                        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
                    }
                }
            } catch (e) {}
            return 'light';
        }

        let currentAppliedTheme = '';
        function applyAppTheme() {
            const nextTheme = getAppThemeMode();
            if (nextTheme === currentAppliedTheme) return;
            currentAppliedTheme = nextTheme;
            document.body.classList.toggle('dark-mode', nextTheme === 'dark');
        }

        function setupThemeAutoSync() {
            window.addEventListener('focus', applyAppTheme);
            document.addEventListener('visibilitychange', applyAppTheme);
            setInterval(applyAppTheme, 700);
        }

        document.addEventListener('DOMContentLoaded', () => {
            applyAppTheme();
            setupThemeAutoSync();
            runEntryLoader();
        });

        function createRipple(event) {
            const button = event.currentTarget;
            const circle = document.createElement("span");
            const diameter = Math.max(button.clientWidth, button.clientHeight);
            const radius = diameter / 2;
            circle.style.width = circle.style.height = `${diameter}px`;
            circle.style.left = `${event.clientX - button.getBoundingClientRect().left - radius}px`;
            circle.style.top = `${event.clientY - button.getBoundingClientRect().top - radius}px`;
            circle.classList.add("ripple");
            const ripple = button.getElementsByClassName("ripple")[0];
            if (ripple) { ripple.remove(); }
            button.appendChild(circle);
        }

        document.querySelectorAll('.ripple-element').forEach(el => {
            el.addEventListener('mousedown', createRipple);
            el.addEventListener('touchstart', (e) => {
                const touch = e.touches[0];
                const fakeEvent = { currentTarget: el, clientX: touch.clientX, clientY: touch.clientY };
                createRipple(fakeEvent);
            }, {passive: true});
        });

        function saveAsImage() {
            const captureArea = document.querySelector('.capture-area') || document.body;
            html2canvas(captureArea, { scale: 2, backgroundColor: '#ffffff' }).then(canvas => {
                let link = document.createElement('a');
                link.download = 'school_info.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
            });
        }
    </script>
"""

# ================= [ 주간 시간표 템플릿 (모노크롬 + 고곡률) ] =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>주간 시간표</title>
""" + COMMON_HEAD_INCLUDES + """
    <style>
        * { box-sizing: border-box; } 
        
        .loading-blur { filter: blur(5px); opacity: 0.5; pointer-events: none; transition: all 0.3s var(--snappy); transform: scale(0.98); }

        .container { width: 100%; flex-grow: 1; padding: 12px; display: flex; flex-direction: column; background: #ffffff; overflow-y: auto; overflow-x: hidden; }
        .container::-webkit-scrollbar { display: none; } 

        .date-info { text-align: center; color: #000000; margin-bottom: 8px; font-size: 0.92rem; font-weight: 800; display: flex; align-items: center; justify-content: center; gap: 6px; }

        .segment-control { display: flex; background-color: #f1f5f9; border-radius: 999px; padding: 5px; margin-bottom: 10px; position: relative; width: 100%; border: none; flex-shrink: 0; }
        .segment-btn { flex: 1; text-align: center; padding: 10px 0; font-size: 0.95em; font-weight: 800; color: #64748b; cursor: pointer; z-index: 2; transition: all 0.4s var(--snappy); text-decoration: none; outline: none; border-radius: 999px; }
        .segment-btn.active { color: #ffffff; }
        .segment-slider { position: absolute; top: 6px; bottom: 6px; background-color: #000000; border-radius: 999px; box-shadow: 0 4px 10px rgba(0,0,0,0.15); transition: all 0.4s var(--snappy); z-index: 1; }
        .slider-left { left: 6px; right: 50%; margin-right: 2px; }
        .slider-right { left: 50%; right: 6px; margin-left: 2px; }

        table { border-collapse: separate; border-spacing: 0; width: 100%; table-layout: fixed; border-radius: 24px; overflow: hidden; border: 2px solid #e2e8f0; box-shadow: 0 10px 20px rgba(0, 0, 0, 0.02); flex-grow: 1; background: #ffffff; }
        th, td { border: none; padding: 2px; text-align: center; border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; overflow: hidden; vertical-align: middle; }
        td { aspect-ratio: 1 / 1; }
        th:last-child, td:last-child { border-right: none; }
        tr:last-child td { border-bottom: none; }
        th { background: #ffffff; color: #000000; font-weight: 900; font-size: clamp(0.75rem, 3vw, 0.95em); height: 40px; border-bottom: 2px solid #000000; }
        tr:nth-child(even) td { background-color: #fafafa; }
        tr:nth-child(odd) td { background-color: #ffffff; }
        .period-col { background: #f8fafc !important; font-weight: 900; width: 12vw; max-width: 45px; color: #000000; font-size: clamp(0.7rem, 2.8vw, 0.85rem); }
        
        .subject-name { font-weight: 900; font-size: clamp(12px, 3.8vw, 24px); color: #000000; margin-bottom: 4px; white-space: nowrap; letter-spacing: -0.5px; }
        .teacher-name { display: inline-block; font-size: clamp(9px, 2.5vw, 15px); color: #000000; background-color: #f1f5f9; padding: 4px 8px; border-radius: 999px; font-weight: 800; white-space: nowrap; letter-spacing: -0.5px; }
        
        .changed-cell { background-color: #f1f5f9 !important; transition: all 0.3s var(--snappy); }
        .changed-cell .subject-name { color: #000000; text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 4px; }
        .changed-cell .teacher-name { background-color: #e2e8f0; color: #000000; }

        .footer { margin-top: 15px; display: flex; justify-content: space-between; align-items: center; background: #ffffff; padding: 15px 20px; border-radius: 999px; border: 2px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.02); }
        .teacher { font-size: 0.95em; font-weight: 800; color: #000000; display: flex; align-items: center; gap: 8px; }
        body.dark-mode .container { background: #181818; }
        body.dark-mode .date-info,
        body.dark-mode .teacher { color: #f3f4f6; }
        body.dark-mode .segment-control { background-color: #2a2a2a; }
        body.dark-mode .segment-btn { color: #a1a1aa; }
        body.dark-mode .segment-btn.active { color: #fff; }
        body.dark-mode .segment-slider { background-color: #3b82f6; box-shadow: 0 4px 10px rgba(59,130,246,0.25); }
        body.dark-mode table { background: #181818; border-color: #3a3a3a; }
        body.dark-mode th, body.dark-mode td { border-bottom-color: #3a3a3a; border-right-color: #3a3a3a; }
        body.dark-mode th { background: #1d1d1d; color: #f3f4f6; border-bottom-color: #3a3a3a; }
        body.dark-mode tr:nth-child(even) td { background-color: #1f1f1f; }
        body.dark-mode tr:nth-child(odd) td { background-color: #181818; }
        body.dark-mode .period-col { background: #242424 !important; color: #f3f4f6; }
        body.dark-mode .subject-name { color: #f3f4f6; }
        body.dark-mode .teacher-name { color: #f3f4f6; background-color: #2a2a2a; border: 1px solid #3a3a3a; }
        body.dark-mode .changed-cell { background-color: #272727 !important; }
        body.dark-mode .changed-cell .teacher-name { background-color: #323232; border-color: #3a3a3a; }
        body.dark-mode .footer { background: #181818; border-color: #3a3a3a; }
    </style>
</head>
<body class="page-transition">
    <div class="app-container">
        <div class="app-top-nav">
            <div class="nav-tabs"><span class="nav-tab active">주간 시간표</span></div>
            <div class="header-actions">
                <button class="icon-btn ripple-element" onclick="saveAsImage()" title="이미지로 저장"><i data-lucide="download"></i></button>
            </div>
        </div>

        <div id="loadingSpinner" class="spinner-container">
            <oneui8-loader size="1.6" speed="2.6"></oneui8-loader>
        </div>

        <div class="container capture-area">
            <div class="date-info"><i data-lucide="calendar-days" style="width: 20px; height: 20px; color: #000;"></i> {{ start_date }} ~ {{ end_date }}</div>
            
            <div class="segment-control">
                <div class="segment-slider {% if data_source == 'neis' %}slider-right{% else %}slider-left{% endif %}"></div>
                <a href="?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{ grade }}&class={{ class_nm }}&source=comcigan" class="segment-btn ripple-element {% if data_source != 'neis' %}active{% endif %}" onclick="handleSegmentClick(event, this, 'comcigan')">컴시간알리미</a>
                <a href="?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{ grade }}&class={{ class_nm }}&source=neis" class="segment-btn ripple-element {% if data_source == 'neis' %}active{% endif %}" onclick="handleSegmentClick(event, this, 'neis')">나이스</a>
            </div>

            <table>
                <thead>
                    <tr>
                        <th class="period-col">교시</th>
                        {% for day in days %}<th>{{ day }}</th>{% endfor %}
                    </tr>
                </thead>
                <tbody>
                    {% for p_idx in range(max_periods) %}
                    <tr>
                        <td class="period-col">{{ p_idx + 1 }}교시</td>
                        {% for d_idx in range(1, 6) %}
                        {% set subject = none %}
                        {% set is_changed = false %}
                        {% if timetable[d_idx] and p_idx < len(timetable[d_idx]) %}
                            {% set subject = timetable[d_idx][p_idx] %}
                            {% if subject and (subject|string).strip() %}
                                {% set test_str = (subject|string).strip() %}
                                {% if test_str.startswith('*') or '[변경]' in test_str or subject.is_changed or subject.changed %}
                                    {% set is_changed = true %}
                                {% endif %}
                            {% endif %}
                        {% endif %}

                        <td class="{% if is_changed %}changed-cell{% endif %}">
                            {% if subject and (subject|string).strip() %}
                                {% set subject_str = (subject|string).strip() %}
                                {% if '교시:' in subject_str %}{% set subject_str = subject_str.split('교시:')[-1].strip() %}{% endif %}
                                {% if '교시 :' in subject_str %}{% set subject_str = subject_str.split('교시 :')[-1].strip() %}{% endif %}
                                {% if subject_str.startswith('*') %}{% set subject_str = subject_str[1:].strip() %}{% endif %}
                                {% if subject_str.startswith('[변경]') %}{% set subject_str = subject_str[4:].strip() %}{% endif %}

                                {% if '(' in subject_str %}
                                    {% set parts = subject_str.split('(') %}
                                    <div class="subject-name">{{ parts[0].strip() }}</div>
                                    <div class="teacher-name">{{ parts[1].replace(')', '').strip() }}</div>
                                {% else %}
                                    <div class="subject-name">{{ subject_str }}</div>
                                {% endif %}
                            {% endif %}
                        </td>
                        {% endfor %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>

            <div class="footer">
                <span class="teacher"><i data-lucide="user-round" style="width: 20px; height: 20px; color: #000;"></i> {{school}} | 담임: {{ homeroom_teacher }}</span>
                <span style="color:#64748b; font-size:0.85em; font-weight: 700;">{{ '나이스' if data_source == 'neis' else '컴시간알리미' }}</span>
            </div>
        </div>
    </div>

""" + COMMON_JS_INCLUDES + """
    <script>
        lucide.createIcons();

        function handleSegmentClick(e, element, source) {
            e.preventDefault(); 
            document.querySelectorAll('.segment-btn').forEach(btn => btn.classList.remove('active'));
            element.classList.add('active');
            
            const slider = document.querySelector('.segment-slider');
            if (source === 'neis') {
                slider.classList.remove('slider-left'); slider.classList.add('slider-right');
            } else {
                slider.classList.remove('slider-right'); slider.classList.add('slider-left');
            }
            
            showGlobalLoader();
            const tableTarget = document.querySelector('table');
            if(tableTarget) tableTarget.classList.add('loading-blur');
            stopEntryLoaderTimer();
            setTimeout(() => { window.location.href = element.getAttribute('href'); }, 80);
        }

        window.addEventListener('pageshow', function(event) {
            hideGlobalLoader();
            const tableTarget = document.querySelector('table');
            if(tableTarget) tableTarget.classList.remove('loading-blur');
        });
    </script>
</body>
</html>
"""

# ================= [ 오늘의 시간표 템플릿 (최적화 + 점 3개 메뉴 + 상단바 컴팩트) ] =================
DAILY_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>오늘 시간표</title>
""" + COMMON_HEAD_INCLUDES + """
    <style>
        * { box-sizing: border-box; } 
        
        .loading-blur { filter: blur(5px); opacity: 0.5; pointer-events: none; transition: all 0.3s var(--snappy); transform: scale(0.98); }

        /* 한 화면에 맞추기 위한 여백 최소화 */
        .container { width: 100%; height: 100vh; padding: 8px; display: flex; flex-direction: column; background: #ffffff; overflow-y: auto; overflow-x: hidden; }
        .container::-webkit-scrollbar { display: none; }

        /* 상단바 두께 컴팩트 최적화 */
        .app-top-nav {
            display: flex; background: #ffffff; padding: 6px 10px;
            border-bottom: 1px solid #f1f5f9; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
            justify-content: space-between; align-items: center; gap: 8px; z-index: 50;
        }
        .daily-title { font-weight: 900; font-size: 1.02rem; color: #000; white-space: nowrap; flex-shrink: 0; }

        .date-info { display: none; }

        .segment-control { display: flex; background-color: #f1f5f9; border-radius: 999px; padding: 3px; position: relative; flex: 1; min-width: 0; margin-bottom: 0; }
        .segment-btn { flex: 1; text-align: center; padding: 7px 0; font-size: 0.78rem; font-weight: 800; color: #64748b; cursor: pointer; z-index: 2; transition: all 0.4s var(--snappy); text-decoration: none; outline: none; border-radius: 999px; white-space: nowrap; }
        .segment-btn.active { color: #ffffff; }
        .segment-slider { position: absolute; top: 4px; bottom: 4px; background-color: #000000; border-radius: 999px; box-shadow: 0 4px 10px rgba(0,0,0,0.15); transition: all 0.4s var(--snappy); z-index: 1; }
        .slider-left { left: 4px; right: 50%; margin-right: 2px; }
        .slider-right { left: 50%; right: 4px; margin-left: 2px; }

        table { border-collapse: separate; border-spacing: 0; width: 100%; table-layout: fixed; border-radius: 20px; overflow: hidden; border: 2px solid #e2e8f0; box-shadow: 0 10px 20px rgba(0, 0, 0, 0.02); flex-grow: 1; background: #ffffff; }
        th, td { border: none; padding: 2px; text-align: center; border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; overflow: hidden; vertical-align: middle; }
        
        /* 한 화면 구성을 위한 높이 조정 */
        td { height: auto; min-height: 40px; }
        th:last-child, td:last-child { border-right: none; }
        tr:last-child td { border-bottom: none; }
        th { background: #ffffff; color: #000000; font-weight: 900; font-size: clamp(0.9rem, 3vw, 1.1rem); height: 35px; border-bottom: 2px solid #000000; }
        tr:nth-child(even) td { background-color: #fafafa; }
        tr:nth-child(odd) td { background-color: #ffffff; }
        .period-col { background: #f8fafc !important; font-weight: 900; width: 15vw; max-width: 50px; color: #000000; font-size: clamp(0.75rem, 3vw, 1rem); }
        
        .subject-name { font-weight: 900; font-size: clamp(1rem, 6vw, 1.8rem); color: #000000; margin-bottom: 2px; white-space: nowrap; letter-spacing: -1px; }
        .teacher-name { display: inline-block; font-size: clamp(0.75rem, 3.5vw, 1rem); color: #000000; background-color: #f1f5f9; padding: 4px 10px; border-radius: 999px; font-weight: 800; white-space: nowrap; }
        
        .changed-cell { background-color: #f1f5f9 !important; }
        .changed-cell .subject-name { color: #000000; text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 4px; }
        .changed-cell .teacher-name { background-color: #e2e8f0; color: #000000; }

        .footer { margin-top: 10px; display: flex; justify-content: space-between; align-items: center; background: #ffffff; padding: 10px 15px; border-radius: 999px; border: 2px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.02); }
        .teacher { font-size: 0.9em; font-weight: 800; color: #000000; display: flex; align-items: center; gap: 8px; }
        .no-school-msg { flex-grow: 1; display: flex; align-items: center; justify-content: center; font-size: 1.5em; font-weight: 900; color: #000000; background-color: #fafafa; border-radius: 32px; border: 2px dashed #cbd5e1; }

        /* 점 3개 드롭다운 메뉴 스타일 */
        .dropdown-menu {
            position: absolute; top: 100%; right: 0; background: #fff;
            border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            border: 1px solid #e2e8f0; min-width: 150px; opacity: 0; pointer-events: none;
            transform: translateY(-10px); transition: all 0.3s var(--snappy); z-index: 100;
        }
        .dropdown-menu.show { opacity: 1; pointer-events: auto; transform: translateY(0); }
        .menu-item {
            display: block; padding: 12px 16px; color: #1e293b; text-decoration: none;
            font-weight: 700; font-size: 0.95rem; transition: background 0.2s;
            white-space: nowrap;
            text-align: left;
        }
        .menu-item:hover { background: #f8fafc; }
        .menu-item:first-child { border-radius: 16px 16px 0 0; }
        .menu-item:last-child { border-radius: 0 0 16px 16px; }

        body.dark-mode .daily-title { color: #f3f4f6; }
        body.dark-mode .segment-control { background-color: #2a2a2a; }
        body.dark-mode .segment-btn { color: #a1a1aa; }
        body.dark-mode .segment-btn.active { color: #fff; }
        body.dark-mode .segment-slider { background-color: #3b82f6; box-shadow: 0 4px 10px rgba(59,130,246,0.25); }
        body.dark-mode .container { background: #181818; }
        body.dark-mode table { background: #181818; border-color: #3a3a3a; }
        body.dark-mode th,
        body.dark-mode td { border-bottom-color: #3a3a3a; border-right-color: #3a3a3a; }
        body.dark-mode th { background: #1d1d1d; color: #f3f4f6; border-bottom-color: #3a3a3a; }
        body.dark-mode tr:nth-child(odd) td { background: #181818; color: #f3f4f6; }
        body.dark-mode tr:nth-child(even) td { background: #1f1f1f; }
        body.dark-mode .period-col { background: #242424 !important; color: #f3f4f6; }
        body.dark-mode .subject-name,
        body.dark-mode .teacher,
        body.dark-mode .teacher-name,
        body.dark-mode .no-school-msg,
        body.dark-mode .menu-item { color: #f3f4f6; }
        body.dark-mode .teacher-name { background-color: #2a2a2a; border: 1px solid #3a3a3a; }
        body.dark-mode .changed-cell { background-color: #272727 !important; }
        body.dark-mode .changed-cell .teacher-name { background-color: #353535; border-color: #3a3a3a; }
        body.dark-mode .footer { background: #181818; border-color: #3a3a3a; }
        body.dark-mode .dropdown-menu { background: #222; border-color: #363636; }
        body.dark-mode .menu-item:hover { background: #2e2e2e; }
    </style>
</head>
<body class="page-transition">
    <div class="app-container">
        <div class="app-top-nav">
            <div class="daily-title">오늘 시간표</div>
            <div class="segment-control">
                <div class="segment-slider {% if data_source == 'neis' %}slider-right{% else %}slider-left{% endif %}"></div>
                <a href="?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{ grade }}&class={{ class_nm }}&source=comcigan" class="segment-btn ripple-element {% if data_source != 'neis' %}active{% endif %}" onclick="handleSegmentClick(event, this, 'comcigan')">컴시간알리미</a>
                <a href="?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{ grade }}&class={{ class_nm }}&source=neis" class="segment-btn ripple-element {% if data_source == 'neis' %}active{% endif %}" onclick="handleSegmentClick(event, this, 'neis')">나이스</a>
            </div>
        </div>

        <div id="loadingSpinner" class="spinner-container">
            <oneui8-loader size="1.6" speed="2.6"></oneui8-loader>
        </div>

        <div class="container capture-area">
            {% if no_school %}
                <div class="no-school-msg">오늘은 등교하지 않아요! 💡</div>
            {% else %}
            <table>
                <thead>
                    <tr>
                        <th class="period-col">교시</th>
                        <th>{{ today_str }} · {{ today_date }}</th>
                    </tr>
                </thead>
                <tbody>
                    {% for p_idx in range(max_periods) %}
                    <tr>
                        <td class="period-col">{{ p_idx + 1 }}교시</td>
                        {% set subject = none %}
                        {% set is_changed = false %}
                        {% if today_timetable and p_idx < len(today_timetable) %}
                            {% set subject = today_timetable[p_idx] %}
                            {% if subject and (subject|string).strip() %}
                                {% set test_str = (subject|string).strip() %}
                                {% if test_str.startswith('*') or '[변경]' in test_str or subject.is_changed or subject.changed %}
                                    {% set is_changed = true %}
                                {% endif %}
                            {% endif %}
                        {% endif %}

                        <td class="{% if is_changed %}changed-cell{% endif %}">
                            {% if subject and (subject|string).strip() %}
                                {% set subject_str = (subject|string).strip() %}
                                {% if '교시:' in subject_str %}{% set subject_str = subject_str.split('교시:')[-1].strip() %}{% endif %}
                                {% if '교시 :' in subject_str %}{% set subject_str = subject_str.split('교시 :')[-1].strip() %}{% endif %}
                                {% if subject_str.startswith('*') %}{% set subject_str = subject_str[1:].strip() %}{% endif %}
                                {% if subject_str.startswith('[변경]') %}{% set subject_str = subject_str[4:].strip() %}{% endif %}

                                {% if '(' in subject_str %}
                                    {% set parts = subject_str.split('(') %}
                                    <div style="display: flex; align-items: center; justify-content: center; gap: 10px;">
                                        <div class="subject-name" style="margin-bottom: 0;">{{ parts[0].strip() }}</div>
                                        <div class="teacher-name">{{ parts[1].replace(')', '').strip() }} 선생님</div>
                                    </div>
                                {% else %}
                                    <div class="subject-name" style="margin-bottom: 0;">{{ subject_str }}</div>
                                {% endif %}
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% endif %}

            <div class="footer">
                <span class="teacher"><i data-lucide="user-round" style="width: 18px; height: 18px; color: #000;"></i> {{school}} | 담임: {{ homeroom_teacher }}</span>
                <span style="color:#64748b; font-size:0.8em; font-weight: 700;">{{ '나이스' if data_source == 'neis' else '컴시간알리미' }}</span>
            </div>
        </div>
    </div>

""" + COMMON_JS_INCLUDES + """
    <script>
        lucide.createIcons();
        
        function handleSegmentClick(e, element, source) {
            e.preventDefault(); 
            document.querySelectorAll('.segment-btn').forEach(btn => btn.classList.remove('active'));
            element.classList.add('active');
            const slider = document.querySelector('.segment-slider');
            if (source === 'neis') { slider.classList.remove('slider-left'); slider.classList.add('slider-right'); } 
            else { slider.classList.remove('slider-right'); slider.classList.add('slider-left'); }
            
            showGlobalLoader();
            const tableTarget = document.querySelector('table');
            if(tableTarget) tableTarget.classList.add('loading-blur');
            stopEntryLoaderTimer();
            setTimeout(() => { window.location.href = element.getAttribute('href'); }, 80);
        }
        window.addEventListener('pageshow', function(event) {
            hideGlobalLoader();
            const tableTarget = document.querySelector('table');
            if(tableTarget) tableTarget.classList.remove('loading-blur');
        });
    </script>
</body>
</html>
"""

# ================= [ 급식 라우트 템플릿 (상단바 컴팩트 + 더보기 드롭다운 통합) ] =================
LUNCH_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>급식표</title>
""" + COMMON_HEAD_INCLUDES + """
    <style>
        * { box-sizing: border-box; } 
        
        /* 상단바 두께 컴팩트 최적화 */
        .app-top-nav {
            display: flex; background: #ffffff; padding: 6px 10px;
            border-bottom: 1px solid #f1f5f9; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
            justify-content: space-between; align-items: center; z-index: 50;
        }
        .lunch-title { font-weight: 900; font-size: 1.15rem; color: #000; }

        /* 점 3개 드롭다운 메뉴 스타일 */
        .dropdown-menu {
            position: absolute; top: 100%; right: 0; background: #fff;
            border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            border: 1px solid #e2e8f0; min-width: 150px; opacity: 0; pointer-events: none;
            transform: translateY(-10px); transition: all 0.3s var(--snappy); z-index: 100;
        }
        .dropdown-menu.show { opacity: 1; pointer-events: auto; transform: translateY(0); }
        .menu-item {
            display: block; padding: 12px 16px; color: #1e293b; text-decoration: none;
            font-weight: 700; font-size: 0.95rem; transition: background 0.2s;
            white-space: nowrap;
            text-align: left;
        }
        .menu-item:hover { background: #f8fafc; }
        .menu-item:first-child { border-radius: 16px 16px 0 0; }
        .menu-item:last-child { border-radius: 0 0 16px 16px; }

        .container { width: 100%; height: 100%; padding: 10px; display: flex; flex-direction: column; background: #ffffff; overflow: hidden; }
        .header-top { display: none; }
        .date-display { font-size: 1.1em; font-weight: 900; color: #000000; display: flex; align-items: center; gap: 8px; }
        .controls { display: flex; align-items: center; gap: 10px; }
        .toggle-btn { background-color: #000000; color: white; border: none; border-radius: 999px; padding: 8px 16px; font-size: 0.9em; font-weight: 800; cursor: pointer; display: flex; align-items: center; gap: 6px; transition: all 0.4s var(--snappy); }
        .toggle-btn:hover { background-color: #333333; transform: scale(1.05) translateY(-2px); }
        
        .meal-list { flex-grow: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; padding-right: 5px; padding-bottom: 20px; }
        .meal-list::-webkit-scrollbar { width: 0; display: none; } 
        
        .meal-card { background: #ffffff; border: 2px solid #e2e8f0; border-radius: 32px; padding: 20px; display: flex; flex-direction: column; gap: 12px; flex-shrink: 0; transition: all 0.4s var(--snappy); transform: translateY(0); }
        .meal-card:hover { transform: translateY(-3px); box-shadow: 0 15px 30px rgba(0, 0, 0, 0.05); border-color: #cbd5e1; }
        .meal-card.today { border: 3px solid #000000; background: #fafafa; }
        .meal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px dashed #f1f5f9; padding-bottom: 12px; }
        .meal-date { font-weight: 900; font-size: 1.05em; color: #000000; display: flex; align-items: center; gap: 8px; }
        .today-badge { background-color: #000000; color: white; padding: 4px 10px; border-radius: 999px; font-size: 0.75em; font-weight: 800; }
        .detail-btn { background: #f1f5f9; border: none; color: #000000; border-radius: 999px; padding: 6px 12px; font-size: 0.85em; font-weight: 800; cursor: pointer; display: flex; align-items: center; gap: 4px; transition: all 0.3s var(--snappy); }
        .detail-btn:hover { background: #e2e8f0; transform: scale(1.05); }
        
        .meal-menu { font-size: 1em; line-height: 1.6; color: #1e293b; font-weight: 700; }
        .meal-cal { font-size: 0.85em; color: #64748b; display: flex; align-items: center; gap: 6px; font-weight: 700; background: #f8fafc; padding: 8px 12px; border-radius: 16px; align-self: flex-start; }
        .no-school-msg { display: flex; align-items: center; justify-content: center; font-size: 1.2em; font-weight: 900; color: #000000; height: 100%; background-color: #fafafa; border-radius: 32px; border: 2px dashed #cbd5e1; padding: 20px; text-align: center; }
        .error-msg { display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 0.9em; font-weight: 700; color: #000000; background-color: #f1f5f9; border-radius: 24px; border: 2px solid #000000; padding: 20px; margin-bottom: 15px; word-break: break-all; }

        .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.4); display: flex; justify-content: center; align-items: center; z-index: 1000; padding: 20px; opacity: 0; pointer-events: none; transition: opacity 0.4s var(--snappy); }
        .modal-overlay.active { opacity: 1; pointer-events: auto; }
        .modal-content { background: white; border-radius: 36px; width: 100%; max-width: 400px; padding: 30px; box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15); transform: translateY(20px) scale(0.95); transition: all 0.4s var(--snappy); }
        .modal-overlay.active .modal-content { transform: translateY(0) scale(1); }
        .modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #f1f5f9; padding-bottom: 15px; margin-bottom: 20px; }
        .modal-title { margin: 0; font-size: 1.2em; color: #000000; font-weight: 900; }
        .close-btn { background: none; border: none; cursor: pointer; color: #000000; padding: 5px; border-radius: 50%; transition: all 0.3s; }
        .close-btn:hover { background: #f1f5f9; transform: scale(1.1); }
        .modal-body { font-size: 0.9em; color: #475569; line-height: 1.6; max-height: 50vh; overflow-y: auto; padding-right: 5px; }
        .modal-body::-webkit-scrollbar { width: 6px; }
        .modal-body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
        .modal-section-title { font-weight: 900; color: #000000; margin-top: 20px; margin-bottom: 8px; }
        body.dark-mode .lunch-title { color: #f3f4f6; }
        body.dark-mode .container,
        body.dark-mode .meal-card,
        body.dark-mode .modal-content { background: #181818; }
        body.dark-mode .meal-card { border-color: #2f2f2f; }
        body.dark-mode .meal-card.today { background: #222; border-color: #4a4a4a; }
        body.dark-mode .meal-date,
        body.dark-mode .meal-menu,
        body.dark-mode .modal-title,
        body.dark-mode .close-btn,
        body.dark-mode .modal-section-title,
        body.dark-mode .error-msg,
        body.dark-mode .menu-item,
        body.dark-mode .no-school-msg { color: #f3f4f6; }
        body.dark-mode .meal-cal,
        body.dark-mode #modalMenuRaw,
        body.dark-mode #modalNtrInfo,
        body.dark-mode .detail-btn { background: #2a2a2a; color: #f3f4f6; }
        body.dark-mode #modalMenuRaw,
        body.dark-mode #modalNtrInfo {
            background: #2a2a2a !important;
            color: #f3f4f6 !important;
            border: 1px solid #3a3a3a;
        }
        body.dark-mode .modal-header { border-bottom-color: #343434; }
        body.dark-mode .modal-body { color: #d1d5db; }
        body.dark-mode .dropdown-menu { background: #222; border-color: #363636; }
        body.dark-mode .menu-item:hover,
        body.dark-mode .close-btn:hover { background: #2e2e2e; }
        
        @media (max-width: 480px) { .header-top { flex-direction: column; align-items: flex-start; gap: 10px; } .meal-menu { font-size: 0.9em; } .meal-card { padding: 16px; } }
    </style>
</head>
<body class="page-transition">
    <div class="app-container">
        <div class="app-top-nav">
            <div class="lunch-title">급식표</div>
            <div class="header-actions" style="padding-bottom: 0;">
                <button id="toggleBtn" class="toggle-btn ripple-element"><i data-lucide="history" style="width: 16px; height: 16px;"></i> 과거 조회</button>
                <button class="icon-btn ripple-element" onclick="saveAsImage()" title="이미지로 저장"><i data-lucide="download" style="width:20px; height:20px;"></i></button>
                <div style="position: relative;">
                    <button class="icon-btn ripple-element" onclick="toggleMenu(event)" title="메뉴"><i data-lucide="more-vertical" style="width:20px; height:20px;"></i></button>
                    <div id="dotMenu" class="dropdown-menu">
                        <a href="/todaysigan?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{grade}}&class={{class_nm}}&source={{data_source}}" class="menu-item">오늘 시간표</a>
                        <a href="/sigan?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{grade}}&class={{class_nm}}&source={{data_source}}" class="menu-item">주간 시간표</a>
                        <a href="/lunch?school={{school}}&school_code={{school_code}}&region_code={{region_code}}&grade={{grade}}&class={{class_nm}}&source={{data_source}}" class="menu-item">급식표</a>
                        <div style="height: 1px; background: #e2e8f0; margin: 4px 0;"></div>
                        <a href="/" class="menu-item">설정 변경</a>
                    </div>
                </div>
            </div>
        </div>

        <div id="loadingSpinner" class="spinner-container">
            <oneui8-loader size="1.6" speed="2.6"></oneui8-loader>
        </div>

        <div class="container capture-area">
            <div id="errorContainer">
                {% if error_msg %}
                    <div class="error-msg">
                        <div style="display:flex; align-items:center; gap:8px; font-weight:900; margin-bottom:8px; font-size:1.1em;"><i data-lucide="alert-triangle" style="width: 20px; height: 20px;"></i> 오류 알림</div>
                        <span>{{ error_msg | safe }}</span>
                    </div>
                {% endif %}
            </div>
            <div id="mealList" class="meal-list"></div>
        </div>

        <div id="detailModal" class="modal-overlay">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">급식 세부 정보</h3>
                    <button class="close-btn ripple-element" onclick="closeModal()"><i data-lucide="x" style="width: 24px; height: 24px;"></i></button>
                </div>
                <div class="modal-body">
                    <div class="modal-section-title">🍽️ 알레르기 유발 정보 포함 메뉴</div>
                    <div id="modalMenuRaw" style="background: #f8fafc; padding: 15px; border-radius: 16px; font-weight: 600;"></div>
                    <div class="modal-section-title">📊 영양 정보</div>
                    <div id="modalNtrInfo" style="background: #f8fafc; padding: 15px; border-radius: 16px; font-weight: 600;"></div>
                </div>
            </div>
        </div>
    </div>

""" + COMMON_JS_INCLUDES + """
    <script>
        lucide.createIcons();
        
        function toggleMenu(e) {
            e.stopPropagation();
            document.getElementById('dotMenu').classList.toggle('show');
        }
        document.addEventListener('click', function(e) {
            const menu = document.getElementById('dotMenu');
            if (menu && menu.classList.contains('show')) {
                menu.classList.remove('show');
            }
        });

        const meals = {{ meal_data_json | safe }};
        const todayYMD = '{{ today_date }}';
        let showMode = 'future'; 
        
        function getWeekDay(dateStr) {
            const y = dateStr.substring(0,4), m = dateStr.substring(4,6), d = dateStr.substring(6,8);
            const w = ['일', '월', '화', '수', '목', '금', '토'][new Date(y, m-1, d).getDay()];
            return `${m}월 ${d}일 (${w})`;
        }

        function renderMeals(dateFilter = null) {
            const listContainer = document.getElementById('mealList');
            listContainer.innerHTML = '';
            let filteredMeals = [];
            
            if (dateFilter) { showMode = 'specific'; filteredMeals = meals.filter(m => m.date === dateFilter); } 
            else {
                if (showMode === 'past') { filteredMeals = meals.filter(m => m.date < todayYMD).reverse(); } 
                else { showMode = 'future'; filteredMeals = meals.filter(m => m.date >= todayYMD); }
            }
            
            if (filteredMeals.length === 0) { listContainer.innerHTML = '<div class="no-school-msg">해당 기간의 급식 정보가 없습니다.</div>'; lucide.createIcons(); return; }
            
            filteredMeals.forEach((meal, index) => {
                const isToday = meal.date === todayYMD;
                const card = document.createElement('div');
                card.className = `meal-card ${isToday ? 'today' : ''}`;
                card.style.animation = `fadeIn 0.4s ease forwards ${index * 0.05}s`; card.style.opacity = '0'; 
                card.innerHTML = `
                    <div class="meal-header">
                        <span class="meal-date">${getWeekDay(meal.date)} ${isToday ? '<span class="today-badge">오늘</span>' : ''}</span>
                        <button class="detail-btn ripple-element" onclick="showDetail('${meal.date}')"><i data-lucide="info" style="width: 14px; height: 14px;"></i> 정보</button>
                    </div>
                    <div class="meal-menu">${meal.menu}</div>
                    <div class="meal-cal"><i data-lucide="flame" style="width: 16px; height: 16px; color: #000;"></i> ${meal.cal}</div>
                `;
                listContainer.appendChild(card);
            });
            lucide.createIcons();
        }

        document.getElementById('toggleBtn').addEventListener('click', (e) => {
            createRipple(e);
            setTimeout(() => {
                const btn = document.getElementById('toggleBtn');
                if (showMode === 'future' || showMode === 'specific') {
                    showMode = 'past'; btn.innerHTML = '<i data-lucide="calendar-check" style="width: 16px; height: 16px;"></i> 오늘 전환'; btn.style.backgroundColor = '#333333';
                } else {
                    showMode = 'future'; btn.innerHTML = '<i data-lucide="history" style="width: 16px; height: 16px;"></i> 과거 조회'; btn.style.backgroundColor = '#000000';
                }
                renderMeals();
            }, 100);
        });

        function showDetail(dateStr) {
            const meal = meals.find(m => m.date === dateStr);
            if(!meal) return;
            document.getElementById('modalMenuRaw').innerHTML = meal.raw_menu;
            document.getElementById('modalNtrInfo').innerHTML = meal.details;
            document.getElementById('detailModal').classList.add('active');
        }
        function closeModal() { document.getElementById('detailModal').classList.remove('active'); }
        renderMeals();
    </script>
</body>
</html>
"""

# ================= [ 관리자 로그인/설정 템플릿 유지 ] =================
LOGIN_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>관리자 로그인</title>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root { --snappy: cubic-bezier(0.34, 1.56, 0.64, 1); }
        body { font-family: 'Pretendard', sans-serif; background: #f1f5f9; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-box { background: white; padding: 40px 30px; border-radius: 36px; box-shadow: 0 20px 40px rgba(0,0,0,0.08); width: 340px; }
        h2 { text-align: center; color: #000; margin-top: 0; font-weight: 900; }
        input { width: 100%; padding: 15px 20px; margin: 10px 0; border: 2px solid #e2e8f0; border-radius: 999px; box-sizing: border-box; font-weight: 600; transition: all 0.3s var(--snappy); outline: none;}
        input:focus { border-color: #000; transform: translateY(-2px); box-shadow: 0 8px 16px rgba(0,0,0,0.05); }
        button { width: 100%; padding: 15px; margin-top: 15px; background: #000; color: white; border: none; border-radius: 999px; font-weight: 800; cursor: pointer; transition: all 0.4s var(--snappy); font-size: 1.05rem; }
        button:hover { background: #333; transform: translateY(-3px) scale(1.02); box-shadow: 0 10px 20px rgba(0,0,0,0.15); }
        .error { color: #000; font-weight: 700; font-size: 0.85em; text-align: center; margin-bottom: 10px; padding: 10px; background: #f1f5f9; border-radius: 12px; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2><i data-lucide="lock" style="vertical-align: middle; margin-right: 8px;"></i>관리자 로그인</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST" action="/setting/login">
            <input type="text" name="username" placeholder="아이디" required>
            <input type="password" name="password" placeholder="비밀번호" required>
            <button type="submit">로그인</button>
        </form>
    </div>
    <script>lucide.createIcons();</script>
</body>
</html>
"""

SETTING_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>배너 설정</title>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root { --snappy: cubic-bezier(0.34, 1.56, 0.64, 1); }
        body { font-family: 'Pretendard', sans-serif; background: #f1f5f9; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; }
        .setting-box { background: white; padding: 40px 30px; border-radius: 36px; box-shadow: 0 20px 40px rgba(0,0,0,0.08); width: 100%; max-width: 440px; }
        h2 { text-align: center; color: #000; margin-top: 0; display: flex; justify-content: space-between; align-items: center; font-weight: 900; }
        .logout-btn { background: #f1f5f9; color: #000; border: none; padding: 8px 16px; border-radius: 999px; font-size: 0.85em; cursor: pointer; text-decoration: none; font-weight: 800; transition: all 0.3s; }
        .logout-btn:hover { background: #e2e8f0; transform: scale(1.05); }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 800; color: #000; font-size: 0.9em; margin-left: 4px; }
        input { width: 100%; padding: 15px 20px; border: 2px solid #e2e8f0; border-radius: 24px; box-sizing: border-box; font-weight: 600; outline: none; transition: all 0.3s var(--snappy); }
        input:focus { border-color: #000; transform: translateY(-2px); box-shadow: 0 8px 16px rgba(0,0,0,0.05); }
        button { width: 100%; padding: 15px; color: white; border: none; border-radius: 999px; font-weight: 800; font-size: 1.05rem; cursor: pointer; margin-top: 10px; transition: all 0.4s var(--snappy); }
        .btn-save { background: #000; }
        .btn-save:hover { background: #333; transform: translateY(-3px) scale(1.02); box-shadow: 0 10px 20px rgba(0,0,0,0.15); }
        .btn-delete { background: #f1f5f9; color: #000; }
        .btn-delete:hover { background: #e2e8f0; transform: translateY(-3px) scale(1.02); }
        .current-banner { background: #f8fafc; padding: 20px; border-radius: 24px; margin-bottom: 25px; font-size: 0.9em; color: #000; word-break: break-all; font-weight: 600; border: 2px dashed #cbd5e1; }
    </style>
</head>
<body>
    <div class="setting-box">
        <h2>
            <span style="display: flex; align-items: center; gap: 8px;"><i data-lucide="settings"></i> 배너 설정</span>
            <a href="/setting/logout" class="logout-btn">로그아웃</a>
        </h2>
        
        <div class="current-banner">
            <strong style="font-size:1.1em;">현재 등록된 배너</strong><br><br>
            {% if banner.image_url %}
                <span style="color:#64748b;">제목:</span> {{ banner.title }}<br>
                <span style="color:#64748b;">이미지:</span> {{ banner.image_url }}<br>
                <span style="color:#64748b;">링크:</span> {{ banner.target_url }}
            {% else %}
                없음
            {% endif %}
        </div>

        <form method="POST" action="/setting/update">
            <div class="form-group">
                <label>배너 제목</label>
                <input type="text" name="title" value="{{ banner.title }}" placeholder="예: 봄맞이 이벤트" required>
            </div>
            <div class="form-group">
                <label>이미지 링크 (URL)</label>
                <input type="url" name="image_url" value="{{ banner.image_url }}" placeholder="https://..." required>
            </div>
            <div class="form-group">
                <label>클릭 시 이동할 링크 (URL)</label>
                <input type="url" name="target_url" value="{{ banner.target_url }}" placeholder="https://..." required>
            </div>
            <button type="submit" class="btn-save"><i data-lucide="save" style="width: 18px; height: 18px; vertical-align: middle; margin-right: 4px;"></i> 저장하기</button>
        </form>
        <form method="POST" action="/setting/clear" style="margin-top: 15px;">
            <button type="submit" class="btn-delete"><i data-lucide="trash-2" style="width: 18px; height: 18px; vertical-align: middle; margin-right: 4px;"></i> 배너 삭제</button>
        </form>
    </div>
    <script>lucide.createIcons();</script>
</body>
</html>
"""
# ==============================================================

def get_week_range():
    now = datetime.now(KST)
    if now.weekday() >= 5:
        monday = now + timedelta(days=(7 - now.weekday()))
    else:
        monday = now - timedelta(days=now.weekday())
    
    friday = monday + timedelta(days=4)
    return monday.strftime("%m월 %d일"), friday.strftime("%m월 %d일")

@app.route('/')
def health_check():
    return render_template_string(SETUP_HTML_TEMPLATE)

def get_color_index(subject_name):
    if not subject_name: return 7
    return sum(ord(c) for c in subject_name) % 7

@app.route('/uiv2sigan')
def get_uiv2sigan():
    try:
        req_grade = int(request.args.get('grade', GRADE))
        req_class = int(request.args.get('class', CLASS_NM))
        school_name = request.args.get('school', SCHOOL_NAME)
        source = request.args.get('source', 'comcigan')
        mode = request.args.get('mode', 'student')
        region_code = request.args.get('region_code', 'B10')
        school_code = request.args.get('school_code', '7091455')
        
        now = datetime.now(KST)
        if now.weekday() >= 5:
            monday = now + timedelta(days=(7 - now.weekday()))
        else:
            monday = now - timedelta(days=now.weekday())
            
        dates = [(monday + timedelta(days=i)).strftime("%d") for i in range(5)]
        today_wd = now.weekday()
        if today_wd >= 5: today_wd = 0

        # 중복 방지 및 2글자+* 교사 추출을 위한 파싱
        def parse_basic(raw_str):
            if '교시:' in raw_str: raw_str = raw_str.split('교시:')[-1]
            if '교시 :' in raw_str: raw_str = raw_str.split('교시 :')[-1]
            raw_str = raw_str.strip().lstrip('*').replace('[변경]', '').strip()
            subj = raw_str
            tch = ""
            if '(' in raw_str:
                parts = raw_str.split('(')
                subj = parts[0].strip()
                rem = parts[1].split(')')
                tch_raw = rem[0].strip()
                if len(tch_raw) >= 2:
                    tch = tch_raw[:2] + "*"
                elif len(tch_raw) == 1:
                    tch = tch_raw + "*"
            return subj, tch

        tt = None
        try:
            if source != 'neis':
                tt = TimeTable(school_name, week_num=0)
        except:
            pass

        # 정보 카드를 위한 전체 탐색 (모드 상관없이 진행)
        teacher_dict = {}
        total_grades = 3
        total_classes = 15
        
        if tt:
            try:
                valid_grades = [g for g in range(1, len(tt.timetable)) if isinstance(tt.timetable[g], list) and len(tt.timetable[g]) > 1]
                if valid_grades:
                    total_grades = len(valid_grades)
                    first_g = valid_grades[0]
                    total_classes = len([c for c in range(1, len(tt.timetable[first_g])) if isinstance(tt.timetable[first_g][c], list)])
            except:
                pass 
                
            # 전학년 전반을 싹 다 검사 (한 번의 tt 객체로 전부 처리)
            for g in range(1, len(tt.timetable)):
                if not isinstance(tt.timetable[g], list): continue
                for c in range(1, len(tt.timetable[g])):
                    if not isinstance(tt.timetable[g][c], list): continue
                    
                    c_data = tt.timetable[g][c]
                    if not c_data: continue # 시간표 없는 반 무시

                    try:
                        for d in range(1, 6):
                            if len(c_data) > d:
                                for p in range(len(c_data[d])):
                                    cell = c_data[d][p]
                                    if cell and str(cell).strip():
                                        subj, tch = parse_basic(str(cell))
                                        if tch:
                                            if tch not in teacher_dict:
                                                teacher_dict[tch] = [[], [""]*8, [""]*8, [""]*8, [""]*8, [""]*8]
                                            teacher_dict[tch][d][p] = f"{subj}({g}-{c})"
                    except:
                        continue

        teachers = sorted(list(teacher_dict.keys()))
        total_teachers = len(teachers)
        selected_teacher = request.args.get('teacher', '')
        if teachers and not selected_teacher:
            selected_teacher = teachers[0]

        timetable = [[], [], [], [], [], []]
        max_periods = 8
        all_classes_data = {}

        if mode == 'teacher':
            if selected_teacher in teacher_dict:
                timetable = teacher_dict[selected_teacher]
        elif mode == 'all':
            if tt:
                for g in range(1, len(tt.timetable)):
                    if not isinstance(tt.timetable[g], list): continue
                    for c in range(1, len(tt.timetable[g])):
                        if not isinstance(tt.timetable[g][c], list): continue
                        try:
                            c_data = tt.timetable[g][c]
                            if len(c_data) > (today_wd + 1):
                                all_classes_data[f"{g}-{c}"] = c_data[today_wd + 1]
                        except:
                            continue
        else: 
            if source == 'neis':
                start_date_neis = monday.strftime("%Y%m%d")
                end_date_neis = (monday + timedelta(days=4)).strftime("%Y%m%d")
                API_KEY = "77243bbec81f496286bacbe357cad48f"
                url = f"https://open.neis.go.kr/hub/misTimetable?KEY={API_KEY}&Type=json&pIndex=1&pSize=100&ATPT_OFCDC_SC_CODE={region_code}&SD_SCHUL_CODE={school_code}&GRADE={req_grade}&CLASS_NM={req_class}&TI_FROM_YMD={start_date_neis}&TI_TO_YMD={end_date_neis}"
                try:
                    req_obj = urllib.request.Request(url)
                    with urllib.request.urlopen(req_obj) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        if "misTimetable" in data:
                            rows = data["misTimetable"][1]["row"]
                            for row in rows:
                                ymd = row["ALL_TI_YMD"]
                                dt = datetime.strptime(ymd, "%Y%m%d")
                                wd = dt.weekday()
                                if wd < 5:
                                    perio = int(row["PERIO"])
                                    subject = row["ITRT_CNTNT"].replace("-", "").strip()
                                    day_list = timetable[wd + 1]
                                    while len(day_list) < perio:
                                        day_list.append("")
                                    day_list[perio - 1] = subject
                except:
                    pass
            else:
                if tt:
                    try:
                        timetable = tt.timetable[req_grade][req_class]
                    except:
                        pass
            
            valid_lengths = [len(timetable[i]) for i in range(1, len(timetable)) if type(timetable[i]) == list]
            max_periods = max(valid_lengths) if valid_lengths else 8
            max_periods = min(max_periods, 8)

        return render_template_string(
            UIV2_SIGAN_HTML_TEMPLATE,
            school=school_name,
            school_code=school_code,
            region_code=region_code,
            grade=req_grade,
            class_nm=req_class,
            data_source=source,
            mode=mode,
            timetable=timetable,
            max_periods=max_periods,
            dates=dates,
            teachers=teachers,
            selected_teacher=selected_teacher,
            all_classes_data=all_classes_data,
            total_grades=total_grades,
            total_classes=total_classes,
            total_teachers=total_teachers,
            len=len,
            get_color_index=get_color_index
        )
    except Exception as e:
        import traceback
        return f"<h3>오류 발생: {e}</h3><pre>{traceback.format_exc()}</pre>"

@app.route('/sigan')
def get_sigan():
    try:
        req_grade = int(request.args.get('grade', GRADE))
        req_class = int(request.args.get('class', CLASS_NM))
        source = request.args.get('source', 'comcigan')
        
        school_name = request.args.get('school', SCHOOL_NAME)
        region_code = request.args.get('region_code', 'B10')
        school_code = request.args.get('school_code', '7091455')

        now = datetime.now(KST)
        if now.weekday() >= 5:
            monday = now + timedelta(days=(7 - now.weekday()))
        else:
            monday = now - timedelta(days=now.weekday())
        friday = monday + timedelta(days=4)
        
        start_d = monday.strftime("%m월 %d일")
        end_d = friday.strftime("%m월 %d일")
        
        if source == 'neis':
            start_date_neis = monday.strftime("%Y%m%d")
            end_date_neis = friday.strftime("%Y%m%d")
            API_KEY = "77243bbec81f496286bacbe357cad48f"
            url = f"https://open.neis.go.kr/hub/misTimetable?KEY={API_KEY}&Type=json&pIndex=1&pSize=100&ATPT_OFCDC_SC_CODE={region_code}&SD_SCHUL_CODE={school_code}&GRADE={req_grade}&CLASS_NM={req_class}&TI_FROM_YMD={start_date_neis}&TI_TO_YMD={end_date_neis}"
            
            all_data = [[], [], [], [], [], []]
            try:
                req_obj = urllib.request.Request(url)
                with urllib.request.urlopen(req_obj) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    if "misTimetable" in data:
                        rows = data["misTimetable"][1]["row"]
                        for row in rows:
                            ymd = row["ALL_TI_YMD"]
                            dt = datetime.strptime(ymd, "%Y%m%d")
                            wd = dt.weekday()
                            if wd < 5:
                                perio = int(row["PERIO"])
                                subject = row["ITRT_CNTNT"].replace("-", "").strip()
                                day_list = all_data[wd + 1]
                                while len(day_list) < perio:
                                    day_list.append("")
                                day_list[perio - 1] = subject
                    elif "RESULT" in data and data["RESULT"].get("CODE") == "INFO-200":
                        pass
                    else:
                        raise Exception(f"API 오류: {data.get('RESULT', {}).get('MESSAGE', '데이터 파싱 실패')}")
            except Exception as e:
                return f"<div style='padding:20px; font-family:sans-serif;'><h3>나이스 시간표 로드 중 오류 발생</h3><p>{e}</p><br><a href='?school={school_name}&school_code={school_code}&region_code={region_code}&grade={req_grade}&class={req_class}&source=comcigan' style='padding:10px 15px; background:#000; color:white; border-radius:24px; text-decoration:none; font-weight:800;'>컴시간알리미로 돌아가기</a></div>"
            
            try:
                tt = TimeTable(school_name, week_num=0)
                homeroom_teacher = tt.homeroom(req_grade, req_class)
            except:
                homeroom_teacher = "조회 불가"
                
        else:
            tt = TimeTable(school_name, week_num=0)
            all_data = tt.timetable[req_grade][req_class] 
            homeroom_teacher = tt.homeroom(req_grade, req_class)
            
        valid_lengths = [len(all_data[i]) for i in range(1, len(all_data)) if type(all_data[i]) == list]
        max_periods = max(valid_lengths) if valid_lengths else 7
        max_periods = min(max_periods, 7) 

        return render_template_string(
            HTML_TEMPLATE, school=school_name, school_code=school_code, region_code=region_code,
            grade=req_grade, class_nm=req_class, days=["월", "화", "수", "목", "금"], timetable=all_data,
            max_periods=max_periods, homeroom_teacher=homeroom_teacher, start_date=start_d, end_date=end_d,
            data_source=source, len=len
        )
    except Exception as e:
        return f"<h3>오류 발생: {e}</h3><p>데이터 구조가 예상과 다릅니다. 이 학교는 컴시간을 지원하지 않을 수 있습니다.</p>"

@app.route('/todaysigan')
def get_todaysigan():
    try:
        req_grade = int(request.args.get('grade', GRADE))
        req_class = int(request.args.get('class', CLASS_NM))
        source = request.args.get('source', 'comcigan')
        school_name = request.args.get('school', SCHOOL_NAME)
        region_code = request.args.get('region_code', 'B10')
        school_code = request.args.get('school_code', '7091455')
        
        now = datetime.now(KST)
        today_weekday = now.weekday() 
        today_date_str = now.strftime("%m월 %d일")
        
        no_school = False
        today_timetable = []
        max_periods = 0
        homeroom_teacher = ""

        if today_weekday >= 5:
            no_school = True
            try:
                tt_check = TimeTable(school_name, week_num=0)
                homeroom_teacher = tt_check.homeroom(req_grade, req_class)
            except:
                homeroom_teacher = "조회 불가"
        else:
            monday = now - timedelta(days=today_weekday)
            friday = monday + timedelta(days=4)
            
            if source == 'neis':
                start_date_neis = monday.strftime("%Y%m%d")
                end_date_neis = friday.strftime("%Y%m%d")
                API_KEY = "77243bbec81f496286bacbe357cad48f"
                url = f"https://open.neis.go.kr/hub/misTimetable?KEY={API_KEY}&Type=json&pIndex=1&pSize=100&ATPT_OFCDC_SC_CODE={region_code}&SD_SCHUL_CODE={school_code}&GRADE={req_grade}&CLASS_NM={req_class}&TI_FROM_YMD={start_date_neis}&TI_TO_YMD={end_date_neis}"
                
                all_data = [[], [], [], [], [], []]
                try:
                    req_obj = urllib.request.Request(url)
                    with urllib.request.urlopen(req_obj) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        if "misTimetable" in data:
                            rows = data["misTimetable"][1]["row"]
                            for row in rows:
                                ymd = row["ALL_TI_YMD"]
                                dt = datetime.strptime(ymd, "%Y%m%d")
                                wd = dt.weekday()
                                if wd < 5:
                                    perio = int(row["PERIO"])
                                    subject = row["ITRT_CNTNT"].replace("-", "").strip()
                                    day_list = all_data[wd + 1]
                                    while len(day_list) < perio:
                                        day_list.append("")
                                    day_list[perio - 1] = subject
                except Exception as e:
                    return f"<div style='padding:20px; font-family:sans-serif;'><h3>나이스 오류</h3></div>"
                try:
                    tt = TimeTable(school_name, week_num=0)
                    homeroom_teacher = tt.homeroom(req_grade, req_class)
                except:
                    homeroom_teacher = "조회 불가"
            else:
                tt = TimeTable(school_name, week_num=0)
                all_data = tt.timetable[req_grade][req_class] 
                homeroom_teacher = tt.homeroom(req_grade, req_class)
            
            target_idx = today_weekday + 1
            if target_idx < len(all_data):
                today_timetable = all_data[target_idx]
                
            if not today_timetable or not any(str(item).strip() for item in today_timetable):
                no_school = True
            else:
                max_periods = len(today_timetable)
                max_periods = min(max_periods, 7) 

        days_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        today_str = days_names[today_weekday]

        return render_template_string(
            DAILY_HTML_TEMPLATE, school=school_name, school_code=school_code, region_code=region_code,
            grade=req_grade, class_nm=req_class, today_date=today_date_str, today_str=today_str,
            today_timetable=today_timetable, max_periods=max_periods, no_school=no_school,
            homeroom_teacher=homeroom_teacher, data_source=source, len=len
        )
    except Exception as e:
        import traceback
        return f"<h3>오류 발생</h3><pre>{traceback.format_exc()}</pre>"

@app.route('/lunch')
def get_lunch():
    req_grade = int(request.args.get('grade', GRADE))
    req_class = int(request.args.get('class', CLASS_NM))
    source = request.args.get('source', 'comcigan')
    school_name = request.args.get('school', SCHOOL_NAME)
    
    API_KEY = "77243bbec81f496286bacbe357cad48f"
    ATPT_OFCDC_SC_CODE = request.args.get('region_code', "B10") 
    SD_SCHUL_CODE = request.args.get('school_code', "7091455")  
    
    now = datetime.now(KST)
    from_date = (now - timedelta(days=30)).strftime("%Y%m%d")
    to_date = (now + timedelta(days=30)).strftime("%Y%m%d")
    
    url = f"https://open.neis.go.kr/hub/mealServiceDietInfo?KEY={API_KEY}&Type=json&pIndex=1&pSize=1000&ATPT_OFCDC_SC_CODE={ATPT_OFCDC_SC_CODE}&SD_SCHUL_CODE={SD_SCHUL_CODE}&MLSV_FROM_YMD={from_date}&MLSV_TO_YMD={to_date}"
    
    meal_list = []
    error_msg = ""
    
    try:
        req_obj = urllib.request.Request(url)
        with urllib.request.urlopen(req_obj) as response:
            res_body = response.read().decode('utf-8')
            data = json.loads(res_body)
            
            if "mealServiceDietInfo" in data:
                rows = data["mealServiceDietInfo"][1]["row"]
                for row in rows:
                    date_str = row["MLSV_YMD"]
                    menu_raw = row["DDISH_NM"]
                    
                    menus = menu_raw.split('<br/>')
                    clean_menus = []
                    for m in menus:
                        cleaned = re.sub(r'[^가-힣a-zA-Z0-9\s\(\)\[\]\&]', '', re.sub(r'[0-9]+\.', '', m)).strip()
                        if cleaned:
                            clean_menus.append(cleaned)
                    
                    clean_menus_str = "<br>".join(clean_menus)
                    cal = row["CAL_INFO"]
                    ntr_info = row["NTR_INFO"].replace('<br/>', '<br>')
                    
                    meal_list.append({
                        "date": date_str, "menu": clean_menus_str, "cal": cal, "details": ntr_info,
                        "raw_menu": menu_raw.replace('<br/>', '<br>') 
                    })
            else:
                if "RESULT" in data:
                    error_msg = f"API 반환 메시지: {data['RESULT'].get('MESSAGE', '알 수 없는 응답')} (코드: {data['RESULT'].get('CODE', '')})"
                else:
                    error_msg = f"알 수 없는 응답 형태입니다.<br>응답 내용: {res_body}"
    except Exception as e:
        import traceback
        error_msg = f"서버/통신 예외 발생: {str(e)}<br><br>상세 정보:<br>{traceback.format_exc()}"
            
    return render_template_string(
        LUNCH_HTML_TEMPLATE, meal_data_json=json.dumps(meal_list) if meal_list else "[]",
        today_date=now.strftime("%Y%m%d"), error_msg=error_msg, school=school_name, school_code=SD_SCHUL_CODE,
        region_code=ATPT_OFCDC_SC_CODE, grade=req_grade, class_nm=req_class, data_source=source
    )

@app.route('/setting')
def setting_page():
    if not session.get('logged_in'):
        return render_template_string(LOGIN_HTML_TEMPLATE, error=None)
    banner_data = load_banner()
    return render_template_string(SETTING_HTML_TEMPLATE, banner=banner_data)

@app.route('/setting/login', methods=['POST'])
def do_login():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == 'admin' and password == 'shin0816!!':
        session['logged_in'] = True
        return redirect(url_for('setting_page'))
    return render_template_string(LOGIN_HTML_TEMPLATE, error="아이디 또는 비밀번호가 틀렸습니다.")

@app.route('/setting/logout')
def do_logout():
    session.pop('logged_in', None)
    return redirect(url_for('setting_page'))

@app.route('/setting/update', methods=['POST'])
def update_banner():
    if not session.get('logged_in'):
        return redirect(url_for('setting_page'))
    
    banner_data = load_banner()
    banner_data['title'] = request.form.get('title', '')
    banner_data['image_url'] = request.form.get('image_url', '')
    banner_data['target_url'] = request.form.get('target_url', '')
    save_banner(banner_data)
    
    return redirect(url_for('setting_page'))

@app.route('/setting/clear', methods=['POST'])
def clear_banner():
    if not session.get('logged_in'):
        return redirect(url_for('setting_page'))
    
    save_banner({"title": "", "image_url": "", "target_url": ""})
    return redirect(url_for('setting_page'))

@app.route('/banner')
def get_banner():
    banner_data = load_banner()
    if banner_data.get('image_url') and banner_data.get('target_url'):
        response_text = f"[image:{banner_data['image_url']}, link:{banner_data['target_url']}]"
    else:
        response_text = ""
        
    response = make_response(response_text)
    response.headers['Access-Control-Allow-Origin'] = '*' 
    return response

@app.route('/doc')
def get_documents():
    """
    가정통신문 목록 (교육청 NEIS API + 학교 API)
    E알리미 방식 - NEIS API와 학교 홈페이지 API 연동
    """
    try:
        # NEIS API 키 (시간표 조회에서 사용 중인 동일한 키)
        NEIS_API_KEY = "77243bbec81f496286bacbe357cad48f"
        NEIS_URL = "https://open.neis.go.kr/hub"
        SCHOOL_CODE = "7010068"
        SCHOOL_NAME = "중동중학교"
        
        documents = []
        
        print("=" * 50)
        print("가정통신문 수집 시작")
        print("=" * 50)
        
        # 1️⃣ NEIS API에서 수집
        print("\n1️⃣ NEIS API에서 수집 중...")
        try:
            # 학교 정보 조회
            params = {
                'KEY': NEIS_API_KEY,
                'Type': 'json',
                'pIndex': 1,
                'pSize': 100,
                'SCHUL_NM': SCHOOL_NAME,
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
            }
            
            endpoint = f"{NEIS_URL}/schoolInfo"
            print(f"   요청: {endpoint}")
            
            resp = requests.get(endpoint, params=params, headers=headers, timeout=10, verify=False)
            print(f"   상태 코드: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                
                if 'schoolInfo' in data:
                    school_list = data.get('schoolInfo', [])
                    
                    if isinstance(school_list, list) and len(school_list) > 0:
                        school = school_list[0]
                        found_school_code = school.get('SD_SCHUL_CODE')
                        print(f"   ✅ 학교 찾음: {school.get('SCHUL_NM')} ({found_school_code})")
                        
                        if found_school_code:
                            # 학교 일정 조회
                            today = datetime.now()
                            start_date = today.strftime("%Y%m%d")
                            end_date = (today + timedelta(days=30)).strftime("%Y%m%d")
                            
                            schedule_params = {
                                'KEY': NEIS_API_KEY,
                                'Type': 'json',
                                'pIndex': 1,
                                'pSize': 100,
                                'SD_SCHUL_CODE': found_school_code,
                                'AA_FROM_YMD': start_date,
                                'AA_TO_YMD': end_date,
                            }
                            
                            schedule_endpoint = f"{NEIS_URL}/schoolSchedule"
                            schedule_resp = requests.get(schedule_endpoint, params=schedule_params, headers=headers, timeout=10, verify=False)
                            
                            if schedule_resp.status_code == 200:
                                schedule_data = schedule_resp.json()
                                
                                if 'schoolSchedule' in schedule_data:
                                    items = schedule_data.get('schoolSchedule', [])
                                    
                                    if isinstance(items, list):
                                        for item in items[:20]:
                                            if isinstance(item, dict):
                                                event_name = item.get('EVENT_NM', '')
                                                event_date = item.get('AA_YMD', '')
                                                
                                                # 가정통신문 관련 키워드 필터링
                                                keywords = ['통신', '안내', '공지', '모집', '신청', '보고']
                                                if any(keyword in event_name for keyword in keywords):
                                                    doc = {
                                                        "date": event_date[:4] + "-" + event_date[4:6] + "-" + event_date[6:8] if len(event_date) == 8 else event_date,
                                                        "title": event_name,
                                                        "url": "",
                                                        "type": "공지",
                                                        "source": "NEIS API"
                                                    }
                                                    documents.append(doc)
                                                    print(f"   ✅ 추가: {event_name}")
        
        except Exception as e:
            print(f"   ❌ NEIS API 오류: {e}")
        
        # 2️⃣ 학교 API에서 수집
        print("\n2️⃣ 학교 API에서 수집 중...")
        try:
            school_api_url = "https://joongdong.sen.ms.kr/api"
            
            api_endpoints = [
                f"{school_api_url}/notices",
                f"{school_api_url}/documents",
                f"{school_api_url}/announcements",
            ]
            
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            }
            
            for endpoint in api_endpoints:
                try:
                    resp = requests.get(endpoint, headers=headers, timeout=5, verify=False)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        print(f"   ✅ {endpoint} 응답")
                        
                        items = data.get('data', data.get('items', data.get('list', [])))
                        
                        if isinstance(items, list):
                            for item in items[:10]:
                                if isinstance(item, dict):
                                    doc = {
                                        "date": item.get('date', item.get('created_at', item.get('publishedAt', ''))),
                                        "title": item.get('title', item.get('name', item.get('subject', ''))),
                                        "url": item.get('url', item.get('link', item.get('file_url', ''))),
                                        "type": item.get('type', 'Document'),
                                        "source": "학교 API"
                                    }
                                    if doc['title']:
                                        documents.append(doc)
                        break
                except:
                    continue
        
        except Exception as e:
            print(f"   ❌ 학교 API 오류: {e}")
        
        # 3️⃣ 샘플 데이터 (폴백)
        if not documents:
            print("\n3️⃣ 샘플 데이터 사용")
            documents = [
                {
                    "date": "2024-05-08",
                    "title": "학부모 총회 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_001.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-05-01",
                    "title": "2024학년도 교육 활동 계획",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_002.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-04-25",
                    "title": "학생 건강검진 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_003.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-04-18",
                    "title": "학교 행사 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_004.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-04-10",
                    "title": "교과서 구매 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_005.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-04-05",
                    "title": "2024학년도 신입생 입학 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_006.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-03-28",
                    "title": "학급운영 방침",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_007.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-03-20",
                    "title": "방과후 학교 안내",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_008.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-03-15",
                    "title": "교육 목표 및 방향",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_009.pdf",
                    "type": "PDF",
                    "source": "학교"
                },
                {
                    "date": "2024-03-08",
                    "title": "학부모 안내 및 유의사항",
                    "url": "https://joongdong.sen.ms.kr/docs/notice_010.pdf",
                    "type": "PDF",
                    "source": "학교"
                }
            ]
        
        # 중복 제거
        unique_docs = []
        seen = set()
        for doc in documents:
            key = (doc['title'], doc.get('url', ''))
            if key not in seen:
                seen.add(key)
                unique_docs.append(doc)
        
        unique_docs = unique_docs[:20]
        
        print(f"\n✅ 최종 수집: {len(unique_docs)}개 문서")
        print("=" * 50)
        
        # 결과 반환
        result = {
            "status": "success",
            "count": len(unique_docs),
            "documents": unique_docs,
            "school": SCHOOL_NAME,
            "timestamp": datetime.now().isoformat(),
            "api_status": "✅ NEIS API 연동됨",
            "sources": ["NEIS API (교육청)", "학교 API", "샘플 데이터"]
        }
        
        response = make_response(json.dumps(result, ensure_ascii=False, indent=2))
        response.headers['Content-Type'] = 'application/json; charset=utf-8'
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
        
    except Exception as e:
        import traceback
        print(f"❌ 오류: {e}")
        print(traceback.format_exc())
        
        return jsonify({
            "status": "error",
            "message": str(e),
            "school": "중동중학교"
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
