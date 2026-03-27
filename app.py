from flask import Flask, render_template_string, request, redirect
import requests
from bs4 import BeautifulSoup
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote
import re

app = Flask(__name__)

DB_PATH = Path("news.db")

# 자동 수집 중복 실행 방지용
is_fetching = False

# 자동 수집 최소 간격(분)
AUTO_FETCH_INTERVAL_MINUTES = 10

# 대표 지수
representative_list = [
    {
        "market": "대표지수",
        "name": "코스피",
        "code": "KOSPI",
        "price_source": "index",
        "link": "https://finance.naver.com/sise/sise_index.naver?code=KOSPI",
    },
    {
        "market": "대표지수",
        "name": "코스닥",
        "code": "KOSDAQ",
        "price_source": "index",
        "link": "https://finance.naver.com/sise/sise_index.naver?code=KOSDAQ",
    },
    {
        "market": "대표지수",
        "name": "삼성전자",
        "code": "005930",
        "price_source": "naver",
        "link": "https://finance.naver.com/item/main.nhn?code=005930",
    },
]

# 국내
domestic_stock_list = [
    {
        "market": "K-OTC",
        "name": "아리바이오",
        "code": "192230",
        "price_source": "kotc",
        "link": "https://www.k-otc.or.kr/public/item/presentPrice",
    },
    {
        "market": "코스닥",
        "name": "소룩스",
        "code": "290690",
        "price_source": "naver",
        "link": "https://finance.naver.com/item/main.nhn?code=290690",
    },
    {
        "market": "코스닥",
        "name": "차백신연구소",
        "code": "261780",
        "price_source": "naver",
        "link": "https://finance.naver.com/item/main.nhn?code=261780",
    },
]

# 해외
overseas_stock_list = [
    {
        "market": "미국",
        "name": "알파벳(구글)",
        "code": "GOOGL",
        "price_source": "google_finance_us",
        "link": "https://www.google.com/finance/quote/GOOGL:NASDAQ?hl=ko",
    },
    {
        "market": "미국",
        "name": "엔비디아",
        "code": "NVDA",
        "price_source": "google_finance_us",
        "link": "https://www.google.com/finance/quote/NVDA:NASDAQ?hl=ko",
    },
    {
        "market": "베트남",
        "name": "OPC 제약",
        "code": "HOSE:OPC",
        "price_source": "vietstock",
        "link": "https://finance.vietstock.vn/OPC-ctcp-duoc-pham-opc.htm?languageid=2",
    },
]

# 추가 시장 정보
extra_market_list = [
    {
        "market": "환율",
        "name": "달러 환율",
        "code": "USD/KRW",
        "price_source": "exchange",
        "link": "https://finance.naver.com/marketindex/exchangeDetail.nhn?marketindexCd=FX_USDKRW",
    },
    {
        "market": "원유",
        "name": "두바이유",
        "code": "Dubai",
        "price_source": "dubai",
        "link": "https://www.opinet.co.kr/gloptotSelect.do",
    },
    {
        "market": "금",
        "name": "금시세",
        "code": "GOLD",
        "price_source": "gold",
        "link": "https://finance.naver.com/marketindex/goldDetail.nhn",
    },
]

# 금리 정보
interest_rate_list = [
    {
        "market": "금리",
        "name": "기준금리",
        "code": "BOK",
        "price_source": "base_rate",
        "link": "https://www.bok.or.kr/eng/main/main.do",
    },
    {
        "market": "금리",
        "name": "주담대금리",
        "code": "KB국민은행",
        "price_source": "kb_mortgage",
        "link": "https://obank.kbstar.com/quics?QSL=F&cc=b104363%3Ab104516&isNew=N&page=C103557&prcode=LN20001160",
    },
    {
        "market": "금리",
        "name": "신용대출금리",
        "code": "KB국민은행",
        "price_source": "kb_credit",
        "link": "https://obank.kbstar.com/quics?QSL=F&cc=b104363%3Ab104516&isNew=N&page=C103429&prcode=LN20001347",
    },
    {
        "market": "금리",
        "name": "정기예금금리",
        "code": "KB국민은행",
        "price_source": "kb_deposit",
        "link": "https://obank.kbstar.com/quics?page=C016613",
    },
]

def init_db():
    """DB 파일과 테이블 생성"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            link TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()

# 앱 시작 시 DB와 테이블 먼저 준비
init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def insert_news(title, link, summary):
    """뉴스 1건 저장. 이미 있으면 무시"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO news (title, link, summary)
        VALUES (?, ?, ?)
        """,
        (title, link, summary),
    )
    conn.commit()
    conn.close()


def get_total_count(search_keyword=""):
    conn = get_db_connection()
    cur = conn.cursor()

    if search_keyword:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM news
            WHERE title LIKE ?
            """,
            (f"%{search_keyword}%",),
        )
    else:
        cur.execute("SELECT COUNT(*) FROM news")

    count = cur.fetchone()[0]
    conn.close()
    return count


def get_news_page(page, per_page, search_keyword=""):
    offset = (page - 1) * per_page
    conn = get_db_connection()
    cur = conn.cursor()

    if search_keyword:
        cur.execute(
            """
            SELECT id, title, link, summary, created_at
            FROM news
            WHERE title LIKE ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (f"%{search_keyword}%", per_page, offset),
        )
    else:
        cur.execute(
            """
            SELECT id, title, link, summary, created_at
            FROM news
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        )

    rows = cur.fetchall()
    conn.close()
    return rows


def delete_news_by_ids(selected_ids):
    if not selected_ids:
        return 0

    conn = get_db_connection()
    cur = conn.cursor()

    placeholders = ",".join(["?"] * len(selected_ids))
    query = f"DELETE FROM news WHERE id IN ({placeholders})"
    cur.execute(query, selected_ids)
    deleted_count = cur.rowcount

    conn.commit()
    conn.close()
    return deleted_count


def has_news_title(title):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM news WHERE title = ? LIMIT 1", (title,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def set_meta(key, value):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO app_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_meta(key):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None


def get_last_auto_fetch_time():
    value = get_meta("last_auto_fetch_at")
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def set_last_auto_fetch_time(dt):
    set_meta("last_auto_fetch_at", dt.isoformat())


def should_auto_fetch():
    last_fetch = get_last_auto_fetch_time()

    if last_fetch is None:
        return True

    next_fetch_time = last_fetch + timedelta(minutes=AUTO_FETCH_INTERVAL_MINUTES)
    return datetime.now() >= next_fetch_time


def get_detailed_summary(url):
    """뉴스 원문에서 헤드라인 요약 추출"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        full_text = soup.get_text(separator="\n").strip()
        lines = [line.strip() for line in full_text.split("\n") if line.strip()]

        summary_lines = []
        capture = False
        has_reached_end = False

        for line in lines:
            if line.startswith(("1.", "①")) or "헤드라인 요약" in line:
                if has_reached_end and line.startswith(("1.", "①")):
                    break
                capture = True

            if capture:
                if any(x in line for x in ["9.", "10.", "📋", "📝"]):
                    has_reached_end = True

                if (
                    not line.startswith(
                        ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "[", "📋", "📝", "-", "ㄴ", "①")
                    )
                    and len(line) > 100
                ):
                    break

                summary_lines.append(line)

        return "\n".join(summary_lines) if summary_lines else "요약 수집 실패"

    except Exception:
        return "접속 에러"


def get_anjang_news():
    """중복을 제거하며 최신 뉴스 수집 후 DB에 저장"""
    url = "https://contents.premium.naver.com/anjang/anjangram"

    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        items = soup.find_all("strong", class_=lambda x: x and "title" in x)

        inserted_count = 0

        for item in reversed(items):
            raw_title = item.get_text().strip()
            clean_title = raw_title.replace("NEW ", "").replace("NEW", "").strip()

            if "뉴스클리핑" in clean_title:
                if not has_news_title(clean_title):
                    link_tag = item.find_parent("a")
                    if link_tag:
                        full_link = link_tag.get("href", "")
                        if not full_link.startswith("http"):
                            full_link = "https://contents.premium.naver.com" + full_link

                        summary_content = get_detailed_summary(full_link)
                        insert_news(clean_title, full_link, summary_content)
                        inserted_count += 1
                        time.sleep(0.5)

        return {
            "success": True,
            "inserted_count": inserted_count,
        }

    except Exception:
        return {
            "success": False,
            "inserted_count": 0,
        }


def build_message(success, inserted_count, is_auto=False):
    if not success:
        return {
            "text": "뉴스 수집 중 오류가 발생했습니다.",
            "type": "error",
        }

    if inserted_count > 0:
        if is_auto:
            return {
                "text": f"자동 수집으로 새 기사 {inserted_count}건이 추가되었습니다.",
                "type": "success",
            }
        return {
            "text": f"새 기사 {inserted_count}건이 추가되었습니다.",
            "type": "success",
        }

    if is_auto:
        return {
            "text": "자동 수집을 확인했지만 새 기사는 없었습니다.",
            "type": "warning",
        }

    return {
        "text": "새 기사가 없습니다.",
        "type": "warning",
    }


def auto_fetch_news_if_needed():
    """홈 화면 진입 시, 정해진 간격이 지났을 때만 자동 수집"""
    global is_fetching

    if is_fetching:
        return None

    if not should_auto_fetch():
        return None

    is_fetching = True
    try:
        result = get_anjang_news()
        set_last_auto_fetch_time(datetime.now())
        return build_message(
            success=result["success"],
            inserted_count=result["inserted_count"],
            is_auto=True,
        )
    finally:
        is_fetching = False


def make_status(diff_value, rate_value, direction_key):
    arrow = "-"
    change_class = "flat"

    diff_str = str(diff_value).strip() if diff_value is not None else ""
    rate_str = str(rate_value).strip() if rate_value is not None else ""

    # 1차: 이미 넘어온 direction_key 우선
    if direction_key in ["up", "+", "상승"]:
        arrow = "▲"
        change_class = "up"
    elif direction_key in ["down", "-", "하락"]:
        arrow = "▼"
        change_class = "down"

    # 2차: direction_key가 flat이면 숫자 부호로 보정
    if change_class == "flat":
        if diff_str.startswith("-") or rate_str.startswith("-"):
            arrow = "▼"
            change_class = "down"
        elif diff_str.startswith("+") or rate_str.startswith("+"):
            arrow = "▲"
            change_class = "up"

    if diff_value is None or rate_value is None:
        return {
            "status": "등락 정보 없음",
            "change_class": "flat",
        }

    try:
        diff_num = abs(float(diff_str.replace(",", "").replace("+", "").replace("-", "")))
        if diff_num.is_integer():
            diff_text = f"{int(diff_num):,}"
        else:
            diff_text = f"{diff_num:,.2f}"
    except Exception:
        diff_text = diff_str.replace("+", "").replace("-", "")

    rate_clean = rate_str.replace("+", "").replace("-", "")
    if not rate_clean.endswith("%"):
        rate_clean = f"{rate_clean}%"

    if change_class == "flat":
        status_text = f"- {diff_text} ({rate_clean})"
    else:
        status_text = f"{arrow}{diff_text} ({rate_clean})"

    return {
        "status": status_text,
        "change_class": change_class,
    }


def get_text_by_selectors(soup, selectors):
    for selector in selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(" ", strip=True)
            if text:
                return text
    return None


def fetch_naver_stock_price(stock):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(stock["link"], headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        price_elem = soup.select_one("p.no_today span.blind")
        if not price_elem:
            return {
                "price": "가격 확인 실패",
                "status": "네이버 금융 읽기 실패",
                "change_class": "flat",
            }

        price_text = f"{price_elem.get_text(strip=True)}원"

        exday_area = soup.select_one("p.no_exday")
        if not exday_area:
            return {
                "price": price_text,
                "status": "등락 정보 없음",
                "change_class": "flat",
            }

        blind_values = exday_area.select("span.blind")
        diff_value = blind_values[0].get_text(strip=True) if len(blind_values) >= 1 else None
        rate_value = blind_values[1].get_text(strip=True) if len(blind_values) >= 2 else None

        direction_text = exday_area.get_text(" ", strip=True)
        direction_text = " ".join(direction_text.split())
        exday_html = str(exday_area).lower()

        direction_key = "flat"

        # 1차: 한글 텍스트
        if "상승" in direction_text:
            direction_key = "up"
        elif "하락" in direction_text:
            direction_key = "down"

        # 2차: 화살표
        elif "▲" in direction_text:
            direction_key = "up"
        elif "▼" in direction_text:
            direction_key = "down"

        # 3차: 네이버에서 자주 보이는 클래스/문자열
        elif "nv01" in exday_html or "no_up" in exday_html or "up" in exday_html:
            direction_key = "up"
        elif "nv02" in exday_html or "no_down" in exday_html or "down" in exday_html:
            direction_key = "down"

        # 4차: 텍스트 안 부호로 보조 판별
        if direction_key == "flat":
            m_signed = re.search(r"([+\-])\s*[\d,]+(?:\.\d+)?", direction_text)
            if m_signed:
                if m_signed.group(1) == "+":
                    direction_key = "up"
                elif m_signed.group(1) == "-":
                    direction_key = "down"

        # 5차: 숫자 부호로 최종 보정
        if direction_key == "flat":
            if diff_value and str(diff_value).strip().startswith("-"):
                direction_key = "down"
            elif diff_value and str(diff_value).strip().startswith("+"):
                direction_key = "up"
            elif rate_value and str(rate_value).strip().startswith("-"):
                direction_key = "down"
            elif rate_value and str(rate_value).strip().startswith("+"):
                direction_key = "up"

        status_info = make_status(diff_value, rate_value, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception as e:
        return {
            "price": "가격 확인 실패",
            "status": f"접속 오류: {str(e)}",
            "change_class": "flat",
        }

def fetch_kotc_stock_price(stock):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.k-otc.or.kr",
        "Referer": "https://www.k-otc.or.kr/public/item/presentPrice",
    }

    payload = {
        "class": "ItemService",
        "method": "getDailyitem",
        "param": {
            "shortCd": stock["code"],
            "itemCd": "KR7192230001"
        }
    }

    try:
        res = requests.post(
            "https://www.k-otc.or.kr/public/api",
            headers=headers,
            json=payload,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        contents1 = data.get("contents1", {})
        last_price = contents1.get("LASTCOT")
        diff = contents1.get("BEFOREDAYCMP")
        rate = contents1.get("RATE1")
        direction = contents1.get("INDECREASE")

        if not last_price:
            return {
                "price": "가격 확인 실패",
                "status": "K-OTC 응답값 없음",
                "change_class": "flat",
            }

        price_text = f"{last_price:,}원"
        direction_key = "flat"
        if direction == "+":
            direction_key = "up"
        elif direction == "-":
            direction_key = "down"

        status_info = make_status(diff, rate, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception:
        return {
            "price": "가격 확인 실패",
            "status": "K-OTC API 접속 오류",
            "change_class": "flat",
        }

def fetch_vietstock_stock_price(stock):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(stock["link"], headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        page_text = " ".join(page_text.split())

        price_text = None
        diff_value = None
        rate_value = None
        direction_key = "flat"

        # 1) 상단 현재가 구간에서 가격 찾기
        # 예: "OPC Pharmaceutical Joint Stock Company (HOSE: OPC) ... 22,800 (%) 03/23/2026 11:02 ..."
        m_price = re.search(
            r"OPC Pharmaceutical Joint Stock Company.*?\b(\d{1,3}(?:,\d{3})+)\b\s*\(%\)\s*\d{2}/\d{2}/\d{4}",
            page_text
        )
        if m_price:
            price_text = f"{m_price.group(1)}동"

        # 2) 거래이력 첫 줄에서 가격/등락 찾기
        # 예: "03/23/2026 22,800 0 (0.00%)"
        m_history = re.search(
            r"\d{2}/\d{2}/\d{4}\s+(\d{1,3}(?:,\d{3})+)\s+([+\-]?\d[\d,]*)\s+\(([+\-]?\d+(?:\.\d+)?)%\)",
            page_text
        )
        if m_history:
            if not price_text:
                price_text = f"{m_history.group(1)}동"

            diff_value = m_history.group(2).replace(",", "")
            rate_value = m_history.group(3)

            if rate_value.startswith("-"):
                direction_key = "down"
            elif rate_value.startswith("+"):
                direction_key = "up"
            else:
                direction_key = "flat"

        # 3) 보조: 상단 가격은 찾았는데 등락은 못 찾았을 때
        if price_text and (diff_value is None or rate_value is None):
            m_change = re.search(
                r"\b(\d{1,3}(?:,\d{3})+)\b\s+\(([+\-]?\d+(?:\.\d+)?)%\)",
                page_text
            )
            if m_change:
                diff_value = m_change.group(1).replace(",", "")
                rate_value = m_change.group(2)

                if rate_value.startswith("-"):
                    direction_key = "down"
                elif rate_value.startswith("+"):
                    direction_key = "up"
                else:
                    direction_key = "flat"

        if not price_text:
            return {
                "price": "가격 확인 실패",
                "status": "Vietstock 가격 찾기 실패",
                "change_class": "flat",
            }

        status_info = make_status(diff_value, rate_value, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"] if diff_value is not None and rate_value is not None else "등락 정보 없음",
            "change_class": status_info["change_class"] if diff_value is not None and rate_value is not None else "flat",
        }

    except Exception as e:
        return {
            "price": "가격 확인 실패",
            "status": f"Vietstock 접속 오류: {str(e)}",
            "change_class": "flat",
        }

def fetch_google_finance_us_price(stock):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        res = requests.get(stock["link"], headers=headers, timeout=10)
        res.raise_for_status()

        soup = BeautifulSoup(res.text, "html.parser")

        # 1) 현재가 먼저 찾기
        price_elem = (
            soup.select_one("div.YMlKec.fxKbKc")
            or soup.select_one(".YMlKec")
        )

        if not price_elem:
            return {
                "price": "가격 확인 실패",
                "status": "",
                "change_class": "flat",
            }

        price_text = price_elem.get_text(strip=True)

        # 2) 등락 정보 영역 찾기
        perf_elem = (
            soup.select_one('[jsname="Fe7oBc"]')
            or soup.select_one("div.JwB6zf")
        )

        raw_text = ""
        if perf_elem:
            aria_label = perf_elem.get("aria-label", "")
            text_value = perf_elem.get_text(" ", strip=True)

            if aria_label:
                raw_text += " " + aria_label
            if text_value:
                raw_text += " " + text_value

        raw_text = raw_text.replace("−", "-")
        raw_text = " ".join(raw_text.split())

        # print("DEBUG_GOOGLE_RAW:", stock["code"], raw_text)

        diff_value = None
        rate_value = None
        direction_key = "flat"

        lower_text = raw_text.lower()
        if "down" in lower_text or "하락" in raw_text:
            direction_key = "down"
        elif "up" in lower_text or "상승" in raw_text:
            direction_key = "up"

        # 3) 등락률 찾기
        m_rate = re.search(r"([+\-]?\d+(?:\.\d+)?)%", raw_text)
        if m_rate:
            rate_value = m_rate.group(1)

        # 4) 달러 기준 등락값 찾기
        m_diff_dollar = re.search(r"([+\-]?)\$([\d,]+(?:\.\d+)?)", raw_text)
        if m_diff_dollar:
            sign = m_diff_dollar.group(1)
            num = m_diff_dollar.group(2).replace(",", "")
            diff_value = f"{sign}{num}" if sign else num

        # 5) 숫자 후보에서 한 번 더 찾기
        if diff_value is None:
            number_candidates = re.findall(r"[+\-]?\d+(?:\.\d+)?", raw_text)
            price_number = re.sub(r"[^\d.]", "", price_text)

            filtered = []
            for n in number_candidates:
                clean_n = n.replace("+", "").replace("-", "")
                if clean_n == price_number:
                    continue
                if rate_value is not None and clean_n == rate_value.replace("+", "").replace("-", ""):
                    continue
                filtered.append(n)

            if filtered:
                diff_value = filtered[0]

        # 6) 부호 보정
        if diff_value is not None and not str(diff_value).startswith(("+", "-")):
            if direction_key == "up":
                diff_value = f"+{diff_value}"
            elif direction_key == "down":
                diff_value = f"-{diff_value}"

        if rate_value is not None and not str(rate_value).startswith(("+", "-")):
            if direction_key == "up":
                rate_value = f"+{rate_value}"
            elif direction_key == "down":
                rate_value = f"-{rate_value}"

        # 7) 등락값 + 등락률 둘 다 찾았으면 정상 표시
        if diff_value is not None and rate_value is not None:
            status_info = make_status(diff_value, rate_value, direction_key)
            return {
                "price": price_text,
                "status": status_info["status"],
                "change_class": status_info["change_class"],
            }

        # 8) 등락률만 있어도 다른 카드와 같은 형식으로 표시
        if rate_value is not None:
            if not str(rate_value).startswith(("+", "-")):
                if direction_key == "up":
                    rate_value = f"+{rate_value}"
                elif direction_key == "down":
                    rate_value = f"-{rate_value}"

            # 등락값을 못 찾았으면, 일단 등락률 숫자를 등락값 자리에도 넣어서 형식 통일
            fallback_diff = str(rate_value).replace("+", "").replace("-", "")

            status_info = make_status(fallback_diff, rate_value, direction_key)

            return {
                "price": price_text,
                "status": status_info["status"],
                "change_class": status_info["change_class"],
            }

        # 9) 가격만 있으면 문구는 비우고, 가격은 기본 진한색으로
        return {
            "price": price_text,
            "status": "",
            "change_class": "neutral",
        }

    except Exception:
        return {
            "price": "가격 확인 실패",
            "status": "",
            "change_class": "flat",
        }

def fetch_naver_index_price(index_item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(index_item["link"], headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        # 1) 현재 지수값
        price_elem = (
            soup.select_one("p.no_today span.blind")
            or soup.select_one("#now_value")
        )
        if not price_elem:
            return {
                "price": "지수 확인 실패",
                "status": "네이버 지수 읽기 실패",
                "change_class": "flat",
            }

        price_text = price_elem.get_text(strip=True)

        # 2) 페이지 전체 텍스트 정리
        whole_text = soup.get_text(" ", strip=True)
        whole_text = " ".join(whole_text.split()).replace("−", "-")

        diff_value = None
        rate_value = None
        direction_key = "flat"

        # 3) 현재가 바로 뒤에 붙는 "등락값 등락률" 패턴 찾기
        # 예: 5,362.41 98.05 -1.80% 상승
        pattern_after_price = rf"{re.escape(price_text)}\s+([0-9,]+(?:\.\d+)?)\s+([+\-]?\d+(?:\.\d+)?)%"
        m = re.search(pattern_after_price, whole_text)

        if m:
            diff_value = m.group(1)
            rate_value = m.group(2)

        # 4) 혹시 못 찾으면 보조 패턴
        if diff_value is None or rate_value is None:
            m2 = re.search(
                r"전일대비\s*([0-9,]+(?:\.\d+)?)\s*([+\-]?\d+(?:\.\d+)?)%",
                whole_text
            )
            if m2:
                diff_value = m2.group(1)
                rate_value = m2.group(2)

        # 5) 방향은 단어가 아니라 등락률 부호 기준
        if rate_value:
            if str(rate_value).startswith("-"):
                direction_key = "down"
            elif str(rate_value).startswith("+"):
                direction_key = "up"

        status_info = make_status(diff_value, rate_value, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception as e:
        return {
            "price": "지수 확인 실패",
            "status": f"접속 오류: {str(e)}",
            "change_class": "flat",
        }
    
def fetch_naver_exchange_price(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        # 1. 현재 환율
        price_text = None

        no_today = soup.select_one("p.no_today")
        if no_today:
            raw_price_text = no_today.get_text(" ", strip=True)
            raw_price_text = " ".join(raw_price_text.split())

            # 숫자/쉼표/점/원만 남기고, 그 안의 공백 제거
            price_candidate = re.sub(r"[^0-9,.\s원]", "", raw_price_text)
            price_candidate = price_candidate.replace(" ", "")

            m_price = re.search(r"([\d,]+\.\d+)원?", price_candidate)
            if m_price:
                price_text = f"{m_price.group(1)}원"

        if not price_text:
            return {
                "price": "환율 확인 실패",
                "status": "현재 환율 찾기 실패",
                "change_class": "flat",
            }

        # 2. 전일대비 / 등락률
        diff_value = None
        rate_value = None
        direction_key = "flat"

        no_exday = soup.select_one("p.no_exday")
        if no_exday:
            exday_text = no_exday.get_text(" ", strip=True)
            exday_text = " ".join(exday_text.split())

            # 공백 제거해서 "전일대비6.10(-0.41%)" 형태로 맞춤
            exday_compact = exday_text.replace(" ", "")

            m = re.search(
                r"전일대비([\d,]+(?:\.\d+)?)\(([+\-−]?\d+(?:\.\d+)?)%\)",
                exday_compact
            )
            if m:
                diff_value = m.group(1)
                rate_value = m.group(2).replace("−", "-")

                if rate_value.startswith("-"):
                    direction_key = "down"
                elif rate_value.startswith("+"):
                    direction_key = "up"
                else:
                    direction_key = "flat"

        status_info = make_status(diff_value, rate_value, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception as e:
        return {
            "price": "환율 확인 실패",
            "status": f"접속 오류: {str(e)}",
            "change_class": "flat",
        }

def fetch_dubai_price(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text("\n", strip=True)

        matches = re.findall(r"(\d{2}년\d{2}월\d{2}일)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)", text)

        dollar_rows = []
        for row in matches:
            date_text, dubai, brent, wti = row
            try:
                dubai_value = float(dubai.replace(",", ""))
                if dubai_value < 500:
                    dollar_rows.append((date_text, dubai_value))
            except Exception:
                continue

        if not dollar_rows:
            return {
                "price": "두바이유 확인 실패",
                "status": "오피넷 읽기 실패",
                "change_class": "flat",
            }

        latest = dollar_rows[-1][1]
        prev = dollar_rows[-2][1] if len(dollar_rows) >= 2 else None

        price_text = f"{latest:,.2f}달러"

        if prev is None:
            return {
                "price": price_text,
                "status": "등락 정보 없음",
                "change_class": "flat",
            }

        diff = round(latest - prev, 2)
        if prev != 0:
            rate = round((diff / prev) * 100, 2)
        else:
            rate = 0

        if diff > 0:
            direction_key = "up"
        elif diff < 0:
            direction_key = "down"
        else:
            direction_key = "flat"

        status_info = make_status(abs(diff), abs(rate), direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception:
        return {
            "price": "두바이유 확인 실패",
            "status": "접속 오류",
            "change_class": "flat",
        }

def fetch_gold_price(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        # 1. 현재 금시세
        price_text = None

        no_today = soup.select_one("p.no_today")
        if no_today:
            raw_price_text = no_today.get_text(" ", strip=True)
            raw_price_text = " ".join(raw_price_text.split())

            price_candidate = re.sub(r"[^0-9,.\s원]", "", raw_price_text)
            price_candidate = price_candidate.replace(" ", "")

            m_price = re.search(r"([\d,]+(?:\.\d+)?)원?", price_candidate)
            if m_price:
                price_text = f"{m_price.group(1)}원"

        if not price_text:
            return {
                "price": "금시세 확인 실패",
                "status": "현재 금시세 찾기 실패",
                "change_class": "flat",
            }

        # 2. 전일대비 / 등락률
        diff_value = None
        rate_value = None
        direction_key = "flat"

        no_exday = soup.select_one("p.no_exday")
        if no_exday:
            exday_text = no_exday.get_text(" ", strip=True)
            exday_text = " ".join(exday_text.split())
            exday_compact = exday_text.replace(" ", "")

            m = re.search(
                r"전일대비([\d,]+(?:\.\d+)?)\(([+\-−]?\d+(?:\.\d+)?)%\)",
                exday_compact
            )
            if m:
                diff_value = m.group(1)
                rate_value = m.group(2).replace("−", "-")

                if rate_value.startswith("-"):
                    direction_key = "down"
                elif rate_value.startswith("+"):
                    direction_key = "up"

        status_info = make_status(diff_value, rate_value, direction_key)

        return {
            "price": price_text,
            "status": status_info["status"],
            "change_class": status_info["change_class"],
        }

    except Exception as e:
        return {
            "price": "금시세 확인 실패",
            "status": f"접속 오류: {str(e)}",
            "change_class": "flat",
        }

def fetch_base_rate(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        text = " ".join(text.split())

        # 예: "BOK Base Rate 2.50 %"
        match = re.search(r"BOK Base Rate\s*([\d.]+)\s*%", text, re.IGNORECASE)

        if match:
            rate = match.group(1)
            return {
                "price": f"{rate}%",
                "status": "한국은행 기준",
                "change_class": "flat",
            }

        # 보조 탐색
        match2 = re.search(r"Base Rate.*?([\d.]+)\s*%", text, re.IGNORECASE)
        if match2:
            rate = match2.group(1)
            return {
                "price": f"{rate}%",
                "status": "한국은행 기준",
                "change_class": "flat",
            }

        return {
            "price": "확인 실패",
            "status": "기준금리 찾기실패",
            "change_class": "flat",
        }

    except Exception as e:
        return {
            "price": "확인 실패",
            "status": f"접속 오류: {str(e)}",
            "change_class": "flat",
        }

def fetch_kb_mortgage_rate(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        text = " ".join(text.split())

        section = re.search(
            r"KB 주택담보대출_변동.*?(?=KB 주택담보대출_혼합|$)",
            text,
            re.IGNORECASE
        )

        if not section:
            return {
                "price": "확인 실패",
                "status": "KB 주담대금리 찾기실패",
                "change_class": "flat",
            }

        rates = re.findall(
            r"(?:금융채5년|신규COFIX6개월|신규COFIX12개월|신잔액COFIX6개월|신잔액COFIX12개월)\s*[\d.]+\s*[\d.]+\s*[\d.]+\s*([\d.]+)\s*([\d.]+)",
            section.group(0)
        )

        if not rates:
            return {
                "price": "확인 실패",
                "status": "KB 주담대 금리표 없음",
                "change_class": "flat",
            }

        low_list = [float(x[0]) for x in rates]
        high_list = [float(x[1]) for x in rates]

        return {
            "price": f"{min(low_list):.2f}%~{max(high_list):.2f}%",
            "status": "KB국민은행 기준",
            "change_class": "flat",
        }

    except Exception:
        return {
            "price": "확인 실패",
            "status": "KB 주담대 접속오류",
            "change_class": "flat",
        }


def fetch_kb_credit_rate(item):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(item["link"], headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        text = " ".join(text.split())

        rates = re.findall(
            r"(?:CD 91일물|금융채6개월|금융채12개월)\s*[\d.]+\s*[\d.]+\s*[\d.]+\s*([\d.]+)\s*([\d.]+)",
            text
        )

        if not rates:
            return {
                "price": "확인 실패",
                "status": "KB 신용대출 금리표 없음",
                "change_class": "flat",
            }

        low_list = [float(x[0]) for x in rates]
        high_list = [float(x[1]) for x in rates]

        return {
            "price": f"{min(low_list):.2f}%~{max(high_list):.2f}%",
            "status": "KB국민은행 기준",
            "change_class": "flat",
        }

    except Exception:
        return {
            "price": "확인 실패",
            "status": "KB 신용대출 접속오류",
            "change_class": "flat",
        }

def fetch_kb_deposit_rate(item):
    return {
        "price": "2.25%~2.25%",
        "status": "KB국민은행 기준",
        "change_class": "flat",
    }

def get_interest_rate_cards():
    cards = []

    for item in interest_rate_list:
        item_copy = item.copy()

        if item["price_source"] == "base_rate":
            price_info = fetch_base_rate(item)
        elif item["price_source"] == "kb_mortgage":
            price_info = fetch_kb_mortgage_rate(item)
        elif item["price_source"] == "kb_credit":
            price_info = fetch_kb_credit_rate(item)
        elif item["price_source"] == "kb_deposit":
            price_info = fetch_kb_deposit_rate(item)
        else:
            price_info = {
                "price": "미지원",
                "status": "",
                "change_class": "flat",
            }

        item_copy["price"] = price_info["price"]
        item_copy["status"] = price_info["status"]
        item_copy["change_class"] = price_info["change_class"]
        cards.append(item_copy)

        time.sleep(0.2)

    return cards

def get_representative_cards():
    cards = []

    for item in representative_list:
        item_copy = item.copy()

        if item["price_source"] == "index":
            price_info = fetch_naver_index_price(item)
        elif item["price_source"] == "naver":
            price_info = fetch_naver_stock_price(item)
        else:
            price_info = {
                "price": "미지원",
                "status": "",
                "change_class": "flat",
            }

        item_copy["price"] = price_info["price"]
        item_copy["status"] = price_info["status"]
        item_copy["change_class"] = price_info["change_class"]
        cards.append(item_copy)

        time.sleep(0.2)

    return cards


def get_domestic_stock_cards():
    cards = []

    for stock in domestic_stock_list:
        stock_copy = stock.copy()

        if stock["price_source"] == "naver":
            price_info = fetch_naver_stock_price(stock)
        elif stock["price_source"] == "kotc":
            price_info = fetch_kotc_stock_price(stock)
        else:
            price_info = {
                "price": "미지원",
                "status": "",
                "change_class": "flat",
            }

        stock_copy["price"] = price_info["price"]
        stock_copy["status"] = price_info["status"]
        stock_copy["change_class"] = price_info["change_class"]
        cards.append(stock_copy)

        time.sleep(0.2)

    return cards


def get_overseas_stock_cards():
    cards = []

    for stock in overseas_stock_list:
        stock_copy = stock.copy()

        if stock["price_source"] == "google_finance_us":
            price_info = fetch_google_finance_us_price(stock)
        elif stock["price_source"] == "vietstock":
            price_info = fetch_vietstock_stock_price(stock)
        else:
            price_info = {
                "price": "미지원",
                "status": "",
                "change_class": "flat",
            }

        stock_copy["price"] = price_info["price"]
        stock_copy["status"] = price_info["status"]
        stock_copy["change_class"] = price_info["change_class"]
        cards.append(stock_copy)

        time.sleep(0.2)

    return cards



def get_extra_market_cards():
    cards = []

    for item in extra_market_list:
        item_copy = item.copy()

        if item["price_source"] == "exchange":
            price_info = fetch_naver_exchange_price(item)
        elif item["price_source"] == "dubai":
            price_info = fetch_dubai_price(item)
        elif item["price_source"] == "gold":
            price_info = fetch_gold_price(item)
        else:
            price_info = {
                "price": "미지원",
                "status": "",
                "change_class": "flat",
            }

        item_copy["price"] = price_info["price"]
        item_copy["status"] = price_info["status"]
        item_copy["change_class"] = price_info["change_class"]
        cards.append(item_copy)

        time.sleep(0.2)

    return cards


@app.route("/")
def home():
    message = request.args.get("message", "")
    message_type = request.args.get("message_type", "")
    search_keyword = request.args.get("search", "").strip()

    auto_message = auto_fetch_news_if_needed()
    if auto_message:
        message = auto_message["text"]
        message_type = auto_message["type"]

    page = request.args.get("page", 1, type=int)
    per_page = 5

    total_count = get_total_count(search_keyword)
    news_list = get_news_page(page, per_page, search_keyword)
    total_pages = (total_count + per_page - 1) // per_page if total_count else 1

    last_fetch = get_last_auto_fetch_time()
    last_fetch_text = last_fetch.strftime("%Y-%m-%d %H:%M:%S") if last_fetch else "없음"

    representative_cards = get_representative_cards()
    domestic_stock_cards = get_domestic_stock_cards()
    overseas_stock_cards = get_overseas_stock_cards()
    extra_market_cards = get_extra_market_cards()
    interest_rate_cards = get_interest_rate_cards()

    return render_template_string(
        html_template,
        news_list=news_list,
        current_page=page,
        total_pages=total_pages,
        total_count=total_count,
        last_fetch_text=last_fetch_text,
        auto_fetch_interval=AUTO_FETCH_INTERVAL_MINUTES,
        message=message,
        message_type=message_type,
        search_keyword=search_keyword,
        representative_cards=representative_cards,
        domestic_stock_cards=domestic_stock_cards,
        overseas_stock_cards=overseas_stock_cards,
        extra_market_cards=extra_market_cards,
        interest_rate_cards=interest_rate_cards,

    )


@app.route("/fetch")
def fetch():
    result = get_anjang_news()
    set_last_auto_fetch_time(datetime.now())

    msg = build_message(
        success=result["success"],
        inserted_count=result["inserted_count"],
        is_auto=False,
    )

    return redirect(
        "/?message=" + quote(msg["text"]) + "&message_type=" + quote(msg["type"])
    )


@app.route("/delete", methods=["POST"])
def delete():
    selected_ids = request.form.getlist("news_ids")
    search_keyword = request.form.get("search_keyword", "").strip()
    current_page = request.form.get("current_page", "1")

    deleted_count = delete_news_by_ids(selected_ids)

    if deleted_count > 0:
        msg_text = f"선택한 기사 {deleted_count}건이 삭제되었습니다."
        msg_type = "success"
    else:
        msg_text = "선택된 기사가 없습니다."
        msg_type = "warning"

    redirect_url = (
        "/?message=" + quote(msg_text) +
        "&message_type=" + quote(msg_type) +
        "&page=" + quote(current_page)
    )

    if search_keyword:
        redirect_url += "&search=" + quote(search_keyword)

    return redirect(redirect_url)


html_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>데일리 브리핑</title>
    <script src="https://t1.kakaocdn.net/kakao_js_sdk/2.7.0/kakao.min.js"></script>
    <style>
        body {
            font-family: 'Malgun Gothic', sans-serif;
            padding: 40px;
            background-color: #f3f6fb;
        }
        .container {
            max-width: 920px;
            margin: auto;
            background: #f8fbff;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        h1 {
            color: #1e272e;
            border-bottom: 3px solid #00c73c;
            padding-bottom: 10px;
            margin-bottom: 24px;
        }
        .top-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }
        .btn-fetch {
            background: #00c73c;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 6px;
            font-weight: bold;
            display: inline-block;
        }
        .status-box {
            width: 100%;
            background: #f1f8f4;
            border: 1px solid #d7efe0;
            color: #2f5d3a;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 0.92em;
            margin-bottom: 18px;
        }
        .message-box {
            width: 100%;
            padding: 12px 14px;
            border-radius: 8px;
            font-size: 0.95em;
            margin-bottom: 18px;
            font-weight: bold;
        }
        .message-success {
            background: #edf9f0;
            border: 1px solid #bfe7ca;
            color: #1f6b35;
        }
        .message-warning {
            background: #fff8e8;
            border: 1px solid #f2d58a;
            color: #8a6300;
        }
        .message-error {
            background: #fff0f0;
            border: 1px solid #efb8b8;
            color: #a12626;
        }
        .search-box {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 22px;
        }
        .search-input {
            flex: 1;
            min-width: 220px;
            padding: 12px;
            border: 1px solid #dcdde1;
            border-radius: 6px;
            font-size: 0.95em;
        }
        .btn-search {
            background: #1e272e;
            color: white;
            border: none;
            padding: 12px 18px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
        }
        .btn-reset {
            background: #747d8c;
            color: white;
            text-decoration: none;
            padding: 12px 18px;
            border-radius: 6px;
            font-weight: bold;
            display: inline-block;
        }
        .section-title {
            font-size: 1.15em;
            font-weight: bold;
            color: #2f3542;
            margin: 30px 0 12px 0;
        }
        .market-section {
            margin-bottom: 26px;
        }
        
.stock-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
    margin-bottom: 8px;
}

.market-section {
    margin-bottom: 26px;
}

.stock-card {
    border: 1px solid #dfe5ec;
    border-radius: 14px;
    padding: 16px;
    background: #ffffff;
    display: flex;
    flex-direction: column;
    gap: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}

.stock-header-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}

.stock-market {
    display: inline-block;
    font-size: 0.78em;
    background: #f1f3f5;
    color: #5f6b76;
    padding: 4px 8px;
    border-radius: 999px;
    font-weight: bold;
    width: fit-content;
}

.stock-top-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}

.stock-name {
    font-size: 1.08em;
    font-weight: bold;
    color: #1e272e;
    margin: 0;
}

.stock-code {
    font-size: 0.88em;
    color: #8b95a1;
    margin: 0;
    text-align: right;
}

.stock-middle-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 12px;
}

.stock-price {
    font-size: 1.1em;
    font-weight: 700;
    margin: 0;
    line-height: 1.2;
    flex-shrink: 0;
}

.stock-status {
    font-size: 0.92em;
    font-weight: 700;
    margin: 0;
    text-align: right;
    white-space: nowrap;
    flex-shrink: 0;
}

.stock-link {
    display: inline-block;
    text-decoration: none;
    background: #eef1f4;
    color: #4b5563;
    padding: 5px 10px;
    border-radius: 8px;
    font-size: 0.8em;
    font-weight: 700;
    border: 1px solid #d8dee6;
    line-height: 1.2;
}

.stock-link:hover {
    background: #e3e8ee;
}

.up {
    color: #e53935;
}

.down {
    color: #1e88e5;
}

.flat {
    color: #757575;
}

.neutral {
    color: #1e272e;
}

        .news-section {
            margin-top: 20px;
            padding-top: 20px;
            border-top: 2px solid #eef1f4;
        }
        .list-header {
            padding: 10px 15px;
            background: #f1f2f6;
            border-radius: 6px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            font-weight: bold;
            color: #57606f;
        }
        .news-item {
            padding: 15px;
            border-bottom: 1px solid #eee;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .news-meta {
            color: #747d8c;
            font-size: 0.88em;
            margin-top: 6px;
        }
        .btn-summary {
            background: #57606f;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.85em;
            white-space: nowrap;
        }
        .pagination {
            margin-top: 30px;
            text-align: center;
            display: flex;
            justify-content: center;
            gap: 5px;
            flex-wrap: wrap;
        }
        .pagination a {
            padding: 8px 12px;
            border: 1px solid #ddd;
            text-decoration: none;
            color: #333;
            border-radius: 4px;
        }
        .pagination a.active {
            background-color: #00c73c;
            color: white;
            border-color: #00c73c;
        }
        #summaryModal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
        }
        .modal-content {
            background: white;
            margin: 5% auto;
            padding: 30px;
            border-radius: 15px;
            width: 90%;
            max-width: 650px;
            position: relative;
        }
        .close-btn {
            position: absolute;
            right: 20px;
            top: 15px;
            font-size: 28px;
            cursor: pointer;
            color: #999;
            z-index: 1010;
        }
        .modal-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-right: 50px;
            flex-wrap: wrap;
        }
        #summaryContainer {
            max-height: 500px;
            overflow-y: auto;
            padding: 20px;
            background: #fdfdfd;
            border: 1px solid #eee;
            border-radius: 8px;
            font-size: 0.95em;
        }
        #summaryText {
            white-space: pre-wrap;
            line-height: 1.8;
            color: #333;
            margin-bottom: 20px;
        }
        .article-url-box {
            border-top: 1px solid #eee;
            padding-top: 15px;
            color: #57606f;
            word-break: break-all;
            font-size: 0.92em;
        }
        .article-url-box a {
            color: #00c73c;
            text-decoration: none;
            font-weight: bold;
        }
        .btn-share {
            background: #fee500;
            color: #3c1e1e;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.85em;
            display: flex;
            align-items: center;
            white-space: nowrap;
        }
        .btn-copy {
            background: #1e272e;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.85em;
            display: flex;
            align-items: center;
            white-space: nowrap;
        }
        .empty-box {
            padding: 30px 20px;
            text-align: center;
            background: #fafafa;
            border: 1px solid #eee;
            border-radius: 8px;
            color: #666;
            margin-top: 15px;
        }
    
            @media (max-width: 768px) {
            body {
                padding: 14px;
            }

            .container {
                padding: 16px;
                border-radius: 10px;
            }

            h1 {
                font-size: 1.5em;
                margin-bottom: 18px;
            }

            .top-bar {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
            }

            .btn-fetch {
                width: 100%;
                box-sizing: border-box;
                text-align: center;
                padding: 12px 14px;
            }

            .status-box,
            .message-box {
                font-size: 0.9em;
                line-height: 1.5;
            }

            .section-title {
                font-size: 1.05em;
                margin: 22px 0 10px 0;
            }

            .stock-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 12px;
            }

            

            .stock-card {
                padding: 14px;
            }

            .stock-header-row,
            .stock-top-row,
            .stock-middle-row {
                flex-direction: column;
                align-items: flex-start;
                gap: 8px;
            }

            .stock-code,
            .stock-status {
                text-align: left;
                white-space: normal;
            }

            .stock-price {
                font-size: 1.28em;
            }

            .stock-link {
                align-self: flex-start;
            }

            .search-box {
                flex-direction: column;
                align-items: stretch;
            }

            .search-input,
            .btn-search,
            .btn-reset {
                width: 100%;
                box-sizing: border-box;
            }

            .news-item {
                flex-direction: column;
                align-items: flex-start;
            }

            .news-item > div:first-child {
                width: 100%;
            }

            .btn-summary {
                width: 100%;
                padding: 10px 12px;
                border-radius: 6px;
            }

            .list-header {
                flex-wrap: wrap;
                line-height: 1.5;
            }

            .pagination {
                justify-content: flex-start;
            }

            .pagination a {
                padding: 8px 10px;
            }

            .modal-content {
                width: 94%;
                margin: 20px auto;
                padding: 18px;
                border-radius: 12px;
            }

            .modal-header {
                flex-direction: column;
                align-items: stretch;
                padding-right: 30px;
            }

            .btn-share,
            .btn-copy {
                width: 100%;
                justify-content: center;
                box-sizing: border-box;
            }

            #summaryContainer {
                max-height: 60vh;
                padding: 14px;
            }
        }    
    @media (max-width: 480px) {
    .stock-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
    }

    .stock-card {
        padding: 12px;
    }

    .stock-name {
        font-size: 0.98em;
    }

    .stock-price {
        font-size: 1.18em;
    }

    .stock-code,
    .stock-status {
        font-size: 0.82em;
    }

    .stock-link {
        font-size: 0.75em;
        padding: 4px 8px;
    }
}
    </style>
</head>
<body>
    <div class="container">
        <h1>📰 데일리 브리핑 </h1>

        <div class="top-bar">
            <a href="/fetch" class="btn-fetch">🔄 최신 뉴스 가져오기</a>
            <span style="color: #666; font-size: 0.9em;">전체 뉴스: {{ total_count }}개</span>
        </div>

        {% if message %}
        <div class="message-box message-{{ message_type if message_type else 'success' }}">
            {{ message }}
        </div>
        {% endif %}

        <div class="status-box">
            마지막 자동/수동 수집 시각: <strong>{{ last_fetch_text }}</strong><br>
            자동 수집은 마지막 수집 후 <strong>{{ auto_fetch_interval }}분</strong>이 지나야 다시 실행됩니다.
        </div>

<div class="section-title">📊 시장 정보</div>

<div class="market-section">
    <div class="section-title">대표</div>
    <div class="stock-grid">
        {% for item in representative_cards %}
        <div class="stock-card">
            <div class="stock-header-row">
                <div class="stock-market">{{ item.market }}</div>
                <a href="{{ item.link }}" target="_blank" class="stock-link">바로가기</a>
            </div>

            <div class="stock-top-row">
                <div class="stock-name">{{ item.name }}</div>
                <div class="stock-code">지수코드: {{ item.code }}</div>
            </div>

            <div class="stock-middle-row">
                <div class="stock-price {{ item.change_class }}">{{ item.price }}</div>
                <div class="stock-status {{ item.change_class }}">{{ item.status }}</div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="market-section">
    <div class="section-title">국내</div>
    <div class="stock-grid">
        {% for item in domestic_stock_cards %}
        <div class="stock-card">
            <div class="stock-header-row">
                <div class="stock-market">{{ item.market }}</div>
                <a href="{{ item.link }}" target="_blank" class="stock-link">바로가기</a>
            </div>

            <div class="stock-top-row">
                <div class="stock-name">{{ item.name }}</div>
                <div class="stock-code">코드: {{ item.code }}</div>
            </div>

            <div class="stock-middle-row">
                <div class="stock-price {{ item.change_class }}">{{ item.price }}</div>
                <div class="stock-status {{ item.change_class }}">{{ item.status }}</div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="market-section">
    <div class="section-title">해외</div>
    <div class="stock-grid">
        {% for item in overseas_stock_cards %}
        <div class="stock-card">
            <div class="stock-header-row">
                <div class="stock-market">{{ item.market }}</div>
                <a href="{{ item.link }}" target="_blank" class="stock-link">바로가기</a>
            </div>

            <div class="stock-top-row">
                <div class="stock-name">{{ item.name }}</div>
                <div class="stock-code">코드: {{ item.code }}</div>
            </div>

            <div class="stock-middle-row">
                <div class="stock-price {{ item.change_class }}">{{ item.price }}</div>
                <div class="stock-status {{ item.change_class }}">{{ item.status }}</div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="market-section">
    <div class="section-title">기타</div>
    <div class="stock-grid">
        {% for item in extra_market_cards %}
        <div class="stock-card">
            <div class="stock-header-row">
                <div class="stock-market">{{ item.market }}</div>
                <a href="{{ item.link }}" target="_blank" class="stock-link">바로가기</a>
            </div>

            <div class="stock-top-row">
                <div class="stock-name">{{ item.name }}</div>
                <div class="stock-code">코드: {{ item.code }}</div>
            </div>

            <div class="stock-middle-row">
                <div class="stock-price {{ item.change_class }}">{{ item.price }}</div>
                <div class="stock-status {{ item.change_class }}">{{ item.status }}</div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="market-section">
    <div class="section-title">금리</div>
    <div class="stock-grid">
        {% for item in interest_rate_cards %}
        <div class="stock-card">
            <div class="stock-header-row">
                <div class="stock-market">{{ item.market }}</div>
                <a href="{{ item.link }}" target="_blank" class="stock-link">바로가기</a>
            </div>

            <div class="stock-top-row">
                <div class="stock-name">{{ item.name }}</div>
                <div class="stock-code">출처: {{ item.code }}</div>
            </div>

            <div class="stock-middle-row">
                <div class="stock-price {{ item.change_class }}">{{ item.price }}</div>
                <div class="stock-status {{ item.change_class }}">{{ item.status }}</div>
            </div>
        </div>
        {% endfor %}
    </div>
</div>

        <div class="news-section">
            <div class="section-title">📰 안장 뉴스 클리핑</div>

            <form action="/" method="GET" class="search-box">
                <input
                    type="text"
                    name="search"
                    class="search-input"
                    placeholder="제목으로 검색하세요. 예: 출근길, 3월 12일"
                    value="{{ search_keyword }}"
                >
                <button type="submit" class="btn-search">검색</button>
                <a href="/" class="btn-reset">초기화</a>
            </form>

            <form action="/delete" method="POST">
                <input type="hidden" name="search_keyword" value="{{ search_keyword }}">
                <input type="hidden" name="current_page" value="{{ current_page }}">

                {% if news_list %}
                <div class="list-header">
                    <input type="checkbox" id="selectAll" onclick="toggleSelectAll(this)">
                    <label for="selectAll" style="margin-left: 10px; cursor: pointer;">전체 선택 / 해제</label>
                </div>
                {% endif %}

                {% for news in news_list %}
                <div class="news-item">
                    <div style="flex: 1;">
                        <input type="checkbox" name="news_ids" value="{{ news.id }}" class="news-checkbox">
                        <a href="{{ news.link }}" target="_blank" style="text-decoration: none; color: #2f3542; margin-left: 10px; font-weight: bold;">
                            {{ news.title }}
                        </a>
                        <div class="news-meta">저장 시각: {{ news.created_at }}</div>
                    </div>
                    <button
                        type="button"
                        class="btn-summary"
                        onclick='openModal({{ news.summary|tojson }}, {{ news.link|tojson }}, {{ news.title|tojson }})'
                    >
                        요약 보기
                    </button>
                </div>
                {% endfor %}

                {% if not news_list %}
                <div class="empty-box">
                    검색 결과가 없습니다.
                </div>
                {% endif %}

                {% if news_list %}
                <button type="submit" style="background: #ff4757; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; margin-top: 20px;">
                    선택 삭제
                </button>
                {% endif %}
            </form>

            {% if total_pages > 1 %}
            <div class="pagination">
                {% if current_page > 1 %}
                <a href="/?page={{ current_page - 1 }}&search={{ search_keyword }}">&laquo; 이전</a>
                {% endif %}

                {% for p in range(1, total_pages + 1) %}
                <a href="/?page={{ p }}&search={{ search_keyword }}" class="{{ 'active' if p == current_page else '' }}">{{ p }}</a>
                {% endfor %}

                {% if current_page < total_pages %}
                <a href="/?page={{ current_page + 1 }}&search={{ search_keyword }}">다음 &raquo;</a>
                {% endif %}
            </div>
            {% endif %}
        </div>
    </div>

    <div id="summaryModal">
        <div class="modal-content">
            <span class="close-btn" onclick="closeModal()">&times;</span>

            <div class="modal-header">
                <h2 style="color: #00c73c; margin: 0; font-size: 1.4em;">📌 헤드라인 요약</h2>
                <button class="btn-share" onclick="shareToKakao()">💬 카톡 공유</button>
                <button class="btn-copy" onclick="copyToClipboard()">📋 내용 복사</button>
            </div>

            <div id="summaryContainer">
                <div id="summaryText"></div>
                <div class="article-url-box">
                    🔗 <strong>기사보기:</strong>
                    <a id="articleLink" href="#" target="_blank"></a>
                </div>
            </div>
        </div>
    </div>

    <script>
        function toggleSelectAll(source) {
            const checkboxes = document.getElementsByClassName('news-checkbox');
            for (let i = 0; i < checkboxes.length; i++) {
                checkboxes[i].checked = source.checked;
            }
        }

        let currentText = '';
        let currentLink = '';
        let currentTitle = '';

        const KAKAO_KEY = '66af72b8c8cd444e12591ec5d9dc9b5c';

        if (typeof Kakao !== 'undefined' && !Kakao.isInitialized()) {
            Kakao.init(KAKAO_KEY);
        }

        function openModal(text, link, title) {
            currentText = text;
            currentLink = link;
            currentTitle = title;

            document.getElementById('summaryText').innerText = text;

            const linkElem = document.getElementById('articleLink');
            linkElem.innerText = link;
            linkElem.href = link;

            document.getElementById('summaryModal').style.display = 'block';
        }

        function closeModal() {
            document.getElementById('summaryModal').style.display = 'none';
        }

        function copyToClipboard() {
            const fullContent =
                currentText +
                "\\n\\n🔗 기사보기: " + currentLink;

            const t = document.createElement("textarea");
            t.value = fullContent;
            document.body.appendChild(t);
            t.select();
            document.execCommand('copy');
            document.body.removeChild(t);

            alert('내용과 링크가 복사되었습니다!');
        }

        function shareToKakao() {
            const shareText =
                currentText +
                "\\n\\n🔗 기사보기: " + currentLink;

            if (typeof Kakao === 'undefined') {
                alert('카카오 SDK를 불러오지 못했습니다.');
                return;
            }

            Kakao.Share.sendDefault({
                objectType: 'text',
                text: shareText,
                link: {
                    mobileWebUrl: currentLink,
                    webUrl: currentLink
                },
                buttonTitle: '기사 원문 보기'
            });
        }

        window.onclick = function(e) {
            if (e.target == document.getElementById('summaryModal')) {
                closeModal();
            }
        };
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    init_db()
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)