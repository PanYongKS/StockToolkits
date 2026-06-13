#!/usr/bin/env python3
"""Batch download A-share financial report PDFs from cninfo.com.cn."""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import unquote

import requests


TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/detailOfQuery"
ANNOUNCEMENT_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE_URL = "https://static.cninfo.com.cn/"

REPORT_TYPE_TO_CATEGORY = {
    "annual": "category_ndbg_szsh",
    "semiannual": "category_bndbg_szsh",
    "q1": "category_yjdbg_szsh",
    "q3": "category_sjdbg_szsh",
}
ALL_REPORT_TYPE_TAG = "all"
ALL_DATE_RANGE_TAGS = {"", "all", "*", "all_years", "全部", "不限"}

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


@dataclass
class Company:
    name: str
    code: Optional[str] = None


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:200] or "unnamed"


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def parse_announcement_datetime(item: Dict[str, Any]) -> datetime:
    raw = str(item.get("adjunctDate", "")).strip() or str(item.get("announcementTime", "")).strip()
    normalized = raw.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return datetime.min


def extract_announcement_date_text(item: Dict[str, Any]) -> str:
    raw = str(item.get("adjunctDate", "")).strip() or str(item.get("announcementTime", "")).strip()
    if not raw:
        return ""
    return raw.split(" ")[0].replace("/", "-")


def company_output_dir(base_output_dir: Path, company_name: str, company_code: Optional[str]) -> Path:
    folder = f"{company_code}_{company_name}" if company_code else company_name
    return base_output_dir / sanitize_filename(folder)


def original_pdf_filename(adjunct_url: str, fallback_title: str) -> str:
    raw_name = unquote(adjunct_url.split("?", 1)[0].rsplit("/", 1)[-1]).strip()
    if raw_name:
        return raw_name
    fallback = sanitize_filename(fallback_title) or "unnamed"
    return f"{fallback}.pdf"


def append_company_index_row(index_csv_path: Path, row: Dict[str, str]) -> None:
    header = [
        "downloaded_at",
        "company_code",
        "company_name",
        "report_type",
        "announcement_date",
        "announcement_title",
        "sec_code",
        "sec_name",
        "pdf_url",
        "filename",
        "file_path",
    ]
    index_csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not index_csv_path.exists()
    with index_csv_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def announcement_matches_company(item: Dict[str, Any], company_name: str, company_code: Optional[str]) -> bool:
    sec_code = str(item.get("secCode", "")).strip()
    sec_name = strip_html(str(item.get("secName", ""))).strip()

    if company_code:
        if sec_code and sec_code != company_code:
            return False
        # If secCode is present and equal, trust code match as primary key.
        if sec_code == company_code:
            return True
        # Fallback when API response misses secCode.
        return bool(sec_name and company_name in sec_name)

    if sec_name:
        return company_name in sec_name

    # Last fallback if secName is missing.
    title = strip_html(str(item.get("announcementTitle", ""))).strip()
    return company_name in title


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    required_fields = ["output_dir", "report_types", "companies"]
    missing = [key for key in required_fields if key not in config]
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")

    if not isinstance(config["companies"], list) or not config["companies"]:
        raise ValueError("companies must be a non-empty list")

    raw_report_types = config["report_types"]
    if isinstance(raw_report_types, str):
        raw_report_types = [raw_report_types]
    if not isinstance(raw_report_types, list) or not raw_report_types:
        raise ValueError("report_types must be a non-empty list or a string")

    report_types = [str(item).strip().lower() for item in raw_report_types if str(item).strip()]
    if not report_types:
        raise ValueError("report_types must contain at least one valid item")

    if ALL_REPORT_TYPE_TAG in report_types:
        config["report_types"] = list(REPORT_TYPE_TO_CATEGORY.keys())
    else:
        invalid_types = [r for r in report_types if r not in REPORT_TYPE_TO_CATEGORY]
        if invalid_types:
            allowed = ", ".join(list(REPORT_TYPE_TO_CATEGORY.keys()) + [ALL_REPORT_TYPE_TAG])
            raise ValueError(f"Invalid report_types: {invalid_types}. Allowed: {allowed}")
        config["report_types"] = report_types

    raw_date_range = str(config.get("date_range", "")).strip()
    if raw_date_range.lower() in ALL_DATE_RANGE_TAGS or raw_date_range in ALL_DATE_RANGE_TAGS:
        config["date_range"] = ""
    else:
        config["date_range"] = raw_date_range

    return config


def parse_company(raw: Any) -> Company:
    if isinstance(raw, str):
        return Company(name=raw.strip())
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"Invalid company item, missing name: {raw}")
        code = str(raw.get("code", "")).strip() or None
        return Company(name=name, code=code)
    raise ValueError(f"Unsupported company config item: {raw}")


def resolve_company_code(session: requests.Session, company_name: str, timeout: int) -> Optional[str]:
    params = {"keyWord": company_name}
    resp = session.get(TOP_SEARCH_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        return None

    for item in data:
        if str(item.get("zwjc", "")).strip() == company_name:
            return str(item.get("code", "")).strip() or None
    first_code = str(data[0].get("code", "")).strip()
    return first_code or None


def iter_announcements(
    session: requests.Session,
    search_key: str,
    category: str,
    date_range: str,
    columns: Iterable[str],
    page_size: int,
    max_pages: int,
    timeout: int,
) -> Iterable[Dict[str, Any]]:
    for column in columns:
        for page_num in range(1, max_pages + 1):
            payload = {
                "pageNum": page_num,
                "pageSize": page_size,
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": "",
                "searchkey": search_key,
                "secid": "",
                "category": category,
                "trade": "",
                "seDate": date_range,
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            resp = session.post(ANNOUNCEMENT_QUERY_URL, data=payload, timeout=timeout)
            resp.raise_for_status()
            result = resp.json()
            items = result.get("announcements") or []
            if not items:
                break
            for item in items:
                yield item


def download_pdf(
    session: requests.Session,
    pdf_url: str,
    file_path: Path,
    timeout: int,
    dry_run: bool,
) -> bool:
    if file_path.exists():
        return False
    if dry_run:
        return True

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with session.get(pdf_url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with file_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Download A-share financial report PDFs from cninfo")
    parser.add_argument("--config", default="config.json", help="Path to config JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be downloaded")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)

    output_dir = Path(config["output_dir"]).expanduser().resolve()
    date_range = str(config.get("date_range", ""))
    report_types: List[str] = list(config["report_types"])
    columns = list(config.get("columns", ["szse", "sse"]))
    raw_page_size = int(config.get("page_size", 30))
    page_size = max(1, min(raw_page_size, 30))
    if raw_page_size != page_size:
        print(
            f"[WARN] page_size={raw_page_size} is adjusted to {page_size}. "
            "CNInfo may return duplicated pages when page_size > 30."
        )
    max_pages = int(config.get("max_pages", 8))
    max_per_company_per_type = int(config.get("max_per_company_per_type", 20))
    latest_only = bool(config.get("latest_only", False))
    timeout = int(config.get("request_timeout_seconds", 15))
    interval_seconds = float(config.get("interval_seconds", 0.5))
    resolve_code = bool(config.get("resolve_code_from_cninfo", True))
    exclude_title_keywords = [str(x) for x in config.get("exclude_title_keywords", ["摘要"])]

    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    companies = [parse_company(x) for x in config["companies"]]

    total_downloaded = 0
    total_planned = 0
    for company in companies:
        code = company.code
        if not code and resolve_code:
            try:
                code = resolve_company_code(session, company.name, timeout=timeout)
            except requests.RequestException as exc:
                print(f"[WARN] Resolve code failed for {company.name}: {exc}")

        print(f"\n== Company: {company.name} ({code or 'NO_CODE'}) ==")
        company_dir = company_output_dir(output_dir, company.name, code)

        for report_type in report_types:
            category = REPORT_TYPE_TO_CATEGORY[report_type]
            search_keys = [code] if code else [company.name]
            seen: Set[str] = set()
            downloaded_for_type = 0
            latest_candidate: Optional[Dict[str, Any]] = None

            for search_key in search_keys:
                try:
                    announcements = iter_announcements(
                        session=session,
                        search_key=search_key,
                        category=category,
                        date_range=date_range,
                        columns=columns,
                        page_size=page_size,
                        max_pages=max_pages,
                        timeout=timeout,
                    )
                    for ann in announcements:
                        if not announcement_matches_company(ann, company.name, code):
                            continue

                        adjunct_url = str(ann.get("adjunctUrl", "")).strip()
                        if not adjunct_url or adjunct_url in seen:
                            continue
                        seen.add(adjunct_url)

                        if not adjunct_url.lower().endswith(".pdf"):
                            continue

                        title = strip_html(str(ann.get("announcementTitle", ""))).strip()
                        if any(keyword in title for keyword in exclude_title_keywords):
                            continue

                        pdf_url = PDF_BASE_URL + adjunct_url
                        filename = original_pdf_filename(adjunct_url, title)
                        file_path = company_dir / filename
                        ann_date_text = extract_announcement_date_text(ann)
                        sec_code = str(ann.get("secCode", code or "")).strip()
                        sec_name = strip_html(str(ann.get("secName", ""))).strip()

                        if latest_only:
                            ann_dt = parse_announcement_datetime(ann)
                            if latest_candidate is None or ann_dt > latest_candidate["ann_dt"]:
                                latest_candidate = {
                                    "ann_dt": ann_dt,
                                    "pdf_url": pdf_url,
                                    "file_path": file_path,
                                    "report_type": report_type,
                                    "announcement_date": ann_date_text,
                                    "title": title,
                                    "sec_code": sec_code,
                                    "sec_name": sec_name,
                                    "filename": filename,
                                }
                            continue

                        if downloaded_for_type >= max_per_company_per_type:
                            break

                        try:
                            is_new = download_pdf(
                                session=session,
                                pdf_url=pdf_url,
                                file_path=file_path,
                                timeout=timeout,
                                dry_run=args.dry_run,
                            )
                        except requests.RequestException as exc:
                            print(f"[WARN] Download failed: {pdf_url} ({exc})")
                            continue

                        if is_new:
                            downloaded_for_type += 1
                            total_planned += 1
                            if not args.dry_run:
                                total_downloaded += 1
                                append_company_index_row(
                                    company_dir / "download_index.csv",
                                    {
                                        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                                        "company_code": code or "",
                                        "company_name": company.name,
                                        "report_type": report_type,
                                        "announcement_date": ann_date_text,
                                        "announcement_title": title,
                                        "sec_code": sec_code,
                                        "sec_name": sec_name,
                                        "pdf_url": pdf_url,
                                        "filename": filename,
                                        "file_path": str(file_path),
                                    },
                                )
                            action = "PLAN" if args.dry_run else "OK"
                            rel_path = file_path.relative_to(output_dir)
                            print(f"[{action}] {rel_path}")
                            time.sleep(interval_seconds)
                except requests.RequestException as exc:
                    print(
                        f"[WARN] Query failed: company={company.name}, report={report_type}, "
                        f"search_key={search_key}, error={exc}"
                    )

            if latest_only and latest_candidate is not None:
                try:
                    is_new = download_pdf(
                        session=session,
                        pdf_url=str(latest_candidate["pdf_url"]),
                        file_path=Path(latest_candidate["file_path"]),
                        timeout=timeout,
                        dry_run=args.dry_run,
                    )
                except requests.RequestException as exc:
                    print(f"[WARN] Download failed: {latest_candidate['pdf_url']} ({exc})")
                else:
                    if is_new:
                        downloaded_for_type += 1
                        total_planned += 1
                        if not args.dry_run:
                            total_downloaded += 1
                            latest_path = Path(latest_candidate["file_path"])
                            append_company_index_row(
                                company_dir / "download_index.csv",
                                {
                                    "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                                    "company_code": code or "",
                                    "company_name": company.name,
                                    "report_type": str(latest_candidate.get("report_type", report_type)),
                                    "announcement_date": str(latest_candidate.get("announcement_date", "")),
                                    "announcement_title": str(latest_candidate.get("title", "")),
                                    "sec_code": str(latest_candidate.get("sec_code", code or "")),
                                    "sec_name": str(latest_candidate.get("sec_name", "")),
                                    "pdf_url": str(latest_candidate["pdf_url"]),
                                    "filename": str(latest_candidate.get("filename", latest_path.name)),
                                    "file_path": str(latest_path),
                                },
                            )
                        action = "PLAN" if args.dry_run else "OK"
                        rel_path = Path(latest_candidate["file_path"]).relative_to(output_dir)
                        print(f"[{action}] {rel_path}")
                        time.sleep(interval_seconds)

            print(f"[INFO] {report_type}: {downloaded_for_type} file(s)")

    if args.dry_run:
        print(f"\nDry-run finished. Planned new files: {total_planned}")
    else:
        print(f"\nFinished. Downloaded files: {total_downloaded}")


if __name__ == "__main__":
    main()
