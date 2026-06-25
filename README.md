# NHS RTT Waiting List Dashboard

This project tracks how England's NHS is performing against the 18-week Referral to
Treatment (RTT) standard, the rule that patients should wait no longer than 18 weeks
from referral to the start of consultant-led treatment. Performance has sat well below
the 92% constitutional standard for years, and NHS England's Medium Term Planning
Framework 2026-2029 sets a staged recovery, with an interim milestone of 70% of
patients waiting 18 weeks or less. The dashboard takes NHS England's monthly open data
and turns it into something you can actually read: the national trend, how individual
trusts compare, and where the longest waits sit by specialty.

The live dashboard is published with GitHub Pages:
https://Moulyas3.github.io/nhs-rtt-waiting-list-dashboard/

The snapshot committed to this repository covers April 2026, with a 13-month national
trend running from April 2025. Re-running the cleaning script with newer files updates
everything automatically.

## Data source

All figures come from NHS England's published RTT waiting times statistics, released
under the Open Government Licence v3.0:

https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/

NHS England updates this data monthly, roughly six weeks after each reporting month.
The pipeline uses the "Full CSV data file" extracts, which give a provider-level
breakdown by treatment function (specialty) and waiting-time band. The dashboard
reports on incomplete pathways, the patients still waiting at the end of the month,
because that is the group the 18-week standard is measured against.

## Dashboard features

The dashboard has three pages.

**National Overview** shows the headline position for a chosen month: total patients
waiting, the percentage seen within 18 weeks, how many trusts are meeting the 70%
target, and how many patients have waited more than a year. A line chart plots the
percentage within 18 weeks across every available month against the 70% target and the
92% standard, and a doughnut chart splits trusts into those meeting and missing the
target. A month selector drives the cards and the doughnut.

**Trust Performance** ranks providers by their percentage within 18 weeks, colouring
each bar red below 70%, amber between 70% and 80%, and green above 80%. A heat-map
table breaks performance down by trust and specialty using the same colours. Filters
for Integrated Care Board (ICB) region and performance band let you narrow the view to
a local area or isolate the trusts that are missing the target.

**Specialty Breakdown** shows the specialties with the longest estimated waits, and a
scatter plot of every trust with list size on one axis and performance on the other,
sized by the number of 52-week waits. Clicking any point drills into that trust's
specialty detail.

## How to use

The quickest way to see it is the live link above; it needs nothing installed.

To run it locally and regenerate the data:

1. Clone the repository.
2. (Optional) create a virtual environment: `python -m venv venv` and activate it.
3. Install the dependencies: `pip install -r requirements.txt`.
4. Run the cleaning script: `python scripts/clean_rtt_data.py`. On first run this
   downloads the raw NHS files into `data/raw/` (about 1 GB across 13 months), then
   writes the cleaned outputs to `data/processed/` and refreshes the dashboard's data
   file. Later runs reuse anything already downloaded.
5. Open `dashboard/index.html` in any browser.

To refresh with a newer month, add its "Full CSV data file" URL to the `SOURCE_FILES`
dictionary at the top of `scripts/clean_rtt_data.py` and run the script again. Nothing
else needs changing; the dashboard reads whatever the script produces.

If you would rather build this in Power BI Desktop (a free download from Microsoft),
follow `dashboard/POWERBI_GUIDE.md`. It lists the data connections, the column types to
set in Power Query, the table relationship, the DAX measures, and the layout for each
page, and it uses the NHS colour theme in `dashboard/nhs_theme.json`. If you move the
processed files, update the path in Power BI under Transform Data, Data Source Settings,
then refresh.

## Requirements

- Python 3.8 or newer
- pandas and requests (see `requirements.txt`; the rest of the pipeline uses the
  standard library)
- Power BI Desktop (free) only if you want to build the Power BI version

## How the figures are calculated

For each provider and specialty the script sums the weekly waiting-time bands:
patients within 18 weeks are the bands up to and including 18 weeks, and patients over
52 weeks are the bands beyond 52 weeks. The total waiting is the sum of all bands. The
percentage within 18 weeks is patients within 18 weeks divided by the total, and
`missed_target` is set where that percentage falls below 0.70. Trust totals are summed
across commissioners and specialties, and the all-specialty roll-up row that NHS England
includes is dropped so nothing is double-counted.

## Known limitations

- NHS England suppresses very small counts and marks them with an asterisk. These are
  read as missing and treated as zero, and any row without a usable total is dropped, so
  a handful of patients in the smallest categories may not be reflected.
- Trusts merge and change boundaries over time, and the set of reporting providers
  shifts month to month, so comparisons across the trend should be read as broad
  direction rather than like-for-like.
- The Trust Performance and Specialty Breakdown pages are a snapshot of the most recent
  month. Only the national trend line covers the full period.
- The "average wait by specialty" is an estimate. The source data gives counts in
  weekly bands rather than exact waits, so the figure uses band midpoints.
- Independent-sector providers are included alongside NHS trusts, which is why some very
  small providers appear at or near 100%.

## Contributing

Pull requests are welcome. A few things that would be useful to add: deeper specialty
drill-downs, aggregation up to Integrated Care Board level, and an automated monthly
refresh using GitHub Actions so the dashboard updates itself when NHS England publishes
new data.

## Licence

Released under the MIT Licence. See [LICENSE](LICENSE).
