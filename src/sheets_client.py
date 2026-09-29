"""Google Sheets service-account client — job queue read/write.

Columns are resolved by header name against the live sheet, not hardcoded
indices, so re-ordering columns in the sheet doesn't break the pipeline.
All API calls retry with exponential backoff (PRD §8: Sheets rate limits /
transient failures must not kill a batch mid-run).
"""
import random
import string
import time

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from . import config
from .log import get_logger

logger = get_logger("sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_RANGE = "Sheet1!A1:Z1000"

_RETRIES = 4


def _with_backoff(fn):
    """Execute fn() retrying on any googleapiclient/network error with
    exponential backoff + jitter. Raises the last error after _RETRIES."""
    for attempt in range(_RETRIES):
        try:
            return fn()
        except Exception as exc:
            if attempt == _RETRIES - 1:
                raise
            delay = (2 ** attempt) + random.uniform(0, 1)
            logger.warning("Sheets call failed (%s), retrying in %.1fs", exc, delay)
            time.sleep(delay)


def _col_letter(index: int) -> str:
    """0-based column index -> spreadsheet column letter (A, B, ..., Z, AA, ...)."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = string.ascii_uppercase[remainder] + letters
    return letters


class SheetsClient:
    def __init__(self):
        creds = Credentials.from_service_account_info(
            {
                "client_email": config.GOOGLE_SERVICE_ACCOUNT_EMAIL,
                "private_key": config.GOOGLE_PRIVATE_KEY,
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=SCOPES,
        )
        self._service = build("sheets", "v4", credentials=creds)
        self._sheet_id = config.GOOGLE_SHEETS_ID
        self._header_to_col: dict[str, int] = {}

    def _values(self):
        return self._service.spreadsheets().values()

    def _load_header(self, header_row: list[str]):
        self._header_to_col = {name.strip(): idx for idx, name in enumerate(header_row)}

    def _row_to_dict(self, header_row: list[str], row: list[str]) -> dict:
        return {header_row[i]: (row[i] if i < len(row) else "") for i in range(len(header_row))}

    def read_all_rows(self) -> list:
        from .models import SheetRow

        result = _with_backoff(
            lambda: self._values().get(
                spreadsheetId=self._sheet_id, range=SHEET_RANGE
            ).execute()
        )
        rows = result.get("values", [])
        if not rows:
            return []

        header_row = rows[0]
        self._load_header(header_row)

        sheet_rows = []
        for i, raw_row in enumerate(rows[1:], start=2):
            if not raw_row or not any(cell.strip() for cell in raw_row if isinstance(cell, str)):
                continue
            sheet_rows.append(SheetRow(row_number=i, values=self._row_to_dict(header_row, raw_row)))
        return sheet_rows

    def get_pending_rows(self) -> list:
        return [r for r in self.read_all_rows() if r.is_pending_generation]

    def get_generated_rows(self) -> list:
        return [r for r in self.read_all_rows() if r.is_ready_to_post]

    def update_row(self, row_number: int, **fields):
        """Write one or more named fields back to a row in a single batched
        API call (one call per row instead of one per field — ~10x less
        quota pressure). Columns not present in the live header row are
        skipped with a loud warning (not created) — add the column to the
        sheet manually first.
        """
        if not self._header_to_col:
            self.read_all_rows()  # populate header map

        data = []
        for field_name, value in fields.items():
            col_idx = self._header_to_col.get(field_name)
            if col_idx is None:
                logger.warning(
                    "Column %r not found in sheet header — write SKIPPED (value: %.80r)",
                    field_name, value,
                )
                continue
            data.append({
                "range": f"Sheet1!{_col_letter(col_idx)}{row_number}",
                "values": [[value]],
            })
        if not data:
            return

        _with_backoff(
            lambda: self._values().batchUpdate(
                spreadsheetId=self._sheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
        )

    def mark_generation_failed(self, row_number: int, reason: str):
        self.update_row(row_number, **{"Batch Status": "Failed", "Error / Notes": reason})

    def mark_post_failed(self, row_number: int, reason: str):
        self.update_row(row_number, **{"Post Status": "Failed", "Error / Notes": reason})
