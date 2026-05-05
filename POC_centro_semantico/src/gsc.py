"""Google Search Console integration via service account."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


def _build_service(service_account_json: dict) -> Any:
    """Build an authenticated GSC service object."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_info(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    return build("searchconsole", "v1", credentials=credentials)


def get_gsc_properties(service_account_json: dict) -> list[str]:
    """Return list of GSC properties accessible by the service account."""
    service = _build_service(service_account_json)
    response = service.sites().list().execute()
    sites = response.get("siteEntry", [])
    return [s["siteUrl"] for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]


def fetch_gsc_data(
    service_account_json: dict,
    property_url: str,
    days: int = 90,
) -> pd.DataFrame:
    """Fetch GSC performance data (page-level) for the last N days.

    Returns DataFrame with columns: url, clicks, impressions, ctr, position.
    """
    service = _build_service(service_account_json)

    end_date = datetime.now(timezone.utc).date() - timedelta(days=3)  # GSC data lag
    start_date = end_date - timedelta(days=days)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["page"],
        "rowLimit": 25000,
        "type": "web",
    }

    rows: list[dict] = []
    start_row = 0

    while True:
        body["startRow"] = start_row
        response = service.searchanalytics().query(
            siteUrl=property_url, body=body
        ).execute()

        batch = response.get("rows", [])
        if not batch:
            break

        for row in batch:
            rows.append({
                "url": row["keys"][0],
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": round(row.get("ctr", 0), 4),
                "position": round(row.get("position", 0), 1),
            })

        start_row += len(batch)
        if len(batch) < 25000:
            break

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["url", "clicks", "impressions", "ctr", "position"]
    )


def fetch_gsc_query_page_data(
    service_account_json: dict,
    property_url: str,
    days: int = 90,
    max_rows: int = 100000,
) -> pd.DataFrame:
    """Fetch GSC performance data at query+page level.

    Returns DataFrame with columns: query, url, clicks, impressions, ctr, position.
    Used for cannibalization validation (keyword overlap between pages).
    """
    service = _build_service(service_account_json)

    end_date = datetime.now(timezone.utc).date() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": 25000,
        "type": "web",
    }

    rows: list[dict] = []
    start_row = 0

    while len(rows) < max_rows:
        body["startRow"] = start_row
        response = service.searchanalytics().query(
            siteUrl=property_url, body=body
        ).execute()

        batch = response.get("rows", [])
        if not batch:
            break

        for row in batch:
            rows.append({
                "query": row["keys"][0],
                "url": row["keys"][1],
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": round(row.get("ctr", 0), 4),
                "position": round(row.get("position", 0), 1),
            })

        start_row += len(batch)
        if len(batch) < 25000:
            break

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["query", "url", "clicks", "impressions", "ctr", "position"]
    )
