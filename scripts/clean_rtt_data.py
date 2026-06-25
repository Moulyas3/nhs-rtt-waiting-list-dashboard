"""
clean_rtt_data.py
=================
Cleaning and transformation pipeline for the NHS England Referral to Treatment
(RTT) waiting list dashboard.

What this script does, in order:
  1. Makes sure the raw monthly "Full CSV" extracts are present in data/raw/,
     downloading and unzipping them from NHS England's open-data site if missing.
  2. Reads every monthly file, standardises the column names to snake_case, and
     filters down to *incomplete pathways* - the group the 18-week waiting-time
     standard is actually measured against.
  3. Derives the headline measures: patients seen within 18 weeks, total waiting,
     waits over 52 weeks, the % within 18 weeks, and a missed-target flag.
  4. Writes the processed CSVs to data/processed/ and a small JavaScript data
     file (dashboard/dashboard_data.js) that the HTML dashboard reads directly.

Design notes (why it is written this way):
  * The pipeline is schema-driven. It discovers the weekly "band" columns from
    their names with a regex rather than listing 100+ columns by hand, so it
    keeps working unchanged when NHS England publishes a new month.
  * NHS England publishes the data at provider x commissioner x specialty grain,
    so we always group across commissioners to get true trust/specialty totals.
  * No trust names, ICB names or totals are hard-coded anywhere - everything is
    read from the source files, so re-running with newer data just works.

Data source: NHS England, Consultant-led RTT Waiting Times (Open Government
Licence v3.0).
https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/

Author: Moulya Shreedhara
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The 18-week standard as a proportion. NHS England's Medium Term Planning
# Framework 2026-2029 sets an interim milestone of 70% of incomplete pathways
# waiting 18 weeks or less, on the way back to the 92% constitutional standard.
# Both live here as named constants so the thresholds are defined in one place.
INTERIM_TARGET = 0.70
CONSTITUTIONAL_STANDARD = 0.92

# Folder layout. This file lives in <repo>/scripts/, so the repo root is one up.
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# NHS England "Full CSV data file" downloads, one ZIP per month. To extend the
# trend in future, add the next month's URL here - nothing else needs changing.
# Keys are "<MonthName>-<Year>" so we can match them against the extracted file
# names (e.g. "...-RTT-April-2026-full-extract.csv").
SOURCE_FILES = {
    "April-2025":     "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Apr25-ZIP-4M-revised.zip",
    "May-2025":       "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-May25-ZIP-4M-revised.zip",
    "June-2025":      "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Jun25-ZIP-4M-revised.zip",
    "July-2025":      "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Jul25-ZIP-4M-revised-2.zip",
    "August-2025":    "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Aug25-ZIP-4M-revised-2.zip",
    "September-2025": "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Sep25-ZIP-4M-revised.zip",
    "October-2025":   "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2025/12/Full-CSV-data-file-Oct25-ZIP-4M-SrRW6y.zip",
    "November-2025":  "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/01/Full-CSV-data-file-Nov25-ZIP-4M-1Xmjkk.zip",
    "December-2025":  "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/02/Full-CSV-data-file-Dec25-ZIP-4M-6jPlxd.zip",
    "January-2026":   "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/03/Full-CSV-data-file-Jan26-ZIP-4M-WL5BiP.zip",
    "February-2026":  "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/04/Full-CSV-data-file-Feb26-ZIP-4M-9j03fJT.zip",
    "March-2026":     "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/05/Full-CSV-data-file-Mar26-ZIP-4M-Dc1i9U.zip",
    "April-2026":     "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/06/Full-CSV-data-file-Apr26-ZIP-3M-X7gGnn.zip",
}

# The RTT "part" we report on. Incomplete pathways are patients still waiting at
# month end - the group the 18-week standard is measured against. We match the
# label exactly so we do not accidentally pull in "Incomplete Pathways with DTA".
INCOMPLETE_LABEL = "Incomplete Pathways"

# NHS England includes an all-specialty roll-up row (treatment function C_999,
# named "Total") for every provider. We drop it from the detailed table so that
# summing the real specialties does not double-count patients.
TOTAL_SPECIALTY_CODE = "C_999"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def snake_case(name: str) -> str:
    """Lower-case a column name and turn any run of non-alphanumeric characters
    into a single underscore. 'Gt 00 To 01 Weeks SUM 1' -> 'gt_00_to_01_weeks_sum_1'."""
    return re.sub(r"[^0-9a-z]+", "_", name.strip().lower()).strip("_")


def period_to_date(period_label: str) -> pd.Timestamp:
    """Turn the source 'Period' value into a month-start date.
    'RTT-April-2026' -> Timestamp('2026-04-01')."""
    return pd.to_datetime(period_label.replace("RTT-", "").strip(), format="%B-%Y")


def ensure_raw_data() -> None:
    """Download and unzip any monthly file whose extract is not already in
    data/raw/. We test for the *extracted* CSV (matched on month and year) so a
    second run is cheap and never re-downloads ~1 GB unnecessarily."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for period, url in SOURCE_FILES.items():
        month_name, year = period.split("-")
        already_there = list(RAW_DIR.glob(f"*{month_name}*{year}*full-extract*.csv"))
        if already_there:
            continue
        print(f"  downloading {period} from NHS England ...")
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
        response.raise_for_status()
        zip_path = RAW_DIR / f"full_csv_{period}.zip"
        zip_path.write_bytes(response.content)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(RAW_DIR)


# ---------------------------------------------------------------------------
# Core transformation
# ---------------------------------------------------------------------------

def load_month(csv_path: Path) -> pd.DataFrame:
    """Read one monthly extract and return a tidy frame of incomplete pathways at
    provider x commissioner x specialty grain, with the headline counts derived.
    The heavy weekly-band columns are summed into a few measures and then dropped
    so we never hold more than one month of wide data in memory at a time."""
    # Reading '*' (NHS England's small-number suppression marker) as NaN means
    # suppressed cells never get silently parsed as text or a stray number.
    df = pd.read_csv(csv_path, low_memory=False, na_values=["*"])
    df.columns = [snake_case(c) for c in df.columns]

    # Keep only incomplete pathways. Exact equality excludes the separate
    # "Incomplete Pathways with DTA" rows, which would otherwise inflate totals.
    df = df[df["rtt_part_description"].str.strip() == INCOMPLETE_LABEL]

    # Discover the weekly band columns from their names. A normal band looks like
    # 'gt_00_to_01_weeks_sum_1' (more than 0, up to 1 week); the final open band
    # is 'gt_104_weeks_sum_1' (more than 104 weeks).
    band_cols, lower, upper = [], {}, {}
    for col in df.columns:
        pair = re.match(r"gt_(\d+)_to_(\d+)_weeks", col)
        if pair:
            band_cols.append(col)
            lower[col], upper[col] = int(pair.group(1)), int(pair.group(2))
            continue
        open_band = re.match(r"gt_(\d+)_weeks", col)
        if open_band:
            band_cols.append(col)
            # Give the open-ended 104+ band a nominal upper edge for the average.
            lower[col], upper[col] = int(open_band.group(1)), int(open_band.group(1)) + 2

    # "Within 18 weeks" = every band whose upper edge is 18 weeks or less.
    within_cols = [c for c in band_cols if upper[c] <= 18]
    # "Over 52 weeks" = every band that begins after 52 weeks.
    over52_cols = [c for c in band_cols if lower[c] >= 52]

    # Keep only the identifier and band columns we actually use, as a contiguous
    # copy. Trimming the 120-column source down keeps memory low and avoids the
    # fragmentation warning pandas raises when columns are added to a wide frame.
    id_cols = ["period", "provider_org_code", "provider_org_name",
               "provider_parent_name", "treatment_function_code",
               "treatment_function_name"]
    df = df[id_cols + band_cols].copy()

    # Coerce the band counts to numbers (suppressed '*' cells are already NaN).
    for col in band_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Estimated "person-weeks" using each band's midpoint. The source only gives
    # banded counts, not exact waits, so this lets us approximate an average wait
    # later (sum of person-weeks / total patients). It is clearly an estimate.
    person_weeks = sum(df[c] * ((lower[c] + upper[c]) / 2.0) for c in band_cols)

    # Derive every measure in a single assign() so the new columns are added to a
    # fresh, contiguous frame rather than inserted one-by-one into the 120-column
    # source (which pandas warns about as fragmentation).
    # For incomplete pathways NHS England leaves the legacy 'Total' column blank,
    # so the waiting total is the sum across the weekly bands (this equals the
    # 'Total All' column). Summing the bands ourselves never depends on which
    # total column is populated and guarantees within_18_weeks <= total_waiting.
    df = df.assign(
        total_waiting=df[band_cols].sum(axis=1),
        within_18_weeks=df[within_cols].sum(axis=1),
        over_52_weeks=df[over52_cols].sum(axis=1),
        weighted_weeks=person_weeks,
    )

    # Drop rows with nobody waiting (all-zero or fully suppressed): they carry no
    # information and would only create undefined percentages downstream.
    df = df[df["total_waiting"] > 0].copy()

    renamed = df.rename(columns={
        "provider_org_code": "trust_code",
        "provider_org_name": "trust_name",
        "provider_parent_name": "icb_name",
        "treatment_function_code": "specialty_code",
        "treatment_function_name": "specialty_name",
    })
    keep = ["period", "trust_code", "trust_name", "icb_name", "specialty_code",
            "specialty_name", "total_waiting", "within_18_weeks", "over_52_weeks",
            "weighted_weeks"]
    return renamed[keep]


def performance_band(pct: float) -> str:
    """Bucket a trust's % within 18 weeks for filtering and conditional colour."""
    if pd.isna(pct):
        return "No data"
    if pct < 0.70:
        return "Below 70%"
    if pct <= 0.80:
        return "70-80%"
    return "Above 80%"


# ---------------------------------------------------------------------------
# Dashboard data file (read by dashboard/index.html)
# ---------------------------------------------------------------------------

def write_dashboard_data(clean_specialty: pd.DataFrame,
                         trust_summary: pd.DataFrame,
                         trend: pd.DataFrame) -> None:
    """Write dashboard/dashboard_data.js. Browsers block fetch() of local CSV
    files when an HTML page is opened from disk, so the dashboard cannot read the
    CSVs directly. Instead we embed a small, pre-aggregated JSON object as a JS
    variable - this keeps the dashboard fully offline and fast, and it is
    regenerated every time this script runs, so the dashboard never goes stale."""
    last = trend.iloc[-1]
    latest_label = pd.to_datetime(last["period_date"]).strftime("%B %Y")

    # National monthly trend (small: one row per month).
    trend_out = [{
        "label": pd.to_datetime(r["period_date"]).strftime("%b %Y"),
        "date": r["period_date"],
        "pct": None if pd.isna(r["pct_within_18_weeks"]) else float(r["pct_within_18_weeks"]),
        "total_waiting": int(r["total_waiting"]),
        "within_18_weeks": int(r["within_18_weeks"]),
        "over_52_weeks": int(r["over_52_weeks"]),
        "meeting": int(r["trusts_meeting_target"]),
        "missing": int(r["trusts_missing_target"]),
        "trusts_total": int(r["trusts_total"]),
    } for _, r in trend.iterrows()]

    # Per-trust snapshot for the most recent month (one row per trust).
    trusts_out = [{
        "code": r["trust_code"],
        "name": r["trust_name"],
        "icb": r["icb_name"],
        "total": int(r["total_waiting"]),
        "within18": int(r["within_18_weeks"]),
        "over52": int(r["over_52_weeks"]),
        "pct": None if pd.isna(r["pct_within_18_weeks"]) else round(float(r["pct_within_18_weeks"]), 4),
        "band": r["performance_band"],
    } for _, r in trust_summary.iterrows()]

    # National specialty breakdown for the most recent month.
    spec = (clean_specialty.groupby("specialty_name", as_index=False)
            [["total_waiting", "within_18_weeks", "over_52_weeks", "weighted_weeks"]].sum())
    spec["pct"] = (spec["within_18_weeks"] / spec["total_waiting"]).where(spec["total_waiting"] > 0)
    spec["est_mean_weeks"] = (spec["weighted_weeks"] / spec["total_waiting"]).where(spec["total_waiting"] > 0)
    spec = spec.sort_values("total_waiting", ascending=False)
    specialties_out = [{
        "name": r["specialty_name"],
        "total": int(r["total_waiting"]),
        "within18": int(r["within_18_weeks"]),
        "over52": int(r["over_52_weeks"]),
        "pct": None if pd.isna(r["pct"]) else round(float(r["pct"]), 4),
        "est_mean_weeks": None if pd.isna(r["est_mean_weeks"]) else round(float(r["est_mean_weeks"]), 1),
    } for _, r in spec.iterrows()]

    # Trust x specialty matrix, stored compactly as index references so the file
    # stays small. The dashboard renders it filtered (by ICB/band) so the DOM
    # never tries to draw every trust at once.
    trust_codes = trust_summary["trust_code"].tolist()
    trust_names = trust_summary["trust_name"].tolist()
    trust_index = {code: i for i, code in enumerate(trust_codes)}
    specialty_names = spec["specialty_name"].tolist()
    specialty_index = {name: i for i, name in enumerate(specialty_names)}
    cells = []
    for row in clean_specialty.itertuples(index=False):
        ti = trust_index.get(row.trust_code)
        si = specialty_index.get(row.specialty_name)
        if ti is None or si is None:
            continue
        cells.append([ti, si, int(row.within_18_weeks), int(row.total_waiting)])

    data = {
        "meta": {
            "latest_period": latest_label,
            "latest_period_date": last["period_date"],
            "interim_target": INTERIM_TARGET,
            "constitutional_standard": CONSTITUTIONAL_STANDARD,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "provider_count": len(trust_codes),
            "national": {
                "total_waiting": int(last["total_waiting"]),
                "within_18_weeks": int(last["within_18_weeks"]),
                "over_52_weeks": int(last["over_52_weeks"]),
                "pct_within_18_weeks": None if pd.isna(last["pct_within_18_weeks"]) else float(last["pct_within_18_weeks"]),
                "trusts_total": int(last["trusts_total"]),
                "trusts_meeting": int(last["trusts_meeting_target"]),
                "trusts_missing": int(last["trusts_missing_target"]),
            },
        },
        "trend": trend_out,
        "trusts": trusts_out,
        "specialties": specialties_out,
        "matrix": {
            "trusts": trust_codes,
            "trust_names": trust_names,
            "specialties": specialty_names,
            "cells": cells,
        },
        "icbs": sorted(trust_summary["icb_name"].dropna().unique().tolist()),
    }

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    js = ("// Auto-generated by scripts/clean_rtt_data.py - do not edit by hand.\n"
          "// Regenerate by re-running the cleaning script after refreshing the data.\n"
          "const DASHBOARD_DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    (DASHBOARD_DIR / "dashboard_data.js").write_text(js, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    print("NHS RTT waiting list - cleaning pipeline")
    print("-" * 48)

    ensure_raw_data()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    csv_paths = sorted(RAW_DIR.glob("*full-extract*.csv"))
    if not csv_paths:
        raise SystemExit("No raw CSV extracts found in data/raw/. Run again with a connection so they can download.")

    # Work out each file's month, so we know which one is the latest and should
    # be kept at full trust x specialty detail (the detail pages are a snapshot
    # of the most recent month; the trend uses every month).
    file_dates = {}
    for path in csv_paths:
        label = pd.read_csv(path, usecols=["Period"], nrows=1)["Period"].iloc[0]
        file_dates[path] = period_to_date(label)
    latest_path = max(file_dates, key=file_dates.get)

    trend_rows = []
    latest_detail = None  # provider x commissioner x specialty rows, latest month

    for path in sorted(csv_paths, key=lambda p: file_dates[p]):
        month = load_month(path)
        # Drop the 'Total' specialty roll-up so specialties never double-count.
        detail = month[month["specialty_code"] != TOTAL_SPECIALTY_CODE].copy()

        # Trust totals for the month: sum across commissioners and specialties.
        per_trust = (detail.groupby(["trust_code", "trust_name", "icb_name"], as_index=False)
                     [["total_waiting", "within_18_weeks", "over_52_weeks"]].sum())
        per_trust["pct_within_18_weeks"] = (
            per_trust["within_18_weeks"] / per_trust["total_waiting"]
        ).where(per_trust["total_waiting"] > 0)

        # National figures and how many trusts met the target this month.
        rated = per_trust[per_trust["total_waiting"] > 0]
        total_waiting = per_trust["total_waiting"].sum()
        within_18 = per_trust["within_18_weeks"].sum()
        trend_rows.append({
            "period": str(month["period"].iloc[0]),
            "period_date": file_dates[path].date().isoformat(),
            "total_waiting": int(total_waiting),
            "within_18_weeks": int(within_18),
            "over_52_weeks": int(per_trust["over_52_weeks"].sum()),
            "pct_within_18_weeks": round(within_18 / total_waiting, 4) if total_waiting else None,
            "trusts_total": int(len(rated)),
            "trusts_meeting_target": int((rated["pct_within_18_weeks"] >= INTERIM_TARGET).sum()),
            "trusts_missing_target": int((rated["pct_within_18_weeks"] < INTERIM_TARGET).sum()),
        })

        if path == latest_path:
            latest_detail = detail
        print(f"  processed {file_dates[path].strftime('%b %Y')}: "
              f"{len(per_trust)} providers, {int(total_waiting):,} waiting")

    # --- Output 1: detailed clean file (latest month, trust x specialty) ------
    group_cols = ["period", "trust_code", "trust_name", "icb_name",
                  "specialty_code", "specialty_name"]
    clean = (latest_detail.groupby(group_cols, as_index=False)
             [["total_waiting", "within_18_weeks", "over_52_weeks", "weighted_weeks"]].sum())
    clean["period_date"] = file_dates[latest_path].date().isoformat()
    clean["pct_within_18_weeks"] = (
        clean["within_18_weeks"] / clean["total_waiting"]
    ).where(clean["total_waiting"] > 0).round(4)
    # missed_target = True where performance is below the 70% interim target.
    clean["missed_target"] = clean["pct_within_18_weeks"] < INTERIM_TARGET
    for col in ["total_waiting", "within_18_weeks", "over_52_weeks"]:
        clean[col] = clean[col].astype(int)
    clean_out = clean[["period", "period_date", "trust_code", "trust_name", "icb_name",
                       "specialty_code", "specialty_name", "total_waiting",
                       "within_18_weeks", "over_52_weeks", "pct_within_18_weeks",
                       "missed_target"]].sort_values(["trust_name", "specialty_name"])
    clean_out.to_csv(PROCESSED_DIR / "rtt_clean.csv", index=False)

    # --- Output 2: trust-level summary (latest month, one row per trust) ------
    summary = (latest_detail.groupby(["trust_code", "trust_name", "icb_name"], as_index=False)
               [["total_waiting", "within_18_weeks", "over_52_weeks"]].sum())
    summary["period"] = file_dates[latest_path].strftime("%B %Y")
    summary["pct_within_18_weeks"] = (
        summary["within_18_weeks"] / summary["total_waiting"]
    ).where(summary["total_waiting"] > 0).round(4)
    summary["performance_band"] = summary["pct_within_18_weeks"].apply(performance_band)
    summary["missed_target"] = summary["pct_within_18_weeks"] < INTERIM_TARGET
    for col in ["total_waiting", "within_18_weeks", "over_52_weeks"]:
        summary[col] = summary[col].astype(int)
    summary_out = summary[["trust_code", "trust_name", "icb_name", "period",
                           "total_waiting", "within_18_weeks", "over_52_weeks",
                           "pct_within_18_weeks", "performance_band",
                           "missed_target"]].sort_values("pct_within_18_weeks")
    summary_out.to_csv(PROCESSED_DIR / "rtt_trust_summary.csv", index=False)

    # --- Output 3: national monthly trend (drives the time-series chart) ------
    trend = pd.DataFrame(trend_rows).sort_values("period_date").reset_index(drop=True)
    trend.to_csv(PROCESSED_DIR / "rtt_national_trend.csv", index=False)

    # --- Output 4: data file the HTML dashboard reads ------------------------
    write_dashboard_data(clean, summary_out, trend)

    # --- Console summary so the run can be sanity-checked --------------------
    latest = trend.iloc[-1]
    print("-" * 48)
    print(f"Latest month            : {file_dates[latest_path].strftime('%B %Y')}")
    print(f"Total patients waiting  : {int(latest['total_waiting']):,}")
    print(f"% within 18 weeks       : {latest['pct_within_18_weeks']:.1%}")
    print(f"Waiting over 52 weeks   : {int(latest['over_52_weeks']):,}")
    print(f"Trusts meeting 70%      : {int(latest['trusts_meeting_target'])} of {int(latest['trusts_total'])}")
    print(f"Months in trend         : {len(trend)}")
    print(f"rtt_clean.csv rows       : {len(clean_out):,}")
    print(f"rtt_trust_summary.csv    : {len(summary_out):,} trusts")
    print("Wrote: rtt_clean.csv, rtt_trust_summary.csv, rtt_national_trend.csv, dashboard_data.js")


if __name__ == "__main__":
    main()
