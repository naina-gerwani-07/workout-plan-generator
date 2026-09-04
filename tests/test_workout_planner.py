"""Tests for input validation, prompt construction and error handling.

These run with no API key and make no network calls: the Groq call is stubbed,
so every failure path the rubric cares about is verifiable offline.

Run with:  .venv/bin/python -m pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from groq import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import groq_client as gc  # noqa: E402
from models import (  # noqa: E402
    Equipment,
    ExperienceLevel,
    FitnessGoal,
    WorkoutRequest,
)
from prompts import build_swap_prompt, build_user_prompt  # noqa: E402

VALID_PLAN = "## Weekly Plan Summary\n" + ("x" * 250) + "\n### Day 1 — Full Body\n"


def make_request(**overrides) -> WorkoutRequest:
    defaults = dict(
        goal=FitnessGoal.BUILD_MUSCLE,
        experience=ExperienceLevel.BEGINNER,
        days_per_week=3,
        equipment=Equipment.HOME_DUMBBELLS,
    )
    return WorkoutRequest(**{**defaults, **overrides})


# --------------------------------------------------------------------------- #
# 1. Input validation
# --------------------------------------------------------------------------- #

def test_valid_request_has_no_errors():
    assert make_request().validate() == []


@pytest.mark.parametrize("days", [0, -3, 8, 100])
def test_out_of_range_days_are_rejected(days):
    errors = make_request(days_per_week=days).validate()
    assert errors and "days per week" in errors[0].lower()


def test_non_integer_days_are_rejected():
    assert make_request(days_per_week="three").validate()  # type: ignore[arg-type]


def test_session_length_bounds_are_enforced():
    assert make_request(session_minutes=5).validate()
    assert make_request(session_minutes=600).validate()


def test_overlong_injury_text_is_rejected():
    errors = make_request(injuries="knee " * 200).validate()
    assert errors and "characters" in errors[0]


def test_injury_flag_and_whitespace_cleanup():
    assert make_request(injuries="   ").has_injuries is False
    assert make_request(injuries="bad\n  knees").clean_injuries == "bad knees"


# --------------------------------------------------------------------------- #
# 2. Prompt construction — the constraints must actually reach the model
# --------------------------------------------------------------------------- #

def test_prompt_carries_every_structured_input():
    prompt = build_user_prompt(
        make_request(
            goal=FitnessGoal.LOSE_FAT,
            experience=ExperienceLevel.ADVANCED,
            days_per_week=5,
            equipment=Equipment.FULL_GYM,
            session_minutes=60,
            injuries="left shoulder impingement",
        )
    )
    for expected in [
        "Lose fat",
        "Advanced",
        "DAYS_PER_WEEK: 5",
        "SESSION_MINUTES: 60",
        "Full gym",
        "left shoulder impingement",
    ]:
        assert expected in prompt


def test_no_equipment_prompt_forbids_gym_kit():
    prompt = build_user_prompt(make_request(equipment=Equipment.NONE))
    assert "FORBIDDEN" in prompt
    assert "barbells" in prompt


def test_injury_input_adds_disclaimer_instruction():
    with_injury = build_user_prompt(make_request(injuries="bad knees"))
    without = build_user_prompt(make_request(injuries=""))
    assert "not medical advice" in with_injury
    assert "not medical advice" not in without


def test_variation_flag_asks_for_a_different_plan():
    assert "genuinely distinct" in build_user_prompt(make_request(), variation=True)
    assert "genuinely distinct" not in build_user_prompt(make_request())


def test_swap_prompt_repeats_equipment_and_injury_constraints():
    prompt = build_swap_prompt(
        make_request(equipment=Equipment.NONE, injuries="bad knees"),
        "barbell squat",
        "Day 2",
    )
    assert "barbell squat" in prompt and "Day 2" in prompt
    assert "FORBIDDEN" in prompt and "bad knees" in prompt


# --------------------------------------------------------------------------- #
# 3. Response validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", ["", "   ", "Sorry, I can't help.", None])
def test_empty_or_malformed_replies_are_unusable(text):
    usable, _ = gc._check_plan_text(text, 3)
    assert usable is False


def test_reply_without_day_headings_is_unusable():
    assert gc._check_plan_text("A lovely wall of text. " * 40, 3)[0] is False


def test_day_count_mismatch_is_a_warning_not_a_failure():
    usable, warnings = gc._check_plan_text(VALID_PLAN, 3)
    assert usable is True and warnings


def test_matching_day_count_produces_no_warnings():
    text = "## Summary\n" + "x" * 250 + "\n### Day 1 — A\n### Day 2 — B\n"
    usable, warnings = gc._check_plan_text(text, 2)
    assert usable is True and warnings == []


# --------------------------------------------------------------------------- #
# 4. API error handling — friendly message, never an exception
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("exc_type", "expected_phrase"),
    [
        (AuthenticationError, "rejected the API key"),
        (RateLimitError, "rate-limiting"),
        (APIConnectionError, "Couldn't reach Groq"),
        (BadRequestError, "retired"),
        (InternalServerError, "server-side error"),
        (ValueError, "Something unexpected"),
    ],
)
def test_api_errors_map_to_friendly_messages(exc_type, expected_phrase):
    # Built via __new__ so no HTTP response object is needed to instantiate them.
    exc = exc_type.__new__(exc_type)
    assert expected_phrase in gc._friendly_api_error(exc)


def test_generation_returns_friendly_error_when_api_fails(monkeypatch):
    monkeypatch.setattr(gc, "build_client", lambda *_a, **_k: object())

    def boom(**_kwargs):
        raise RateLimitError.__new__(RateLimitError)

    monkeypatch.setattr(gc, "_complete", boom)
    result = gc.generate_workout_plan(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, 3, Equipment.NONE
    )
    assert result.ok is False and "rate-limiting" in result.error


def test_missing_api_key_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(gc, "load_dotenv", lambda *_a, **_k: None)
    result = gc.generate_workout_plan(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, 3, Equipment.NONE
    )
    assert result.ok is False and "API key" in result.error


def test_invalid_input_never_reaches_the_api(monkeypatch):
    def fail(**_kwargs):
        raise AssertionError("the API must not be called with invalid input")

    monkeypatch.setattr(gc, "_complete", fail)
    result = gc.generate_workout_plan(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, 0, Equipment.NONE
    )
    assert result.ok is False and "days per week" in result.error.lower()


def test_empty_model_reply_falls_back_gracefully(monkeypatch):
    monkeypatch.setattr(gc, "build_client", lambda *_a, **_k: object())
    monkeypatch.setattr(gc, "_complete", lambda **_k: "")
    result = gc.generate_workout_plan(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, 3, Equipment.NONE
    )
    assert result.ok is False and result.error == gc.FALLBACK_MESSAGE


def test_successful_generation_returns_the_plan(monkeypatch):
    monkeypatch.setattr(gc, "build_client", lambda *_a, **_k: object())
    monkeypatch.setattr(gc, "_complete", lambda **_k: VALID_PLAN)
    result = gc.generate_workout_plan(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, 1, Equipment.NONE
    )
    assert result.ok is True and "Day 1" in result.text


# --------------------------------------------------------------------------- #
# 5. Exercise swap
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("exercise", ["", "   "])
def test_swap_requires_an_exercise_name(exercise):
    result = gc.swap_exercise(
        FitnessGoal.BUILD_MUSCLE, ExperienceLevel.BEGINNER, Equipment.NONE, exercise
    )
    assert result.ok is False and "exercise" in result.error.lower()


def test_swap_returns_alternatives(monkeypatch):
    monkeypatch.setattr(gc, "build_client", lambda *_a, **_k: object())
    monkeypatch.setattr(gc, "_complete", lambda **_k: "**Replacing:** squat")
    result = gc.swap_exercise(
        FitnessGoal.BUILD_MUSCLE,
        ExperienceLevel.BEGINNER,
        Equipment.NONE,
        "barbell squat",
    )
    assert result.ok is True and "Replacing" in result.text
