"""Quarterly Health Bulletin: the data pipeline.

Reads the five DHIS2-like CSV exports, checks them for data-quality problems,
and calculates the bulletin figures, with a coverage figure next to every
quarterly number.

Design rules, applied throughout this file
------------------------------------------
1. Report data problems; never fix them silently. The only step that changes
   row counts is the documented duplicate rule, and every facility-month it
   touches is flagged in the outputs.
2. Stop early on structural problems (a missing file, a missing column, facility
   lists that disagree). A bulletin built on a broken input is worse than no
   bulletin, because nobody can tell it is broken.
3. Keep business rules out of the code. Anything the M&E unit owns lives in
   config.py, so changing a rule never means editing this file.
4. One run feeds every output. run() returns plain DataFrames; bulletin.py and
   build_site.py only format them, so the HTML bulletin, the dashboard, the PDF
   and the CSVs cannot disagree.

Run
---
    python bulletin.py       simple HTML bulletin and the two CSVs, in output/
    python build_site.py     dashboard, PDF and CSVs in site/, ready for Netlify
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

import config as cfg

log = logging.getLogger("bulletin")

KEY = ["facility_id", "reporting_month"]

CAUSE_COLUMNS = [
    "death_birth_asphyxia",
    "death_prematurity",
    "death_sepsis",
    "death_congenital",
    "death_other",
]

COUNT_COLUMNS = [
    "total_deliveries", "live_births", "stillbirths",
    "neonatal_deaths_0_7d", "neonatal_deaths_8_28d",
    "preterm_births_28_32w", "preterm_births_32_37w",
    "birth_weight_less_2500g", "apgar_less_7_at_5min",
] + CAUSE_COLUMNS

# The columns each file must contain for the pipeline to run. This is the
# prototype's stand-in for the data contract described in Part 3 of the PDF:
# a renamed column fails here with a clear message instead of silently
# becoming an empty column further down.
REQUIRED_COLUMNS = {
    "clinical_neonatal": KEY + COUNT_COLUMNS,
    "facilities": ["facility_id", "facility_name", "district", "province",
                   "tier_level", "gps_lat", "gps_lon"],
    "governance": ["facility_id", "hmis_reporting_completeness",
                   *cfg.PERCENT_COLUMNS, *cfg.DATE_COLUMNS["governance"]],
    "healthcare_workers": ["facility_id", *cfg.DATE_COLUMNS["healthcare_workers"]],
    "operations": ["facility_id"],
}

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class DataError(ValueError):
    """The input cannot produce a trustworthy bulletin. The message says why."""


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------
def load_all(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Load the five exports and check they fit together before anything else.

    IDs and months are read as text. Pandas would otherwise guess their types,
    and a guessed type is a silent change to the data.
    """
    frames = {}
    for name, required in REQUIRED_COLUMNS.items():
        path = Path(data_dir) / f"{name}.csv"
        if not path.exists():
            raise DataError(f"Missing input file: {path}")
        df = pd.read_csv(path, dtype={"facility_id": str, "reporting_month": str})
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise DataError(f"{path.name} is missing required columns: {', '.join(missing)}")
        frames[name] = df
        log.info("  loaded %-20s %6d rows", name, len(df))

    # Every later join is on facility_id. If the five files list different
    # facilities, those joins drop rows without warning, so we stop here.
    base = set(frames["facilities"].facility_id)
    for name, df in frames.items():
        ids = set(df.facility_id)
        if ids != base:
            extra, absent = sorted(ids - base)[:5], sorted(base - ids)[:5]
            raise DataError(f"facility_id list in {name} differs from facilities.csv "
                            f"(extra: {extra}, absent: {absent})")

    bad = ~frames["clinical_neonatal"].reporting_month.str.match(MONTH_PATTERN)
    if bad.any():
        sample = frames["clinical_neonatal"].reporting_month[bad].unique()[:5]
        raise DataError(f"reporting_month must be YYYY-MM; found {list(sample)}")
    return frames


# ---------------------------------------------------------------------------
# 2. Profile: find problems before any figure is calculated
# ---------------------------------------------------------------------------
def _issue(issues: list, name: str, detail: str, count, ids=None) -> None:
    """Append one data-quality finding. The first ten IDs travel with it, so
    the M&E unit can start fixing without rerunning anything."""
    ids = list(ids) if ids is not None else []
    issues.append({
        "issue": name,
        "detail": detail,
        "count": int(count),
        "affected_ids": "; ".join(map(str, ids[:10])),
        "affected_ids_total": len(ids),
    })


def profile_clinical(df: pd.DataFrame, issues: list) -> None:
    """Check the monthly clinical export.

    The last three checks find nothing on this sample. They stay because they
    cost nothing, and a check added only after it would have fired has already
    let one wrong bulletin out.
    """
    # Repeated facility-months. Identical copies would be a harmless double
    # export. Different values mean two submissions exist and nobody has said
    # which is right, which is a decision for the M&E unit, not for the code.
    dup_mask = df.duplicated(KEY, keep=False)
    dup_keys = df.loc[dup_mask, KEY].drop_duplicates()
    exact = df.duplicated(keep=False).sum()
    _issue(issues, "Facility-months reported twice with different values",
           f"{len(dup_keys)} facility-months have two reports that disagree "
           f"({exact} rows are exact copies). Months affected: "
           f"{', '.join(sorted(dup_keys.reporting_month.unique()))}.",
           dup_mask.sum(),
           (dup_keys.facility_id + " " + dup_keys.reporting_month).tolist())

    # Missing months, checked per facility against the expected calendar.
    present = df.groupby("facility_id").reporting_month.apply(set)
    missing_by_month: dict[str, list] = {}
    for fid, months in present.items():
        for m in set(cfg.EXPECTED_MONTHS) - months:
            missing_by_month.setdefault(m, []).append(fid)
    for m, fids in sorted(missing_by_month.items()):
        _issue(issues, "Missing reporting month",
               f"{m} is absent for {len(fids)} of {df.facility_id.nunique()} "
               f"facilities.", len(fids), sorted(fids))

    # Checks that should always pass. Reported, not raised: one bad row should
    # be flagged for correction, not stop the whole bulletin.
    neg = (df[COUNT_COLUMNS] < 0).any(axis=1)
    _issue(issues, "Negative values", "Rows with any negative count.",
           neg.sum(), df.loc[neg, "facility_id"].tolist())

    lb_gt = df.live_births > df.total_deliveries
    _issue(issues, "Live births exceed deliveries",
           "Impossible by definition; indicates a data entry error.",
           lb_gt.sum(), df.loc[lb_gt, "facility_id"].tolist())

    total_deaths = df.neonatal_deaths_0_7d + df.neonatal_deaths_8_28d
    mismatch = df[CAUSE_COLUMNS].sum(axis=1) != total_deaths
    _issue(issues, "Cause-of-death columns do not sum to total deaths",
           "Each neonatal death should have exactly one recorded cause.",
           mismatch.sum(), df.loc[mismatch, "facility_id"].tolist())


def profile_reference(facilities: pd.DataFrame, governance: pd.DataFrame,
                      workers: pd.DataFrame, issues: list) -> None:
    """Check the facility reference tables."""
    # Percentages stored as text read fine in Excel and break all arithmetic.
    pct_cols = [c for c in governance.columns
                if governance[c].astype(str).str.endswith("%").any()]
    _issue(issues, "Percent values stored as text",
           f"Columns: {', '.join(pct_cols)}.", len(pct_cols), pct_cols)

    # Never in a date field carries meaning (the training never happened), but
    # it breaks date parsing. It becomes a missing date and is counted here.
    for frame, name in ((governance, "governance"), (workers, "healthcare_workers")):
        for col in cfg.DATE_COLUMNS[name]:
            never = frame[col] == "Never"
            _issue(issues, "Never used as a date value",
                   f"{name}.{col}: treated as missing, not as a date.",
                   never.sum(), frame.loc[never, "facility_id"].tolist())

    # Tier versus name. The data cannot say which one is right, and both the
    # province tables and Health Atlas depend on tier, so this is flagged for
    # the M&E unit. Province figures in this prototype do not use tier.
    name, tier = facilities.facility_name, facilities.tier_level
    conflict = ((name.str.contains("Health Center") & tier.isin(["Provincial", "Referral"]))
                | (name.str.contains("Hospital") & (tier == "Health Center")))
    _issue(issues, "Facility tier contradicts facility name",
           "For example, a health center tagged as a referral hospital. "
           "The data cannot tell which one is right.",
           conflict.sum(), facilities.loc[conflict, "facility_id"].tolist())

    # GPS. A rough test: more than 0.5 degrees from the district's median point.
    # It only says a coordinate is worth checking; the real test is the
    # Ministry's confirmed facility master list.
    med = facilities.groupby("district")[["gps_lat", "gps_lon"]].transform("median")
    far = ((facilities.gps_lat - med.gps_lat).abs() > 0.5) | \
          ((facilities.gps_lon - med.gps_lon).abs() > 0.5)
    _issue(issues, "GPS coordinates far from district median",
           "More than 0.5 degrees from the middle of their district. When this "
           "affects most facilities, the whole coordinate set is unreliable, "
           "so Health Atlas should not use it until the facility master is "
           "confirmed.",
           far.sum(), facilities.loc[far, "facility_id"].tolist())


# ---------------------------------------------------------------------------
# 3. Clean: the only steps that change the data, each one documented
# ---------------------------------------------------------------------------
# Duplicate rules, by name. Each takes the rows of the export in file order and
# returns one row per facility-month. Adding the M&E unit's rule means adding
# one entry here and changing DUPLICATE_RULE in config.py.
DUPLICATE_RULES = {
    "keep_first": lambda df: df.drop_duplicates(KEY, keep="first"),
    "keep_last": lambda df: df.drop_duplicates(KEY, keep="last"),
}


def clean_clinical(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the duplicate rule and add the quarter.

    Every facility-month that had more than one report is marked
    conflicting_export=True, whichever rule ran, so a reader can always see
    which figures rest on an unresolved choice.
    """
    try:
        rule = DUPLICATE_RULES[cfg.DUPLICATE_RULE]
    except KeyError:
        raise DataError(f"Unknown DUPLICATE_RULE {cfg.DUPLICATE_RULE!r}; "
                        f"choose one of {sorted(DUPLICATE_RULES)}") from None
    df = df.copy()
    df["conflicting_export"] = df.duplicated(KEY, keep=False)
    df = rule(df).reset_index(drop=True)
    df["quarter"] = pd.PeriodIndex(df.reporting_month, freq="M").asfreq("Q").astype(str)
    return df


def clean_reference(facilities: pd.DataFrame, governance: pd.DataFrame,
                    workers: pd.DataFrame):
    """Turn percent text into numbers and Never into a missing date."""
    governance, workers = governance.copy(), workers.copy()
    for col in cfg.PERCENT_COLUMNS:
        values = pd.to_numeric(governance[col].astype(str).str.rstrip("%"), errors="coerce")
        out_of_range = ~values.between(0, 100) & values.notna()
        if out_of_range.any():
            raise DataError(f"{col} has values outside 0 to 100: "
                            f"{values[out_of_range].head(5).tolist()}")
        governance[col] = values
    for frame, name in ((governance, "governance"), (workers, "healthcare_workers")):
        for col in cfg.DATE_COLUMNS[name]:
            frame[col] = frame[col].replace("Never", pd.NA)
    return facilities, governance, workers


# ---------------------------------------------------------------------------
# 4. Metrics
# ---------------------------------------------------------------------------
def _rate(numerator: pd.Series, denominator: pd.Series, per: int) -> pd.Series:
    """numerator / denominator x per, with a missing value instead of an error
    or an infinite number when the denominator is zero (a province or quarter
    with no live births must show a gap, not a misleading figure)."""
    return numerator / denominator.where(denominator != 0) * per


def expected_months_by_quarter() -> pd.Series:
    """How many months each quarter should have, taken from the calendar, so a
    partial year (for example a bulletin that starts in March) is handled."""
    q = pd.PeriodIndex(cfg.EXPECTED_MONTHS, freq="M").asfreq("Q").astype(str)
    return pd.Series(q).value_counts().sort_index()


def coverage_by_quarter(clinical: pd.DataFrame) -> pd.DataFrame:
    """How much of each quarter is actually in the data.

    This table sits next to every quarterly figure. A total without its month
    count is how a 32% fall in deliveries gets quoted for a quarter that simply
    had one month missing from the export.
    """
    g = clinical.groupby("quarter")
    cov = pd.DataFrame({
        "months_present": g.reporting_month.nunique(),
        "facilities_reporting": g.facility_id.nunique(),
    }).reset_index()
    cov["months_expected"] = cov.quarter.map(expected_months_by_quarter()).fillna(3).astype(int)
    cov["coverage"] = cov.months_present / cov.months_expected
    cov["low_coverage"] = cov.coverage < cfg.FULL_QUARTER_SHARE
    cov["coverage_pct"] = (cov.coverage * 100).round(0).astype(int)
    months = clinical.groupby("quarter").reporting_month.agg(lambda s: ", ".join(sorted(s.unique())))
    cov["months_in_data"] = cov.quarter.map(months)
    return cov


def metric_top_facilities(clinical: pd.DataFrame, facilities: pd.DataFrame,
                          quarter: str, n: int) -> pd.DataFrame:
    """The n facilities with the most deliveries in one quarter.

    The repeated-report flag covers the whole year, not just this quarter, so a
    reader sees when a facility's figures rest on an unresolved export.
    Ties are broken by facility_id so the ranking is the same on every run.
    """
    q = clinical[clinical.quarter == quarter]
    agg = q.groupby("facility_id").agg(
        deliveries=("total_deliveries", "sum"),
        months_present=("reporting_month", "nunique"),
    ).reset_index()
    conflict_year = (clinical.groupby("facility_id").conflicting_export.any()
                     .rename("conflicting_in_year"))
    out = (agg.merge(conflict_year, on="facility_id")
              .merge(facilities[["facility_id", "facility_name", "district", "province"]],
                     on="facility_id")
              .sort_values(["deliveries", "facility_id"], ascending=[False, True])
              .head(n).reset_index(drop=True))
    out.index = out.index + 1          # rank 1..n
    return out


def metric_neonatal_indicators(clinical: pd.DataFrame, facilities: pd.DataFrame | None = None,
                               by: str | None = None) -> pd.DataFrame:
    """Neonatal and maternal indicators per quarter, nationally or by province.

    Formulas, in one place:
      stillbirth rate       = stillbirths / (live births + stillbirths) x 1,000
      neonatal mortality    = deaths / live births x 1,000
                              (early 0 to 7 days, late 8 to 28 days)
      preterm, LBW, Apgar<7 = count / live births x 100
      cause of death        = deaths from the cause / all neonatal deaths x 100

    Counts are summed first and rates calculated from the sums, never averaged
    from facility rates, so large and small facilities weigh what they should.
    Antenatal care visits are not in this export, so there is no ANC indicator;
    the gap is reported rather than filled with a proxy.
    """
    df = clinical
    if by == "province":
        df = df.merge(facilities[["facility_id", "province"]], on="facility_id")
    elif by is not None:
        raise ValueError(f"by must be None or 'province', not {by!r}")
    keys = ["quarter"] + ([by] if by else [])
    g = df.groupby(keys)
    s = g[COUNT_COLUMNS].sum()

    out = pd.DataFrame(index=s.index)
    out["deliveries"] = s.total_deliveries
    out["live_births"] = s.live_births
    out["stillbirths"] = s.stillbirths
    out["stillbirth_rate_per_1000"] = _rate(s.stillbirths, s.live_births + s.stillbirths, 1000)
    out["early_nmr_per_1000"] = _rate(s.neonatal_deaths_0_7d, s.live_births, 1000)
    out["late_nmr_per_1000"] = _rate(s.neonatal_deaths_8_28d, s.live_births, 1000)
    out["nmr_per_1000"] = out.early_nmr_per_1000 + out.late_nmr_per_1000
    out["preterm_pct"] = _rate(s.preterm_births_28_32w + s.preterm_births_32_37w, s.live_births, 100)
    out["lbw_pct"] = _rate(s.birth_weight_less_2500g, s.live_births, 100)
    out["apgar_lt7_pct"] = _rate(s.apgar_less_7_at_5min, s.live_births, 100)
    deaths = s[CAUSE_COLUMNS].sum(axis=1)
    for c in CAUSE_COLUMNS:
        out[c.replace("death_", "cause_") + "_pct"] = _rate(s[c], deaths, 100)
    out["months_present"] = g.reporting_month.nunique()
    return out.round(1).reset_index()


def metric_facility_performance(clinical: pd.DataFrame, governance: pd.DataFrame,
                                facilities: pd.DataFrame) -> pd.DataFrame:
    """Completeness seen in the data against the figure the facility reports.

    Observed = months with a report / months expected. Self-reported = the
    hmis_reporting_completeness column in governance.csv. Where the two differ,
    that is a finding for the M&E unit; the prototype shows both and does not
    pick one. Timeliness cannot be measured: the export has no date for when
    each report arrived (DHIS2 records it, so it comes with the connection).
    """
    g = clinical.groupby("facility_id")
    perf = pd.DataFrame({
        "months_present": g.reporting_month.nunique(),
        "last_month_reported": g.reporting_month.max(),
        "conflicting_months": g.conflicting_export.sum(),
    }).reset_index()
    perf["observed_completeness"] = (perf.months_present / len(cfg.EXPECTED_MONTHS) * 100).round(1)
    perf = perf.merge(governance[["facility_id", "hmis_reporting_completeness"]], on="facility_id")
    perf = perf.rename(columns={"hmis_reporting_completeness": "self_reported_completeness"})
    perf["gap"] = (perf.observed_completeness - perf.self_reported_completeness).round(1)
    perf = perf.merge(facilities[["facility_id", "facility_name", "district", "province"]],
                      on="facility_id")
    return (perf.sort_values(["gap", "facility_id"], key=lambda c: c.abs() if c.name == "gap" else c,
                             ascending=[False, True])
                .reset_index(drop=True))


def metric_trends(national: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    """Change from the previous quarter, with a comparable flag.

    Two quarters are comparable only when both are complete. On this export
    only Q2 to Q3 qualifies, and the outputs say so rather than drawing a trend
    line through a gap in the export.
    """
    cols = ["deliveries", "stillbirth_rate_per_1000", "nmr_per_1000"]
    t = national[["quarter"] + cols].merge(
        coverage[["quarter", "months_present", "months_expected", "coverage"]], on="quarter")
    for col in cols:
        t[f"{col}_change_pct"] = (t[col].pct_change() * 100).round(1)
    full = t.months_present >= t.months_expected
    t["comparable"] = full & full.shift(1, fill_value=False)
    return t


def facility_reporting_status(perf: pd.DataFrame, clinical: pd.DataFrame) -> pd.DataFrame:
    """Which facilities are reporting, partial or silent: the first piece of
    Problem B, using the same coverage logic per facility instead of per quarter.

      Silent     last report is SILENT_MONTHS_THRESHOLD or more months old
      Partial    fewer reports than expected up to the latest month
      Reporting  everything expected so far is present
    """
    latest = pd.Period(clinical.reporting_month.max(), freq="M")
    expected_so_far = [m for m in cfg.EXPECTED_MONTHS if pd.Period(m, freq="M") <= latest]
    perf = perf.copy()
    months_since = perf.last_month_reported.map(lambda m: (latest - pd.Period(m, freq="M")).n)
    perf["status"] = "Reporting"
    perf.loc[perf.months_present < len(expected_so_far), "status"] = "Partial"
    perf.loc[months_since >= cfg.SILENT_MONTHS_THRESHOLD, "status"] = "Silent"
    cols = ["facility_id", "facility_name", "district", "province",
            "last_month_reported", "months_present", "conflicting_months", "status"]
    return perf[cols].sort_values(["status", "district", "facility_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5. Run
# ---------------------------------------------------------------------------
def run(verbose: bool = True) -> dict:
    """Run the pipeline once and return every table the outputs need.

    Returns plain DataFrames and lists only; formatting belongs to the callers
    (main() below, and build_site.py), so every output shows the same numbers.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    issues: list = []

    log.info("Loading")
    frames = load_all(cfg.DATA_DIR)

    log.info("Checking data quality")
    profile_clinical(frames["clinical_neonatal"], issues)
    profile_reference(frames["facilities"], frames["governance"],
                      frames["healthcare_workers"], issues)

    log.info("Cleaning (duplicate rule: %s)", cfg.DUPLICATE_RULE)
    clinical = clean_clinical(frames["clinical_neonatal"])
    facilities, governance, _workers = clean_reference(
        frames["facilities"], frames["governance"], frames["healthcare_workers"])

    log.info("Calculating")
    coverage = coverage_by_quarter(clinical)
    latest_q = coverage.quarter.max()
    national = metric_neonatal_indicators(clinical)
    by_province = metric_neonatal_indicators(clinical, facilities, by="province")
    top_by_quarter = {q: metric_top_facilities(clinical, facilities, q, cfg.TOP_N)
                      for q in coverage.quarter}
    perf = metric_facility_performance(clinical, governance, facilities)
    trends = metric_trends(national, coverage)
    status = facility_reporting_status(perf, clinical)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "issues": issues,
        "clinical": clinical,
        "facilities": facilities,
        "coverage": coverage,
        "latest_q": latest_q,
        "national": national,
        "by_province": by_province,
        "top_by_quarter": top_by_quarter,
        "perf": perf,
        "trends": trends,
        "status": status,
    }


# ---------------------------------------------------------------------------
# 6. Simple HTML bulletin (python bulletin.py)
# ---------------------------------------------------------------------------
def bulletin_context(r: dict) -> dict:
    """The values the templates need, built from run()."""
    latest_q = r["latest_q"]
    national, coverage, perf, status = r["national"], r["coverage"], r["perf"], r["status"]
    by_province = r["by_province"]
    return {
        "generated_at": r["generated_at"],
        "latest_q": latest_q,
        "headline": national[national.quarter == latest_q].iloc[0],
        "headline_cov": coverage[coverage.quarter == latest_q].iloc[0],
        "coverage": coverage,
        "top": r["top_by_quarter"][latest_q],
        "national": national,
        "by_province": by_province[by_province.quarter == latest_q].drop(columns="quarter"),
        "perf_top": perf.head(15),
        "perf_summary": {
            "n": len(perf),
            "mean_observed": round(perf.observed_completeness.mean(), 1),
            "mean_self": round(perf.self_reported_completeness.mean(), 1),
            "n_over_10": int((perf.gap.abs() > 10).sum()),
        },
        "trends": r["trends"],
        "status_counts": status.status.value_counts().to_dict(),
        "silent": status[status.status == "Silent"],
        "issues": [i for i in r["issues"] if i["count"] > 0],
        "issues_clean": [i for i in r["issues"] if i["count"] == 0],
        "cfg": cfg,
    }


def render_html(context: dict, template_path: Path, out_path: Path) -> None:
    env = Environment(loader=FileSystemLoader(str(Path(template_path).parent)), autoescape=True)
    fmt = dict(classes="tbl", border=0, float_format=lambda x: f"{x:,.1f}", na_rep="")
    # Tables are built by pandas, which escapes cell text itself, so they are
    # marked safe; everything else in the template is escaped by Jinja.
    env.filters["table"] = lambda df: Markup(df.to_html(index=False, **fmt))
    env.filters["table_idx"] = lambda df: Markup(df.to_html(index=True, **fmt))
    html = env.get_template(Path(template_path).name).render(**context)
    Path(out_path).write_text(html, encoding="utf-8")


def main() -> int:
    try:
        r = run()
    except DataError as e:
        log.error("Stopped: %s", e)
        return 2
    out = Path(cfg.OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(r["issues"]).to_csv(out / "data_quality_report.csv", index=False)
    r["status"].to_csv(out / "facility_reporting_status.csv", index=False)
    context = bulletin_context(r)
    render_html(context, cfg.TEMPLATE_PATH, out / "bulletin.html")
    log.info("\nFacilities: %d   quarters: %s", len(r["perf"]), ", ".join(r["coverage"].quarter))
    log.info("Issues with findings: %d   status: %s", len(context["issues"]), context["status_counts"])
    log.info("Bulletin written to %s", out / "bulletin.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
