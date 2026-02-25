# 뉴스 클리핑 MVP (Streamlit)

URL의 HTML을 읽어 뉴스 제목/번호 목록/기사 링크를 추출하고, SQLite에 중복 없이 저장한 뒤 대시보드에서 확인하는 간단한 앱입니다.

## 기능
- URL 입력창 제공
- 버튼 클릭 시 HTML 요청
- 페이지에서 다음 정보 추출
  - 페이지 제목
  - `1. ...`, `2. ...`, `3. ...` 형태의 번호 목록 텍스트
  - 각 항목에 포함된 기사 링크
- SQLite(`news_clipping.db`)에 저장
- 중복 항목 자동 제외 (`UNIQUE(source_url, numbered_item, article_link)`)
- 저장 기록을 하단 리스트로 표시
- 직전 저장에서 새로 추가된 항목은 `NEW` 표시

## 파일 구조
```text
news-clipping-mvp/
├── app.py              # Streamlit 앱 본체
├── requirements.txt    # 필요한 패키지 목록
├── README.md           # 실행/설명 문서
└── news_clipping.db    # 실행 시 자동 생성되는 SQLite DB
```

## 설치
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## 실행 방법
```bash
streamlit run app.py
```

실행 후 브라우저에서 표시되는 로컬 주소(기본 `http://localhost:8501`)로 접속하세요.

## 사용 방법
1. 상단 입력창에 뉴스/목록 URL 입력
2. **가져오고 저장하기** 버튼 클릭
3. 추출 및 저장 결과 메시지 확인
4. 하단 **저장된 클리핑 기록**에서 전체 기록과 `NEW` 배지 확인

## 참고
- 번호 목록은 `li` 또는 `p` 태그 내에서 `숫자 + 점 + 공백` 패턴(`^\d+\.\s+`)을 기준으로 찾습니다.
- 링크는 상대경로일 경우 입력 URL 기준 절대경로로 변환합니다.
