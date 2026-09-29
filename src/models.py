"""Shared data model for the GBP automation pipeline."""
from dataclasses import dataclass, field
from typing import Optional

# Canonical sheet header row, in column order. sheets_client resolves columns
# by name against this (and the live header row) rather than hardcoded indices.
SHEET_COLUMNS = [
    "S.No",
    "Report URL",
    "Market Name",
    "Base Year / Forecast Year",
    "Base / Forecast Market Size",
    "CAGR (%)",
    "GBP Description",
    "Image Status",
    "Image Link (Cloudinary)",
    "CTA Link (UTM-tagged)",
    "Batch Status",
    "Post Status",
    "Post Timestamp",
    "Error / Notes",
]

@dataclass
class SheetRow:
    row_number: int  # 1-indexed, includes header row offset
    values: dict = field(default_factory=dict)

    def get(self, column: str, default: str = "") -> str:
        return (self.values.get(column) or "").strip() or default

    @property
    def report_url(self) -> str:
        return self.get("Report URL")

    @property
    def batch_status(self) -> str:
        return self.get("Batch Status", "Pending")

    @property
    def post_status(self) -> str:
        return self.get("Post Status", "Pending")

    @property
    def is_pending_generation(self) -> bool:
        return self.report_url != "" and self.batch_status in ("", "Pending")

    @property
    def is_ready_to_post(self) -> bool:
        return self.batch_status == "Generated" and self.post_status in ("", "Pending")


@dataclass
class GenerationResult:
    success: bool
    market_name: Optional[str] = None
    base_year: Optional[str] = None
    forecast_year: Optional[str] = None
    base_market_size: Optional[str] = None
    forecast_market_size: Optional[str] = None
    cagr: Optional[str] = None
    description: Optional[str] = None
    image_bytes: Optional[bytes] = None
    image_url: Optional[str] = None
    cta_link: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PostResult:
    success: bool
    posted_at: Optional[str] = None
    error: Optional[str] = None
