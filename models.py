"""Structured input contract for the workout plan generator.

Everything the app collects from the user is defined here once: the enums drive
the Streamlit dropdowns, and the same enums are read by `prompts.py` when the
prompt is assembled. That way the UI and the prompt can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

# --- Validation bounds (shared by the UI widgets and `WorkoutRequest.validate`) ---
MIN_DAYS_PER_WEEK: Final[int] = 1
MAX_DAYS_PER_WEEK: Final[int] = 7
MIN_SESSION_MINUTES: Final[int] = 15
MAX_SESSION_MINUTES: Final[int] = 120
MAX_INJURY_CHARS: Final[int] = 400


class FitnessGoal(str, Enum):
    """What the user is training for."""

    BUILD_MUSCLE = "Build muscle"
    LOSE_FAT = "Lose fat"
    GENERAL_FITNESS = "General fitness"
    IMPROVE_ENDURANCE = "Improve endurance"


class ExperienceLevel(str, Enum):
    """How much training history the user has."""

    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class Equipment(str, Enum):
    """What the user can actually train with."""

    NONE = "No equipment"
    HOME_DUMBBELLS = "Home dumbbells"
    FULL_GYM = "Full gym"


@dataclass(frozen=True)
class WorkoutRequest:
    """One complete, validated request for a weekly workout plan.

    Frozen because a request is a value: once the user hits "Generate", the
    inputs that produced a plan should not be mutable behind that plan's back.
    """

    goal: FitnessGoal
    experience: ExperienceLevel
    days_per_week: int
    equipment: Equipment
    injuries: str = ""
    session_minutes: int = 45

    @property
    def has_injuries(self) -> bool:
        """True when the user disclosed an injury or limitation.

        Drives two things: the extra "work around this" prompt block, and the
        safety disclaimer the assignment asks for.
        """
        return bool(self.injuries.strip())

    @property
    def clean_injuries(self) -> str:
        """Injury text with surrounding whitespace and newlines collapsed."""
        return " ".join(self.injuries.split())

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty list means valid.

        Returning messages instead of raising lets the UI show *every* problem
        at once, and keeps this logic unit-testable without Streamlit or Groq.
        """
        errors: list[str] = []

        if not isinstance(self.days_per_week, int) or isinstance(self.days_per_week, bool):
            errors.append("Days per week must be a whole number.")
        elif not MIN_DAYS_PER_WEEK <= self.days_per_week <= MAX_DAYS_PER_WEEK:
            errors.append(
                f"Pick between {MIN_DAYS_PER_WEEK} and {MAX_DAYS_PER_WEEK} training "
                f"days per week — {self.days_per_week} isn't a week I can plan for."
            )

        if not isinstance(self.session_minutes, int) or isinstance(self.session_minutes, bool):
            errors.append("Session length must be a whole number of minutes.")
        elif not MIN_SESSION_MINUTES <= self.session_minutes <= MAX_SESSION_MINUTES:
            errors.append(
                f"Session length should be {MIN_SESSION_MINUTES}-{MAX_SESSION_MINUTES} minutes."
            )

        if not isinstance(self.goal, FitnessGoal):
            errors.append("Please choose a fitness goal from the list.")
        if not isinstance(self.experience, ExperienceLevel):
            errors.append("Please choose your experience level from the list.")
        if not isinstance(self.equipment, Equipment):
            errors.append("Please choose what equipment you have access to.")

        if len(self.clean_injuries) > MAX_INJURY_CHARS:
            errors.append(
                f"Please keep injuries/limitations under {MAX_INJURY_CHARS} characters "
                f"(currently {len(self.clean_injuries)})."
            )

        return errors
