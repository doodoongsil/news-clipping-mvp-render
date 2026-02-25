import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import requests
import streamlit as st
from bs4 import BeautifulSoup

DB_PATH = Path("news_clipping.db")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
        conn.commit()


def fetch_html(url: str) -> str:
    response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


def extract_numbered_items_and_links(html: str, base_url: str) -> Tuple[str, List[Tuple[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else "(제목 없음)"

    numbered_pattern = re.compile(r"^\s*\d+\.\s+.+")
    extracted: List[Tuple[str, str]] = []
    seen = set()

    for tag in soup.find_all(["li", "p"]):
        text = tag.get_text(" ", strip=True)
        if not numbered_pattern.match(text):
            continue

        link_tag = tag.find("a", href=True)
        if not link_tag:
            continue

        link = requests.compat.urljoin(base_url, link_tag["href"])
        if not link.startswith(("http://", "https://")):
            continue

        key = (text, link)
        if key in seen:
            continue
        seen.add(key)
        extracted.append(key)

    return title, extracted


def save_records(source_url: str, page_title: str, rows: List[Tuple[str, str]]) -> List[int]:
    inserted_ids: List[int] = []

    with sqlite3.connect(DB_PATH) as conn:
        with closing(conn.cursor()) as cursor:
            for numbered_item, article_link in rows:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO clipping_records
                    (source_url, page_title, numbered_item, article_link, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        source_url,
                        page_title,
                        numbered_item,
                        article_link,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_ids.append(cursor.lastrowid)
        conn.commit()

    return inserted_ids


def load_records() -> List[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        records = conn.execute(
            """
            SELECT id, source_url, page_title, numbered_item, article_link, created_at
            FROM clipping_records
            ORDER BY id DESC
            """
        ).fetchall()
    return records


def main() -> None:
    st.set_page_config(page_title="뉴스 클리핑 대시보드", page_icon="📰", layout="wide")

    init_db()
    if "new_ids" not in st.session_state:
        st.session_state.new_ids = set()

    st.title("📰 뉴스 클리핑 대시보드")
    st.caption("URL의 HTML을 가져와 제목, 번호 목록, 기사 링크를 추출하고 SQLite에 저장합니다.")

    url = st.text_input("뉴스/목록 페이지 URL", placeholder="https://example.com/news")

    if st.button("가져오고 저장하기", type="primary"):
        if not url:
            st.warning("먼저 URL을 입력해 주세요.")
        else:
            try:
                html = fetch_html(url)
                page_title, rows = extract_numbered_items_and_links(html, url)

                if not rows:
                    st.info("번호 목록(예: 1. ... 2. ...) + 링크 조합을 찾지 못했습니다.")
                else:
                    new_ids = save_records(url, page_title, rows)
                    st.session_state.new_ids = set(new_ids)

                    st.success(
                        f"총 {len(rows)}개 추출, 신규 {len(new_ids)}개 저장 완료 (중복 {len(rows) - len(new_ids)}개 제외)"
                    )
            except requests.RequestException as exc:
                st.error(f"HTML 요청 실패: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"처리 중 오류: {exc}")

    st.divider()
    st.subheader("저장된 클리핑 기록")

    records = load_records()
    if not records:
        st.write("저장된 기록이 없습니다.")
        return

    for record in records:
        is_new = record["id"] in st.session_state.new_ids
        new_badge = " 🔥 **NEW**" if is_new else ""

        st.markdown(f"- **{record['page_title']}**{new_badge}")
        st.markdown(f"  - 항목: {record['numbered_item']}")
        st.markdown(f"  - 링크: [{record['article_link']}]({record['article_link']})")
        st.caption(f"원본 URL: {record['source_url']} | 저장 시각: {record['created_at']}")


if __name__ == "__main__":
    main()
