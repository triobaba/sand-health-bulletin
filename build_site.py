"""Build the public site: an interactive dashboard plus a downloadable PDF.

Run:  python build_site.py

Writes everything to site/, which is the folder Netlify publishes:

  site/index.html                 the dashboard
  site/assets/                    dashboard styles and script
  site/data/bulletin-data.js      every figure the dashboard shows
  site/downloads/*.pdf            the printable bulletin
  site/downloads/*.csv            data-quality report and reporting status

Every number comes from one call to bulletin.run(). This file only formats
those results, so the dashboard, the PDF and the CSVs cannot disagree.

The site is static on purpose: no server, no database and no calls to other
services, so it is cheap to host, opens on a weak connection and cannot leak
anything beyond what is in site/. The data is aggregate facility-month data
only; no patient-level record is read or published.

The PDF is printed by a headless browser (Playwright) from an HTML template, so
the printed layout is reviewed like any other code. If Playwright is missing the
site is still built, the script says so, and it exits with code 1 so an
automated build cannot publish a site whose PDF link is broken.

Exit codes: 0 built, 1 built without the PDF, 2 stopped by a data problem.
"""

import json
import logging
import math
import shutil
import sys
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

import bulletin
import config as cfg

log = logging.getLogger("build_site")

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
DASHBOARD_SRC = HERE / "dashboard"
REPORT_TEMPLATE = HERE / "templates" / "report.html.j2"


# ---------------------------------------------------------------------------
# Data for the dashboard
# ---------------------------------------------------------------------------
def _records(df, cols=None):
    """DataFrame to a list of plain dicts that JSON can hold.

    NaN becomes None (JSON has no NaN, and a browser would fail to parse it)
    and numpy scalars become Python numbers. Missing values therefore reach the
    dashboard as null and are drawn as n/a, never as zero.
    """
    if cols:
        df = df[cols]
    out = []
    for row in df.to_dict(orient="records"):
        clean = {}
        for k, v in row.items():
            if hasattr(v, "item"):
                v = v.item()
            if isinstance(v, float) and math.isnan(v):
                v = None
            clean[k] = v
        out.append(clean)
    return out


def dashboard_data(r):
    """Everything the dashboard shows, as one JSON-ready dict.

    The dashboard does no arithmetic of its own beyond simple display maths
    (for example deliveries per month for the Q4 note), so the published
    figures are exactly the pipeline's figures."""
    q_cols = ["quarter", "deliveries", "live_births", "stillbirths",
              "stillbirth_rate_per_1000", "early_nmr_per_1000", "late_nmr_per_1000",
              "nmr_per_1000", "preterm_pct", "lbw_pct", "apgar_lt7_pct",
              "cause_birth_asphyxia_pct", "cause_prematurity_pct", "cause_sepsis_pct",
              "cause_congenital_pct", "cause_other_pct", "months_present"]
    p_cols = ["quarter", "province", "deliveries", "live_births",
              "stillbirth_rate_per_1000", "nmr_per_1000", "preterm_pct", "lbw_pct",
              "apgar_lt7_pct", "months_present"]
    top = {q: _records(df.reset_index().rename(columns={"index": "rank"}),
                       ["rank", "facility_id", "facility_name", "district", "province",
                        "deliveries", "months_present", "conflicting_in_year"])
           for q, df in r["top_by_quarter"].items()}
    perf = r["perf"]
    status = r["status"]
    return {
        "meta": {
            "title": "Quarterly Health Bulletin: Neonatal Care, 2024",
            "generated_at": r["generated_at"],
            "latest_quarter": r["latest_q"],
            "quarters": list(r["coverage"].quarter),
            "facilities": int(len(perf)),
            "districts": int(r["facilities"].district.nunique()),
            "provinces": sorted(r["facilities"].province.unique().tolist()),
            "duplicate_rule": cfg.DUPLICATE_RULE,
            "expected_months": [cfg.EXPECTED_MONTHS[0], cfg.EXPECTED_MONTHS[-1]],
            "pdf": f"downloads/{pdf_name(r)}",
        },
        "coverage": _records(r["coverage"], ["quarter", "months_in_data", "months_present",
                                              "months_expected", "facilities_reporting",
                                              "coverage_pct", "low_coverage"]),
        "national": _records(r["national"], q_cols),
        "by_province": _records(r["by_province"], p_cols),
        "top": top,
        "trends": _records(r["trends"], ["quarter", "months_present", "deliveries",
                                          "deliveries_change_pct", "stillbirth_rate_per_1000",
                                          "stillbirth_rate_per_1000_change_pct", "nmr_per_1000",
                                          "nmr_per_1000_change_pct", "comparable"]),
        "performance": {
            "mean_observed": round(float(perf.observed_completeness.mean()), 1),
            "mean_self": round(float(perf.self_reported_completeness.mean()), 1),
            "n_over_10": int((perf.gap.abs() > 10).sum()),
            "rows": _records(perf, ["facility_id", "facility_name", "district", "province",
                                    "months_present", "observed_completeness",
                                    "self_reported_completeness", "gap", "conflicting_months"]),
        },
        "status": {
            "counts": {k: int(v) for k, v in status.status.value_counts().items()},
            "rows": _records(status),
        },
        "issues": [i for i in r["issues"] if i["count"] > 0],
        "checks_passed": [i["issue"] for i in r["issues"] if i["count"] == 0],
    }


def pdf_name(r):
    return f"quarterly-health-bulletin-{r['latest_q']}.pdf"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def render_report_html(r, out_path):
    """Render the printable A4 bulletin to HTML, ready for the PDF printer."""
    env = Environment(loader=FileSystemLoader(str(REPORT_TEMPLATE.parent)), autoescape=True)
    env.filters["num"] = lambda v, d=0: "" if v is None or (isinstance(v, float) and math.isnan(v)) \
        else (f"{v:,.{d}f}")
    ctx = bulletin.bulletin_context(r)
    ctx["coverage_rows"] = _records(r["coverage"])
    ctx["national_rows"] = _records(r["national"].merge(r["coverage"][["quarter", "months_expected"]], on="quarter"))
    ctx["province_rows"] = _records(ctx["by_province"])
    ctx["top_rows"] = _records(ctx["top"].reset_index().rename(columns={"index": "rank"}))
    ctx["trend_rows"] = _records(r["trends"])
    ctx["perf_rows"] = _records(ctx["perf_top"])
    ctx["max_deliveries"] = float(r["national"].deliveries.max())
    html = env.get_template(REPORT_TEMPLATE.name).render(**ctx)
    Path(out_path).write_text(html, encoding="utf-8")


def print_pdf(html_path, pdf_path):
    """Print the report HTML to an A4 PDF. Returns False if it could not."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("  PDF skipped: Playwright is not installed "
                    "(pip install playwright, then python -m playwright install chromium)")
        return False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(Path(html_path).resolve().as_uri())   # correct on Windows and Linux
        page.wait_for_load_state("networkidle")
        page.pdf(path=pdf_path, format="A4", print_background=True,
                 margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
                 display_header_footer=True,
                 header_template="<span></span>",
                 footer_template=(
                     "<div style='font-size:7px;width:100%;padding:0 14mm;color:#6b6760;"
                     "font-family:Helvetica,Arial,sans-serif;display:flex;justify-content:space-between'>"
                     "<span>Quarterly Health Bulletin · prototype for the Rwanda Ministry of Health · figures are provisional</span>"
                     "<span>Page <span class='pageNumber'></span> of <span class='totalPages'></span></span></div>"))
        browser.close()
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build(r, out: Path) -> bool:
    """Write the whole site into the folder out. Returns whether the PDF was made."""
    for sub in ("assets", "data", "downloads"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    log.info("Writing dashboard data")
    payload = json.dumps(dashboard_data(r), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (out / "data" / "bulletin-data.js").write_text(f"window.BULLETIN = {payload};\n", encoding="utf-8")

    log.info("Copying dashboard files")
    shutil.copy(DASHBOARD_SRC / "index.html", out / "index.html")
    for name in ("style.css", "app.js"):
        shutil.copy(DASHBOARD_SRC / name, out / "assets" / name)
    if (DASHBOARD_SRC / "favicon.svg").exists():
        shutil.copy(DASHBOARD_SRC / "favicon.svg", out / "favicon.svg")

    log.info("Writing CSV downloads")
    pd.DataFrame(r["issues"]).to_csv(out / "downloads" / "data_quality_report.csv", index=False)
    r["status"].to_csv(out / "downloads" / "facility_reporting_status.csv", index=False)

    log.info("Printing the PDF bulletin")
    # The report HTML is rendered next to its template so the relative font
    # paths resolve, then removed. A copy is kept in output/ for review.
    report_html = REPORT_TEMPLATE.parent / "_report_render.html"
    try:
        render_report_html(r, report_html)
        pdf_ok = print_pdf(report_html, out / "downloads" / pdf_name(r))
        # Keep a reviewable copy in output/, with its fonts beside it so it
        # renders correctly when opened directly in a browser.
        Path(cfg.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        shutil.copy(report_html, Path(cfg.OUTPUT_DIR) / "report.html")
        shutil.copytree(REPORT_TEMPLATE.parent / "fonts", Path(cfg.OUTPUT_DIR) / "fonts", dirs_exist_ok=True)
    finally:
        report_html.unlink(missing_ok=True)
    return pdf_ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log.info("Running the pipeline")
    try:
        r = bulletin.run(verbose=False)
    except bulletin.DataError as e:
        log.error("Stopped: %s", e)
        return 2

    # Build into a fresh folder and swap it in only when everything succeeded,
    # so a failed build never leaves a half-written site/ behind.
    staging = HERE / "site.build"
    shutil.rmtree(staging, ignore_errors=True)
    pdf_ok = build(r, staging)
    shutil.rmtree(SITE, ignore_errors=True)
    staging.rename(SITE)

    log.info("\nDone. The site is in site/")
    log.info("  check it with: python -m http.server 8000 --directory site")
    log.info("  then deploy the site folder to Netlify.")
    if not pdf_ok:
        log.warning("  The PDF was not created, so the download button will not work yet.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
