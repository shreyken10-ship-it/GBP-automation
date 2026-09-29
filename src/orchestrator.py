"""Sequences the two stages: generation for all pending rows, then posting
for the completed batch (FR-6 — never interleaved).
"""
from . import generation_agent, posting_agent
from .log import get_logger
from .sheets_client import SheetsClient

logger = get_logger("orchestrator")


def run() -> int:
    sheets = SheetsClient()

    logger.info("=" * 60)
    logger.info("STAGE 1 — Generation")
    logger.info("=" * 60)
    gen = generation_agent.run_batch(sheets)
    logger.info(
        "Generation done: %d total, %d generated, %d failed",
        gen["total"], gen["generated"], gen["failed"],
    )

    logger.info("=" * 60)
    logger.info("STAGE 2 — Posting")
    logger.info("=" * 60)
    post = posting_agent.run_batch(sheets)
    logger.info(
        "Posting done: %d total, %d posted, %d failed",
        post["total"], post["posted"], post["failed"],
    )

    logger.info("=" * 60)
    logger.info(
        "RUN SUMMARY — generated %d/%d, posted %d/%d",
        gen["generated"], gen["total"], post["posted"], post["total"],
    )
    logger.info("=" * 60)

    return 1 if (gen["failed"] or post["failed"]) else 0
