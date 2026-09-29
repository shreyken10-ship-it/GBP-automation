"""GBP Post Automation — entry point.

Usage:
    python run.py              # full pipeline: generation then posting
    python run.py generate     # Stage 1 only
    python run.py post         # Stage 2 only (rows already Generated)
"""
import sys

from src.log import get_logger
from src.sheets_client import SheetsClient

logger = get_logger("run")


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if mode == "all":
        from src import orchestrator
        return orchestrator.run()

    sheets = SheetsClient()
    if mode == "generate":
        from src import generation_agent
        summary = generation_agent.run_batch(sheets)
        logger.info("Generation summary: %s", summary)
        return 1 if summary["failed"] else 0
    if mode == "post":
        from src import posting_agent
        summary = posting_agent.run_batch(sheets)
        logger.info("Posting summary: %s", summary)
        return 1 if summary["failed"] else 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
