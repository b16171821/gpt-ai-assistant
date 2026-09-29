"""Export only an aggregate GA4 user count; never export credentials or user-level data."""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


START_DATE = "2026-09-29"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "site_visitors.json"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def report_request(end_date):
    return {
        "dateRanges": [{"startDate": START_DATE, "endDate": end_date}],
        "metrics": [{"name": "totalUsers"}],
        "dimensionFilter": {"andGroup": {"expressions": [
            {"filter": {"fieldName": "hostName", "stringFilter": {
                "matchType": "EXACT", "value": "b16171821.github.io"}}},
            {"filter": {"fieldName": "pagePath", "stringFilter": {
                "matchType": "BEGINS_WITH", "value": "/gpt-ai-assistant/"}}},
        ]}},
    }


def parse_report(report):
    if not isinstance(report, dict):
        raise ValueError("Invalid report")
    if report.get("metricHeaders") != [{"name": "totalUsers", "type": "TYPE_INTEGER"}]:
        raise ValueError("Unexpected metric")
    metadata = report.get("metadata", {})
    if metadata.get("subjectToThresholding") or metadata.get("samplingMetadatas"):
        raise ValueError("Report is thresholded or sampled; retain last confirmed count")
    rows = report.get("rows", [])
    if rows == [] and report.get("rowCount", 0) == 0:
        return 0
    if len(rows) != 1 or len(rows[0].get("metricValues", [])) != 1:
        raise ValueError("Expected one aggregate row")
    value = rows[0]["metricValues"][0].get("value")
    if not isinstance(value, str) or not re.fullmatch(r"\d+", value):
        raise ValueError("Invalid user count")
    count = int(value)
    if count > 9007199254740991:
        raise ValueError("Count exceeds browser integer range")
    return count


def export_report(fetch_report, output=OUTPUT, now=None):
    now = now or datetime.now(timezone.utc)
    through = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    count = parse_report(fetch_report(report_request(through)))
    result = {
        "status": "OK", "source": "Google Analytics 4", "metric": "totalUsers",
        "totalUsers": count, "startDate": START_DATE, "throughDate": through,
        "updatedAt": now.isoformat(),
    }
    # Validate the entire response before replacing the previous successful export.
    output = Path(output)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)
    return result


def main():
    property_id = os.environ.get("GA4_PROPERTY_ID", "").strip()
    credential_json = os.environ.get("GA4_SERVICE_ACCOUNT_JSON", "")
    if not property_id or not credential_json:
        print("GA4 report credentials not configured; no statistics changed.")
        return 0
    if not re.fullmatch(r"\d+", property_id):
        print("GA4_PROPERTY_ID must be numeric; no statistics changed.")
        return 1
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession

        info = json.loads(credential_json)
        # Do not let credential configuration redirect authentication to another host.
        if info.get("type") != "service_account" or info.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise ValueError("Invalid credential configuration")
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        with AuthorizedSession(credentials) as session:
            def fetch_report(body):
                response = session.post(
                    f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
                    json=body, timeout=30,
                )
                response.raise_for_status()
                return response.json()
            export_report(fetch_report)
    except Exception:
        # Do not log auth objects, response bodies or exception text containing credential data.
        print("GA4 report update failed. Check API access and credentials; previous statistics retained.")
        return 1
    print("Public aggregate visitor statistics updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
