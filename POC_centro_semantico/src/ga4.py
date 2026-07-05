"""Google Analytics 4 (Data API) — serie diaria por canal.

El lado de negocio que Search Console no ve: sesiones, usuarios,
conversiones e ingresos por día. Usa la misma convención de service
account que GSC (`credentials_json`), y la Data API de GA4
(`google-analytics-data`, import perezoso: opcional hasta que se conecte
una cuenta GA4).
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def _build_client(service_account_json: dict) -> Any:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return BetaAnalyticsDataClient(credentials=creds)


def fetch_ga4_daily(
    service_account_json: dict,
    property_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Serie diaria por canal. Columnas: date, channel, sessions,
    active_users, conversions, revenue.

    ``property_id`` como "properties/123456" o "123456" (se normaliza).
    """
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest,
    )

    pid = property_id if property_id.startswith("properties/") else f"properties/{property_id}"
    client = _build_client(service_account_json)
    request = RunReportRequest(
        property=pid,
        dimensions=[Dimension(name="date"), Dimension(name="sessionDefaultChannelGroup")],
        metrics=[
            Metric(name="sessions"), Metric(name="activeUsers"),
            Metric(name="conversions"), Metric(name="totalRevenue"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=100000,
    )
    resp = client.run_report(request)
    rows: list[dict] = []
    for r in resp.rows:
        d = r.dimension_values
        m = r.metric_values
        raw_date = d[0].value  # GA4 devuelve "YYYYMMDD"
        iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date) == 8 else raw_date
        rows.append({
            "date": iso, "channel": d[1].value,
            "sessions": int(m[0].value or 0),
            "active_users": int(m[1].value or 0),
            "conversions": float(m[2].value or 0.0),
            "revenue": float(m[3].value or 0.0),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["date", "channel", "sessions", "active_users", "conversions", "revenue"])


def get_ga4_properties(service_account_json: dict) -> list[dict]:
    """Lista las propiedades GA4 accesibles (Admin API). Devuelve
    [{property_id, display_name}]."""
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    client = AnalyticsAdminServiceClient(credentials=creds)
    out: list[dict] = []
    for acc in client.list_account_summaries():
        for prop in acc.property_summaries:
            out.append({"property_id": prop.property,
                        "display_name": prop.display_name})
    return out
