#!/usr/bin/env python3
import argparse
import html
import http.cookiejar
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser


BASE_URL = "https://forms.newforest.gov.uk/ufs/FIND_MY_BIN_BAR.eb?ebd=0&ebp=10"
AREA_NAME = "The Hummicks"
POSTCODE = "SO42 7YU"
ADDRESS_ID = "100061999373"
TIMEZONE_ID = "Europe/London"
OUTPUT_FILE = "nfdc_bin_collections.ics"

CONTAINER_DETAILS = {
    "Food 23L": "Brown food recycling caddy (weekly)",
    "General 180L": "Black-lid rubbish bin (fortnightly)",
    "Recycle 240L": "Green-lid recycle bin (fortnightly)",
    "Glass Box": "Glass box (4 weekly)",
}

CONTAINER_ORDER = {
    "Food 23L": 0,
    "General 180L": 1,
    "Recycle 240L": 2,
    "Glass Box": 3,
}

SUMMARY_NAMES = {
    "Food 23L": "Food",
    "General 180L": "General",
    "Recycle 240L": "Recycle",
    "Glass Box": "Glass",
}

SUMMARY_EMOJIS = {
    "Food 23L": "🍽️",
    "General 180L": "🗑️",
    "Recycle 240L": "♻️",
    "Glass Box": "🫙",
}


@dataclass
class Form:
    action: str
    inputs: list[tuple[str, str]]
    selects: dict[str, list[tuple[str, str]]]


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.form_action = ""
        self.inputs: list[tuple[str, str]] = []
        self.selects: dict[str, list[tuple[str, str]]] = {}
        self._current_select: str | None = None
        self._current_option_value: str | None = None
        self._current_option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name: value or "" for name, value in attrs}
        if tag == "form":
            self.form_action = attrs_dict.get("action", "")
        elif tag == "input":
            name = attrs_dict.get("name")
            if name:
                self.inputs.append((name, attrs_dict.get("value", "")))
        elif tag == "select":
            name = attrs_dict.get("name")
            if name:
                self._current_select = name
                self.selects.setdefault(name, [])
        elif tag == "option" and self._current_select:
            self._current_option_value = attrs_dict.get("value", "")
            self._current_option_text = []

    def handle_data(self, data: str) -> None:
        if self._current_option_value is not None:
            self._current_option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._current_select and self._current_option_value is not None:
            text = normalize_text("".join(self._current_option_text))
            self.selects[self._current_select].append((self._current_option_value, text))
            self._current_option_value = None
            self._current_option_text = []
        elif tag == "select":
            self._current_select = None


class FutureCollectionsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_future_section = False
        self.in_table = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name: value or "" for name, value in attrs}
        class_name = attrs_dict.get("class", "")
        if tag == "table" and "eb-1j4UaesZ-tableContent" in class_name:
            self.in_future_section = True
            self.in_table = True
        elif self.in_table and tag == "td":
            self.in_cell = True
            self.current_cell = []
        elif self.in_table and tag == "tr":
            self.current_row = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag == "td":
            self.in_cell = False
            self.current_row.append(normalize_text("".join(self.current_cell)))
        elif self.in_table and tag == "tr":
            if len(self.current_row) >= 2 and self.current_row[0] != "Collection date":
                self.rows.append((self.current_row[0], self.current_row[1]))
            self.current_row = []
        elif self.in_table and tag == "table":
            self.in_table = False


def normalize_text(value: str) -> str:
    return html.unescape(value.replace("\xa0", " ")).strip()


def parse_form(page: str) -> Form:
    parser = FormParser()
    parser.feed(page)
    if not parser.form_action:
        raise RuntimeError("Could not find form action in council response")
    return Form(parser.form_action, parser.inputs, parser.selects)


def build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def request_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    data: dict[str, str] | None = None,
) -> tuple[str, str]:
    encoded = None
    if data is not None:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "NFDC-Collections/1.0 (+https://github.com/damorrison/NFDC-Collections)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with opener.open(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace"), response.geturl()


def absolute_action_url(current_url: str, form: Form) -> str:
    return urllib.parse.urljoin(current_url, form.action)


def submit_form(
    opener: urllib.request.OpenerDirector,
    current_url: str,
    page: str,
    updates: dict[str, str],
) -> tuple[str, str]:
    form = parse_form(page)
    fields = dict(form.inputs)
    fields.update(updates)
    return request_text(opener, absolute_action_url(current_url, form), fields)


def find_postcode_field(form: Form) -> str:
    for name, _value in form.inputs:
        if name.startswith("CTRL:") and name.endswith(":A"):
            return name
    raise RuntimeError("Could not find postcode input field")


def find_submit_field(form: Form, value: str = "Submit") -> str:
    for name, input_value in form.inputs:
        if name.startswith("CTRL:") and input_value == value:
            return name
    raise RuntimeError(f"Could not find submit field with value {value!r}")


def find_address_select(form: Form) -> str:
    for name, options in form.selects.items():
        if any(value == ADDRESS_ID for value, _text in options):
            return name
    raise RuntimeError(f"Could not find address option {ADDRESS_ID}")


def scrape_collections() -> list[tuple[date, str]]:
    opener = build_opener()
    page, url = request_text(opener, BASE_URL)

    postcode_form = parse_form(page)
    page, url = submit_form(
        opener,
        url,
        page,
        {
            find_postcode_field(postcode_form): POSTCODE,
            find_submit_field(postcode_form): "Submit",
        },
    )

    address_form = parse_form(page)
    page, _url = submit_form(
        opener,
        url,
        page,
        {
            find_address_select(address_form): ADDRESS_ID,
            find_submit_field(address_form): "Submit",
        },
    )

    parser = FutureCollectionsParser()
    parser.feed(page)
    if not parser.rows:
        raise RuntimeError("Could not find future collections table in council response")

    collections: list[tuple[date, str]] = []
    for raw_date, container in parser.rows:
        try:
            collection_date = datetime.strptime(raw_date, "%A %B %d, %Y").date()
        except ValueError as exc:
            raise RuntimeError(f"Could not parse collection date {raw_date!r}") from exc
        collections.append((collection_date, container))
    return collections


def infer_future_events(scraped: list[tuple[date, str]], start: date, horizon_days: int) -> dict[date, set[str]]:
    by_container: dict[str, list[date]] = defaultdict(list)
    for collection_date, container in scraped:
        by_container[container].append(collection_date)

    events: dict[date, set[str]] = defaultdict(set)
    for collection_date, container in scraped:
        if start <= collection_date <= start + timedelta(days=horizon_days):
            events[collection_date].add(container)

    end = start + timedelta(days=horizon_days)
    for container, dates in by_container.items():
        unique_dates = sorted(set(dates))
        if len(unique_dates) >= 2:
            interval = min(
                (later - earlier).days
                for earlier, later in zip(unique_dates, unique_dates[1:])
                if (later - earlier).days > 0
            )
        elif container == "Food 23L":
            interval = 7
        elif container == "Glass Box":
            interval = 28
        else:
            interval = 14

        cursor = unique_dates[-1]
        while cursor < end:
            cursor += timedelta(days=interval)
            if start <= cursor <= end:
                events[cursor].add(container)

    return events


def sort_containers(containers: set[str]) -> list[str]:
    return sorted(containers, key=lambda item: (CONTAINER_ORDER.get(item, 99), item))


def ical_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> list[str]:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return [line]
    parts = []
    current = ""
    for char in line:
        candidate = current + char
        if len(candidate.encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def append_line(lines: list[str], line: str) -> None:
    lines.extend(fold_line(line))


def generate_ics(events: dict[date, set[str]], generated_at: datetime) -> str:
    dtstamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Codex//NFDC Bin Collections//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:♻️ NFDC Bin Collections - {AREA_NAME}",
        f"X-WR-TIMEZONE:{TIMEZONE_ID}",
    ]

    for collection_date in sorted(events):
        containers = sort_containers(events[collection_date])
        if not containers:
            continue
        emoji_prefix = "".join(SUMMARY_EMOJIS.get(item, "") for item in containers)
        summary = f"{emoji_prefix} Bins: " + ", ".join(
            SUMMARY_NAMES.get(item, item) for item in containers
        )
        descriptions = [
            f"{container} - {CONTAINER_DETAILS.get(container, 'Collection')}"
            for container in containers
        ]
        description = f"Area: {AREA_NAME}.\n\nCollections:\n" + ".\n".join(descriptions) + "."
        dtstart = collection_date.strftime("%Y%m%d")
        lines.append("BEGIN:VEVENT")
        append_line(lines, f"UID:nfdc-the-hummicks-{dtstart}@local")
        lines.append(f"DTSTAMP:{dtstamp}")
        append_line(lines, f"SUMMARY:{ical_escape(summary)}")
        append_line(lines, f"DESCRIPTION:{ical_escape(description)}")
        lines.append(f"DTSTART;TZID={TIMEZONE_ID}:{dtstart}T070000")
        lines.append(f"DTEND;TZID={TIMEZONE_ID}:{dtstart}T080000")
        lines.append("TRANSP:TRANSPARENT")
        lines.append("BEGIN:VALARM")
        lines.append("ACTION:DISPLAY")
        lines.append("DESCRIPTION:Put bins out for tomorrow")
        lines.append("TRIGGER:-PT13H")
        lines.append("END:VALARM")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_calendar(path: str, months: int) -> None:
    scraped = scrape_collections()
    today = datetime.now(timezone.utc).date()
    horizon_days = (add_months(today, months) - today).days
    events = infer_future_events(scraped, today, horizon_days)
    ics = generate_ics(events, datetime.now(timezone.utc))
    with open(path, "w", encoding="utf-8", newline="") as output:
        output.write(ics)
    print(f"Wrote {path} with {len(events)} collection days")


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    days_in_month = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ][month - 1]
    return date(year, month, min(value.day, days_in_month))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate NFDC bin collection ICS")
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--months", type=int, default=3)
    args = parser.parse_args()

    try:
        write_calendar(args.output, args.months)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
