#!/usr/bin/env python3
import json
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

URL = "https://www.elyes.co.kr/post/recruit"
STATE_FILE = Path(os.getenv("ELYES_STATE_FILE", "elyes_seen.json"))
HEADLESS = os.getenv("ELYES_HEADLESS", "true").lower() != "false"

# 환경변수로 설정하세요.
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = os.getenv("MAIL_TO", "")

# 사이트 구조 변경에 대비해 여러 후보를 순서대로 검사합니다.
CARD_SELECTORS = [
    "a[href*='/post/recruit/']",
    "a[href*='/recruit/']",
    "a[href*='recruit?']",
    ".recruit-list a",
    ".board-list a",
    ".list-wrap a",
]

EXCLUDE_TEXTS = {
    "모집공고", "전체", "이용안내", "공지사항", "자주하는질문", "문의방법",
    "회원가입", "로그인", "이전", "다음", "처음", "마지막"
}


def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip()


def collect_posts(page):
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # 스크롤/더보기 방식도 최대한 대응합니다.
    previous_height = 0
    for _ in range(30):
        for label in ("더보기", "MORE", "Load more"):
            button = page.get_by_text(label, exact=True)
            if button.count() and button.first.is_visible():
                try:
                    button.first.click(timeout=1500)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
        height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        if height == previous_height:
            break
        previous_height = height

    posts = {}
    for selector in CARD_SELECTORS:
        for element in page.locator(selector).all():
            try:
                title = normalize(element.inner_text(timeout=1500))
                href = element.get_attribute("href") or ""
            except Exception:
                continue
            if not title or title in EXCLUDE_TEXTS or len(title) < 4:
                continue
            # 내비게이션/목록 자체 링크 제외
            absolute = urljoin(URL, href)
            if absolute.rstrip("/") == URL.rstrip("/"):
                continue
            posts[title] = absolute

    if not posts:
        page.screenshot(path="elyes_debug.png", full_page=True)
        Path("elyes_debug.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(
            "공고 제목을 찾지 못했습니다. elyes_debug.png/html을 확인해 "
            "CARD_SELECTORS에 실제 공고 링크 선택자를 추가하세요."
        )
    return posts


def load_seen():
    if not STATE_FILE.exists():
        return None
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return set(data.get("titles", []))


def save_seen(posts):
    payload = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "titles": sorted(posts.keys()),
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def send_mail(new_posts):
    missing = [name for name, value in {
        "SMTP_USER": SMTP_USER, "SMTP_PASSWORD": SMTP_PASSWORD, "MAIL_TO": MAIL_TO
    }.items() if not value]
    if missing:
        raise RuntimeError("메일 환경변수가 없습니다: " + ", ".join(missing))

    lines = ["ELYES 신규 모집공고가 등록되었습니다.", ""]
    for title, link in sorted(new_posts.items()):
        lines.extend([f"- {title}", f"  {link}"])
    lines.extend(["", f"확인: {URL}"])

    msg = EmailMessage()
    msg["Subject"] = f"[ELYES] 신규 모집공고 {len(new_posts)}건"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    msg.set_content("\n".join(lines))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        try:
            posts = collect_posts(browser.new_page(viewport={"width": 1440, "height": 1000}))
        finally:
            browser.close()

    seen = load_seen()
    if seen is None:
        save_seen(posts)
        print(f"최초 실행: 현재 공고 {len(posts)}건을 기준 목록으로 저장했습니다. 메일은 보내지 않습니다.")
        return 0

    new_posts = {title: link for title, link in posts.items() if title not in seen}
    if not new_posts:
        save_seen(posts)
        print(f"신규 공고 없음. 현재 공고 {len(posts)}건")
        return 0

    # 메일 발송 성공 후에만 상태를 갱신하여 실패 시 다음 실행에서 재시도합니다.
    send_mail(new_posts)
    save_seen(posts)
    print(f"신규 공고 {len(new_posts)}건 알림 발송 완료")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
