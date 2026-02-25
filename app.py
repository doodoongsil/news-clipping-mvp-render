import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup

DB_PATH = Path("news_clipping.db")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ----------------------------
# DB
# ----------------------------
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clipping_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL,
                page_title TEXT NOT NULL,
                numbered_item TEXT NOT NULL,
                article_link TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_url, numbered_item, article_link)
            )
            """
        )


def save_records(source_url: str, page_title: str, rows: List[Tuple[str, str]]) -> List[int]:
    """
    rows: [(numbered_item, article_link), ...]
    returns inserted row ids
    """
    inserted_ids: List[int] = []
    now = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(DB_PATH) as conn:
        for numbered_item, article_link in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO clipping_records
                (source_url, page_title, numbered_item, article_link, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_url, page_title, numbered_item, article_link, now),
            )
            if cur.rowcount == 1:
                inserted_ids.append(cur.lastrowid)

    return inserted_ids


def load_records(limit: int = 200) -> List[Tuple]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, source_url, page_title, numbered_item, article_link, created_at
            FROM clipping_records
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [tuple(r) for r in rows]


# ----------------------------
# Fetch / Parse
# ----------------------------
def fetch_html(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def extract_page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title or "(no title)"


def extract_numbered_items_and_links(html: str, base_url: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    1. ... 2. ... 형태의 번호와 링크가 같이 있는 페이지를 최대한 보수적으로 추출
    """
    page_title = extract_page_title(html)
    soup = BeautifulSoup(html, "html.parser")

    rows: List[Tuple[str, str]] = []
    seen = set()

    # a 태그 주변 텍스트에 "1." 같은 패턴이 있는지 검사
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#"):
            continue

        full = urljoin(base_url, href)
        text_around = a.get_text(" ", strip=True)

        parent_text = ""
        if a.parent:
            parent_text = a.parent.get_text(" ", strip=True)

        blob = f"{parent_text} {text_around}".strip()
        m = re.search(r"(^|\s)(\d{1,3})\.\s+", blob)
        if not m:
            continue

        num = m.group(2)
        key = (num, full)
        if key in seen:
            continue
        seen.add(key)

        rows.append((num, full))

    return page_title, rows


def _parse_korean_date_to_iso(s: str) -> Optional[str]:
    """
    "26년 2월 25일" -> 2026-02-25
    "2026.02.25" -> 2026-02-25
    """
    s = s.strip()

    m = re.search(r"(\d{2,4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if m:
        y = int(m.group(1))
        if y < 100:  # 26년 같은 케이스
            y = 2000 + y
        mo = int(m.group(2))
        d = int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    m2 = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", s)
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"

    return None


def extract_naver_premium_posts(html: str, base_url: str) -> List[Tuple[str, str, str]]:
    """
    네이버 프리미엄(anjangram) 목록 페이지에서 contents 링크를 모아:
    [(date_iso, title, link), ...]
    date_iso를 못 찾으면 created_at 기준으로라도 저장할 수 있게 'unknown'으로 둠
    """
    soup = BeautifulSoup(html, "html.parser")

    posts: List[Tuple[str, str, str]] = []
    seen_links = set()

    # contents 링크 후보 수집
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/contents/" not in href:
            continue

        link = urljoin(base_url, href)

        # 같은 링크 중복 제거
        if link in seen_links:
            continue
        seen_links.add(link)

        # 제목 후보: a 텍스트 / aria-label / 주변 텍스트
        title = a.get_text(" ", strip=True) or a.get("aria-label") or ""
        if not title:
            # 카드 전체 텍스트에서 첫 줄-ish를 제목으로
            parent = a.find_parent()
            if parent:
                title = parent.get_text(" ", strip=True)

        title = (title or "").strip()
        if len(title) > 120:
            title = title[:120].strip()

        # 날짜 후보: 카드 주변 텍스트에서 찾기
        date_iso = None
        container = a.find_parent()
        if container:
            blob = container.get_text(" ", strip=True)
            date_iso = _parse_korean_date_to_iso(blob)

        # 그래도 없으면 페이지 전체에서 가장 가까운 날짜 패턴(보수적)
        if not date_iso:
            date_iso = _parse_korean_date_to_iso(soup.get_text(" ", strip=True))

        if not date_iso:
            date_iso = "unknown"

        posts.append((date_iso, title or "(no title)", link))

    return posts


# ----------------------------
# UI
# ----------------------------
def main() -> None:
    init_db()

    if "new_ids" not in st.session_state:
        st.session_state.new_ids = set()

    st.title("📰 뉴스 클리핑 대시보드")
    st.caption("URL의 HTML을 가져와 제목/번호목록/기사 링크를 추출하고 SQLite에 저장합니다.")

    url = st.text_input("뉴스/목록 페이지 URL", placeholder="https://contents.premium.naver.com/anjang/anjangram")

if st.button("가져오고 저장하기", type="primary"):
    if not url:
        st.warning("먼저 URL을 입력해 주세요.")
    else:
        try:
            html = fetch_html(url)

            page_title, rows = extract_numbered_items_and_links(html, url)

            if rows:
                new_ids = save_records(url, page_title, rows)
                st.session_state.new_ids = set(new_ids)

                st.success(
                    f"총 {len(rows)}개 추출, 신규 {len(new_ids)}개 저장 완료 "
                    f"(중복 {len(rows) - len(new_ids)}개)"
                )

            else:
                # 2️⃣ 네이버 프리미엄 목록 파서
                naver_posts = extract_naver_premium_posts(html, url)

                if naver_posts:
                    new_ids_all = []
                    for date_iso, title, link in naver_posts:
                        new_ids = save_records(url, title, [(date_iso, link)])
                        new_ids_all.extend(new_ids)

                    st.session_state.new_ids = set(new_ids_all)

                    st.success(
                        f"네이버 프리미엄 목록에서 {len(naver_posts)}개 처리 완료 "
                        f"(신규 {len(set(new_ids_all))}개)"
                    )
                else:
                    st.info("번호목록/네이버 프리미엄 목록을 모두 찾지 못했습니다.")

        except requests.RequestException as exc:
            st.error(f"HTML 요청 실패: {exc}")

        except Exception as exc:
            st.error(f"처리 중 오류: {exc}")
    st.divider()
    st.subheader("저장된 클리핑 기록")

    records = load_records()
    if not records:
        st.write("저장된 기록이 없습니다.")
        return

    # 표로 보기 좋게
    for _id, source_url, page_title, numbered_item, article_link, created_at in records:
        is_new = _id in st.session_state.new_ids
        badge = "🆕 NEW" if is_new else ""
        st.markdown(
            f"- {badge} **[{page_title}]**  \n"
            f"  - 날짜/번호: `{numbered_item}`  \n"
            f"  - 링크: {article_link}  \n"
            f"  - 저장시각: {created_at}"
        )


if __name__ == "__main__":
    main()
