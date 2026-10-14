"""Run-time settings for the Quarterly Health Bulletin prototype.

Everything an M&E officer may need to change for a routine run lives here, so
the pipeline in bulletin.py never has to be edited to produce a new quarter.

Each setting below is either a business rule the Ministry owns (marked OWNER:
M&E unit) or a technical choice of this prototype. The business rules are
placeholders until the M&E unit confirms them; they are kept here, in one
place, so replacing them is a one-line change that shows up clearly in review.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths. Resolved from this file, so the pipeline runs from any working folder.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
TEMPLATE_PATH = ROOT / "templates" / "bulletin.html.j2"

# ---------------------------------------------------------------------------
# Reporting calendar. OWNER: M&E unit.
# Every facility is expected to report every month of 2024. In production this
# comes from DHIS2 periods, not a fixed list (see Part 3, item 3 of the PDF).
# ---------------------------------------------------------------------------
EXPECTED_MONTHS = [f"2024-{m:02d}" for m in range(1, 13)]

# A quarter counts as complete only when every expected month is in the data.
# Any quarter below this share is flagged, and its counts are not compared with
# other quarters. Rates (per 1,000) are still shown because they do not depend
# on how many months are present.
FULL_QUARTER_SHARE = 1.0

# A facility is Silent when its last report is this many months or more before
# the latest month in the export.
SILENT_MONTHS_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Repeated reports. OWNER: M&E unit.
# When one facility has two different reports for the same month, this rule
# picks the one the bulletin uses. "keep_first" is a placeholder so the pipeline
# runs end to end; it is NOT a judgement about which report is correct.
# Options implemented: "keep_first", "keep_last". The production rule (latest
# DHIS2 update wins, or the district-approved report wins) needs data the CSV
# export does not carry, so it is added when the DHIS2 connection exists.
# Every affected facility-month is flagged in the outputs whatever rule runs.
# ---------------------------------------------------------------------------
DUPLICATE_RULE = "keep_first"

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
TOP_N = 10

# ---------------------------------------------------------------------------
# Known quirks of this export
# ---------------------------------------------------------------------------
# Governance columns that arrive as text such as "63%".
PERCENT_COLUMNS = [
    "death_audits_conducted_pct",
    "staff_trained_on_protocol_pct",
    "hmis_reporting_completeness",
    "bag_mask_ventilation_competency",
    "thermal_care_protocol_compliance",
    "infection_prevention_score",
]

# Date columns that may hold the word Never instead of a date.
DATE_COLUMNS = {
    "governance": ["protocol_last_updated"],
    "healthcare_workers": ["last_neonatal_training_date"],
}
