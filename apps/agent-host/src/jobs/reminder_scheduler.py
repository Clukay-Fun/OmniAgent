"""Backward-compatible import for ReminderScheduler."""

from __future__ import annotations

from src.jobs.schedulers.reminder_scheduler import ReminderScheduler

__all__ = ["ReminderScheduler"]
