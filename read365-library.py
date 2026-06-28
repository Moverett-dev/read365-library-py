from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SEARCH_URL = "https://read365.edunet.net/alpasq/api/search"
STATE_URL = "https://read365.edunet.net/alpasq/api/search/book/state"

DEFAULT_PROV_CODE = "N10"
DEFAULT_NEIS_CODE = "N100002733"
DEFAULT_SCHOOL_NAME = "내포중학교"


class Read365Error(RuntimeError):
    pass


@dataclass
class Book:
    book_key: str
    species_key: str
    title: str
    author: str
    publisher: str
    pub_year: str
    isbn: str
    reg_no: str
    call_no: str
    location_name: str
    page_count: int | None
    category: str
    kdc: str

    cover_url: str = ""
    loan_status: str = "상태 확인 안 함"
    return_plan_date: str = ""
    reservation_count: int = 0
    reservation_available: bool = False
    state_error: str = ""


def make_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )

    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://read365.edunet.net",
            "Referer": "https://read365.edunet.net/",
        }
    )
    return session


def check_api_response(body: dict[str, Any]) -> dict[str, Any]:
    if body.get("status") != "OK":
        message = body.get("message") or "알 수 없는 API 오류"
        raise Read365Error(message)

    data = body.get("data")
    if data is None:
        raise Read365Error("응답에 data가 없습니다.")

    return data


def search_books(
    session: requests.Session,
    keyword: str,
    prov_code: str = DEFAULT_PROV_CODE,
    neis_code: str = DEFAULT_NEIS_CODE,
    page: int = 1,
    display: int = 10,
) -> tuple[list[Book], int, int]:
    payload = {
        "searchKeyword": keyword,
        "provCode": prov_code,
        "neisCode": [neis_code],
        "page": str(page),
        "display": str(display),
        "sort": "SCORE",
        "order": "ASC",
        "coverYn": "N",
    }

    response = session.post(
        SEARCH_URL,
        json=payload,
        timeout=(5, 20),
    )

    # 서버 구현이 JSON 대신 폼 전송을 요구하도록 바뀐 경우를 위한 보조 처리
    if response.status_code in (400, 415):
        form_payload = payload.copy()
        form_payload["neisCode"] = neis_code
        response = session.post(
            SEARCH_URL,
            data=form_payload,
            timeout=(5, 20),
        )

    response.raise_for_status()

    try:
        data = check_api_response(response.json())
    except ValueError as exc:
        raise Read365Error(
            f"검색 API가 JSON이 아닌 응답을 반환했습니다: {response.text[:200]}"
        ) from exc

    book_list = data.get("bookList") or []
    total_count = int(data.get("allTotalCount") or data.get("totalCount") or 0)
    total_page = int(data.get("totalPage") or 0)

    books: list[Book] = []

    for item in book_list:
        kdc_info = item.get("kdcInfo") or {}
        category_info = item.get("categoryInfo") or {}

        category_parts = [
            category_info.get("ldesc", ""),
            category_info.get("mdesc", ""),
            category_info.get("sdesc", ""),
        ]
        category = " > ".join(part for part in category_parts if part)

        kdc_parts = [
            kdc_info.get("lcode", ""),
            kdc_info.get("ldesc", ""),
            kdc_info.get("scode", ""),
            kdc_info.get("sdesc", ""),
        ]
        kdc = " / ".join(part for part in kdc_parts if part)

        books.append(
            Book(
                book_key=str(item.get("bookKey", "")),
                species_key=str(item.get("speciesKey", "")),
                title=str(item.get("title", "")),
                author=str(item.get("author", "")),
                publisher=str(item.get("publisher", "")),
                pub_year=str(item.get("pubYear", "")),
                isbn=str(item.get("isbn", "")),
                reg_no=str(item.get("regNo", "")),
                call_no=str(item.get("callNo", "")),
                location_name=str(item.get("locationName", "")),
                page_count=item.get("page"),
                category=category,
                kdc=kdc,
                cover_url=str(item.get("coverUrl", "")),
            )
        )

    return books, total_count, total_page


def get_book_state(
    session: requests.Session,
    book: Book,
    prov_code: str,
    neis_code: str,
) -> Book:
    try:
        response = session.get(
            STATE_URL,
            params={
                "bookKey": book.book_key,
                "provCode": prov_code,
                "neisCode": neis_code,
            },
            timeout=(5, 15),
        )
        response.raise_for_status()

        data = check_api_response(response.json())

        book.cover_url = str(data.get("coverUrl") or book.cover_url)
        book.loan_status = str(data.get("status") or "상태 알 수 없음")
        book.return_plan_date = str(data.get("returnPlanDate") or "")
        book.reservation_count = int(data.get("rsvtCount") or 0)
        book.reservation_available = data.get("rsvtYn") == "Y"
        book.location_name = str(
            data.get("locationName") or book.location_name
        )

    except (requests.RequestException, ValueError, Read365Error) as exc:
        book.loan_status = "상태 조회 실패"
        book.state_error = str(exc)

    return book


def add_states(
    books: list[Book],
    prov_code: str,
    neis_code: str,
    max_workers: int = 5,
) -> None:
    if not books:
        return

    # requests.Session 하나를 여러 스레드가 동시에 쓰지 않도록
    # 각 작업마다 별도 세션을 생성한다.
    def worker(book: Book) -> Book:
        with make_session() as session:
            return get_book_state(session, book, prov_code, neis_code)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, book) for book in books]

        for future in as_completed(futures):
            future.result()


def print_books(
    books: list[Book],
    total_count: int,
    page: int,
    total_page: int,
    school_name: str,
) -> None:
    print()
    print(
        f"[{school_name}] 검색 결과: 총 {total_count}권 "
        f"(현재 {page}/{max(total_page, 1)}페이지)"
    )
    print("=" * 72)

    if not books:
        print("검색 결과가 없습니다.")
        return

    for index, book in enumerate(books, start=1):
        print(f"{index}. {book.title}")
        print(f"   저자      : {book.author or '-'}")
        print(f"   출판사    : {book.publisher or '-'} ({book.pub_year or '-'})")
        print(f"   대출 상태 : {book.loan_status}")

        if book.return_plan_date:
            print(f"   반납 예정 : {book.return_plan_date}")

        print(
            "   예약      : "
            + (
                f"가능 / 현재 {book.reservation_count}명"
                if book.reservation_available
                else f"불가 / 현재 {book.reservation_count}명"
            )
        )
        print(f"   청구기호  : {book.call_no or '-'}")
        print(f"   등록번호  : {book.reg_no or '-'}")
        print(f"   위치      : {book.location_name or '-'}")
        print(f"   ISBN      : {book.isbn or '-'}")

        if book.category:
            print(f"   분류      : {book.category}")

        if book.cover_url:
            print(f"   표지      : {book.cover_url}")

        if book.state_error:
            print(f"   상태 오류 : {book.state_error}")

        print(f"   bookKey   : {book.book_key}")
        print("-" * 72)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="독서로 학교도서관 소장 도서 및 대출 상태 검색"
    )
    parser.add_argument(
        "keyword",
        nargs="?",
        help="검색어. 생략하면 실행 후 입력받습니다.",
    )
    parser.add_argument("--prov-code", default=DEFAULT_PROV_CODE)
    parser.add_argument("--neis-code", default=DEFAULT_NEIS_CODE)
    parser.add_argument("--school-name", default=DEFAULT_SCHOOL_NAME)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="대출 상태 조회를 생략합니다.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="화면용 목록 대신 JSON으로 출력합니다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    keyword = args.keyword
    if not keyword:
        keyword = input("검색할 책 제목 또는 저자: ").strip()

    if not keyword:
        print("검색어를 입력해야 합니다.", file=sys.stderr)
        return 2

    if args.page < 1:
        print("--page는 1 이상이어야 합니다.", file=sys.stderr)
        return 2

    if not 1 <= args.size <= 100:
        print("--size는 1~100 사이여야 합니다.", file=sys.stderr)
        return 2

    try:
        with make_session() as session:
            books, total_count, total_page = search_books(
                session=session,
                keyword=keyword,
                prov_code=args.prov_code,
                neis_code=args.neis_code,
                page=args.page,
                display=args.size,
            )

        if not args.no_state:
            add_states(
                books,
                prov_code=args.prov_code,
                neis_code=args.neis_code,
            )

        if args.json:
            result = {
                "schoolName": args.school_name,
                "provCode": args.prov_code,
                "neisCode": args.neis_code,
                "keyword": keyword,
                "page": args.page,
                "totalPage": total_page,
                "totalCount": total_count,
                "books": [asdict(book) for book in books],
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_books(
                books=books,
                total_count=total_count,
                page=args.page,
                total_page=total_page,
                school_name=args.school_name,
            )

        return 0

    except requests.RequestException as exc:
        print(f"네트워크 요청 실패: {exc}", file=sys.stderr)
        return 1
    except Read365Error as exc:
        print(f"독서로 API 오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
