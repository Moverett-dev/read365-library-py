# read365-library-py

독서로에서 학교도서관의 소장 도서와 대출 상태를 검색합니다.

> 독서로에서 공식적으로 제공한 라이브러리가 아닌 비공식 Python 도구입니다.

## 설치

```bash
pip install requests
```

## 도서 검색

학교의 교육청 코드와 NEIS 학교 코드를 이용해 도서를 검색합니다.

```python
from read365_library import make_session, search_books

with make_session() as session:
    books, total_count, total_page = search_books(
        session=session,
        keyword="코스모스",
        prov_code="N10",
        neis_code="N100002733",
        page=1,
        display=10,
    )

for book in books:
    print(book.title)
```

반환 예시

```json
{
  "total_count": 5,
  "total_page": 1,
  "books": [
    {
      "book_key": "123456",
      "species_key": "654321",
      "title": "코스모스",
      "author": "칼 세이건",
      "publisher": "사이언스북스",
      "pub_year": "2006",
      "isbn": "9788983711892",
      "reg_no": "EM000012345",
      "call_no": "443.1-세68ㅋ",
      "location_name": "종합자료실",
      "page_count": 719,
      "category": "자연과학",
      "kdc": "400 / 자연과학",
      "cover_url": "",
      "loan_status": "상태 확인 안 함",
      "return_plan_date": "",
      "reservation_count": 0,
      "reservation_available": false,
      "state_error": ""
    }
  ]
}
```

## 대출 상태 조회

검색된 도서의 대출 여부, 반납 예정일, 예약 인원을 조회합니다.

```python
from read365_library import make_session, search_books, add_states

PROV_CODE = "N10"
NEIS_CODE = "N100002733"

with make_session() as session:
    books, total_count, total_page = search_books(
        session=session,
        keyword="코스모스",
        prov_code=PROV_CODE,
        neis_code=NEIS_CODE,
    )

add_states(
    books,
    prov_code=PROV_CODE,
    neis_code=NEIS_CODE,
)

for book in books:
    print(book.title, book.loan_status)
```

조회되는 상태 정보

```json
{
  "loan_status": "대출가능",
  "return_plan_date": "",
  "reservation_count": 0,
  "reservation_available": false,
  "location_name": "종합자료실"
}
```
