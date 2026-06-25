# Building the dashboard in Power BI Desktop

This repository ships a live, self-contained HTML dashboard (`dashboard/index.html`)
that anyone can open in a browser. This guide is for rebuilding the same views in
**Power BI Desktop** from the processed CSVs, for people who prefer Power BI or want
a `.pbix` for their portfolio.

A `.pbix` file is a proprietary binary that only Power BI Desktop can create, so it
is not committed here — follow the steps below to produce it yourself. Power BI
Desktop is a free download from Microsoft.

## 1. Data to connect to

Run `scripts/clean_rtt_data.py` first, then connect to the three files in
`data/processed/`:

| File | Grain | Used for |
|------|-------|----------|
| `rtt_clean.csv` | one row per trust × specialty (latest month) | KPI cards, specialty matrix, headline measures |
| `rtt_trust_summary.csv` | one row per trust (latest month) | trust counts vs target, trust ranking, the relationship |
| `rtt_national_trend.csv` | one row per month (13 months) | the "% within 18 weeks over time" line chart |

> The monthly snapshot files (`rtt_clean`, `rtt_trust_summary`) hold the most recent
> month only, so the time-series chart reads from `rtt_national_trend.csv` instead.

## 2. Connect and check column types

For each file: **Home → Get Data → Text/CSV**, select the file, then **Transform Data**
and confirm the types in Power Query before loading:

- `period_date` → **Date**
- `pct_within_18_weeks` → **Decimal Number**
- `total_waiting`, `within_18_weeks`, `over_52_weeks`, `trusts_total`,
  `trusts_meeting_target`, `trusts_missing_target` → **Whole Number**
- `missed_target` → **True/False**
- everything else (`trust_code`, `trust_name`, `icb_name`, `specialty_name`,
  `performance_band`, `period`) → **Text**

Then **Close & Apply**.

## 3. Model relationships

In **Model** view:

- `rtt_trust_summary[trust_code]` (1) → `rtt_clean[trust_code]` (\*), single cross-filter
  direction. `trust_code` is unique in `rtt_trust_summary`, so this is a valid
  one-to-many relationship.
- `rtt_national_trend` needs no relationship — it is national and stands alone.

## 4. DAX measures

Create a dedicated measures table: **Home → Enter Data**, name it `_Measures`, load it,
then delete its placeholder column. Add these measures (the first six are the core set):

```DAX
Total Patients Waiting = SUM(rtt_clean[total_waiting])

Pct Within 18 Weeks =
DIVIDE(SUM(rtt_clean[within_18_weeks]), SUM(rtt_clean[total_waiting]))

Trusts Meeting Target =
COUNTROWS(FILTER(rtt_trust_summary, rtt_trust_summary[pct_within_18_weeks] >= 0.70))

Trusts Missing Target =
COUNTROWS(FILTER(rtt_trust_summary, rtt_trust_summary[pct_within_18_weeks] < 0.70))

Over 52 Weeks Total = SUM(rtt_clean[over_52_weeks])

Target Met Flag =
IF([Pct Within 18 Weeks] >= 0.70, "Met", "Missed")
```

One extra measure for the trend line (it reads the monthly table):

```DAX
Pct Within 18 Weeks (Monthly) =
DIVIDE(SUM(rtt_national_trend[within_18_weeks]), SUM(rtt_national_trend[total_waiting]))
```

Set the format of both percentage measures to **Percentage** (1 decimal place).

## 5. Apply the NHS theme

**View → Themes → Browse for themes**, then select `dashboard/nhs_theme.json`.
This sets the NHS data colours and Arial as the default font. Set each page's
background (Format page → Canvas background) to white at 0% transparency.

## 6. Page 1 — National Overview

- **Four KPI cards** across the top (Card visual): `Total Patients Waiting`,
  `Pct Within 18 Weeks` (formatted %), `Trusts Meeting Target`, `Over 52 Weeks Total`.
- **Line chart** — "% within 18 weeks over time":
  - X axis: `rtt_national_trend[period_date]`
  - Y axis: `[Pct Within 18 Weeks (Monthly)]`
  - Add two constant lines (Analytics pane → Constant line): one at **0.70** (interim
    target, NHS Aqua), one at **0.92** (constitutional standard, NHS Red).
- **Donut chart** — proportion meeting vs missing: use `Trusts Meeting Target` and
  `Trusts Missing Target` (or `rtt_trust_summary[performance_band]` grouped).
- **Slicer** on `rtt_national_trend[period_date]` (or `period`) to filter the page.
- Title each visual in NHS Blue `#003087`.

## 7. Page 2 — Trust Performance

- **Horizontal bar chart** — trusts ranked by % within 18 weeks:
  - Axis: `rtt_trust_summary[trust_name]`, Value: `rtt_trust_summary[pct_within_18_weeks]`,
    sorted ascending.
  - Conditional colour (Format → Bars → Colour → fx → Format style **Rules**, based on
    `pct_within_18_weeks`):
    - `>= 0` and `< 0.70` → `#DA291C` (red)
    - `>= 0.70` and `< 0.80` → `#FFB81C` (amber)
    - `>= 0.80` and `<= 1` → `#009639` (green)
  - Tooltip fields: `trust_name`, `total_waiting`, `over_52_weeks`, `[Target Met Flag]`.
- **Matrix** — Rows: `rtt_clean[trust_name]`, Columns: `rtt_clean[specialty_name]`,
  Values: `[Pct Within 18 Weeks]`. Apply the same rules-based conditional formatting to
  the values (Format → Cell elements → Background colour → Rules).
- **Slicer** on `rtt_trust_summary[icb_name]` (ICB region).
- **Slicer** on `rtt_trust_summary[performance_band]` to isolate failing trusts.

## 8. Page 3 — Specialty Breakdown

- **Bar chart** — average wait by specialty (top 15): Axis `rtt_clean[specialty_name]`,
  Value `[Total Patients Waiting]` (or build an estimated-mean-wait measure). Use the
  visual's **Top N** filter on the value to keep the 15 longest.
- **Scatter chart**: X = `[Total Patients Waiting]`, Y = `[Pct Within 18 Weeks]`,
  Details = `rtt_clean[trust_name]`, Legend = `rtt_trust_summary[performance_band]`,
  Size = `[Over 52 Weeks Total]`.
- **Drillthrough page**: create a hidden page with a `trust_name` drillthrough filter and
  a table of that trust's specialties; right-click a scatter point → Drillthrough.

## 9. Save and export

- Save as `dashboard/nhs_rtt_dashboard.pbix`.
- **File → Export → Export to PDF** for a shareable copy of the report.
- Use the Windows Snipping Tool (or **Win+Shift+S**) to capture page screenshots into
  `screenshots/` if you want them in the README.
