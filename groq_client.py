"""Groq API access and error handling.

Deliberately Streamlit-free: this module knows nothing about the UI, which is
what lets `tests/` exercise every failure path without a browser or an API key.

Three layers of defence, each producing a friendly message instead of a crash:
  1. Input validation      -> `WorkoutRequest.validate()`, before any network call.
  2. API failure           -> every `groq` exception mapped to plain English.
  3. Bad model output      -> empty / truncated / structureless replies caught here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Final

from dotenv import load_dotenv
from groq import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Groq,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)

from models import Equipment, ExperienceLevel, FitnessGoal, WorkoutRequest
from prompts import SYSTEM_PROMPT, build_swap_prompt, build_user_prompt

load_dotenv()

# GPT-OSS 120B is the strongest instruction-follower currently served by Groq, and
# this app lives or dies on multi-constraint compliance. The smaller models are
# offered as faster fallbacks for when the 120B is rate-limited.
#
# Model ids on Groq do get retired — `llama-3.3-70b-versatile` was the default here
# until it started returning 404. Verify the live list with `client.models.list()`
# rather than trusting the docs, and see the NotFoundError branch below.
DEFAULT_MODEL: Final[str] = "openai/gpt-oss-120b"
AVAILABLE_MODELS: Final[tuple[str, ...]] = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
)

REQUEST_TIMEOUT_SECONDS: Final[float] = 60.0
MAX_COMPLETION_TOKENS: Final[int] = 4096
MIN_PLAUSIBLE_PLAN_CHARS: Final[int] = 200

FALLBACK_MESSAGE: Final[str] = (
    "The model replied, but not with a usable plan. This usually clears up on a "
    "retry — press **Generate Plan** again, or try a different model in Advanced "
    "settings."
)


@dataclass
class GenerationResult:
    """Outcome of one generation attempt.

    A result object rather than raised exceptions: the UI only ever has to check
    `ok` and then show either `text` or `error`, so there is no path where an
    unhandled exception can reach the user as a traceback.
    """

    ok: bool
    text: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    model: str = ""


class MissingAPIKeyError(RuntimeError):
    """Raised internally when no Groq API key is configured."""


def get_api_key(explicit_key: str | None = None) -> str:
    """Resolve the Groq API key.

    Order: a key passed in (e.g. typed into the sidebar) wins, then the
    `GROQ_API_KEY` environment variable, which `.env` populates.
    """
    key = (explicit_key or os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        raise MissingAPIKeyError(
            "No Groq API key found. Create one at https://console.groq.com/keys, "
            "then either add `GROQ_API_KEY=your_key` to a `.env` file next to "
            "`app.py`, or paste it into the sidebar."
        )
    return key


def build_client(api_key: str | None = None) -> Groq:
    """Construct a Groq client with a bounded timeout and one automatic retry."""
    return Groq(
        api_key=get_api_key(api_key),
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


def _friendly_api_error(exc: Exception) -> str:
    """Translate an exception from the Groq SDK into something a user can act on."""
    if isinstance(exc, AuthenticationError):
        return (
            "Groq rejected the API key. Check that `GROQ_API_KEY` in your `.env` "
            "file is correct and still active at console.groq.com/keys."
        )
    if isinstance(exc, RateLimitError):
        return (
            "Groq is rate-limiting this key right now (free tier limit). Wait about "
            "a minute and try again, or switch to a smaller model in Advanced settings."
        )
    if isinstance(exc, APITimeoutError):
        return (
            "Groq took too long to respond. Try again — or reduce the days per week, "
            "which shortens the plan it has to write."
        )
    if isinstance(exc, APIConnectionError):
        return (
            "Couldn't reach Groq. Check your internet connection and try again."
        )
    if isinstance(exc, NotFoundError):
        # Groq retires model ids periodically; a 404 almost always means the model
        # id is gone rather than that anything is wrong with the request.
        return (
            "That model isn't available on your Groq account — model ids get "
            "retired periodically. Pick a different model in Advanced settings."
        )
    if isinstance(exc, BadRequestError):
        return (
            "Groq rejected the request. If you changed the model, try another one "
            "in Advanced settings."
        )
    if isinstance(exc, InternalServerError):
        return "Groq had a server-side error. This is usually temporary — try again."
    if isinstance(exc, APIError):
        return f"The Groq API returned an error: {exc}"
    return f"Something unexpected went wrong while generating the plan: {exc}"


def _check_plan_text(text: str, expected_days: int) -> tuple[bool, list[str]]:
    """Validate the model's reply.

    Returns `(is_usable, warnings)`. A reply that is empty or has no day structure
    is unusable and triggers the fallback message. A reply with the wrong *number*
    of days is still shown — it is useful — but the mismatch is surfaced as a
    warning, because silently accepting it would hide a prompt-design failure.
    """
    warnings: list[str] = []
    body = (text or "").strip()

    if not body:
        return False, warnings
    if len(body) < MIN_PLAUSIBLE_PLAN_CHARS:
        return False, warnings

    day_headings = re.findall(r"^#{1,4}\s*Day\s*\d+", body, flags=re.MULTILINE)
    if not day_headings:
        return False, warnings

    found = len(day_headings)
    if found != expected_days:
        warnings.append(
            f"You asked for {expected_days} training day(s) but the model wrote "
            f"{found}. Press Regenerate if you want it to try again."
        )
    return True, warnings


def _complete(
    client: Groq,
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
) -> str:
    """Single chat completion call. Returns the raw assistant text."""
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    if not response.choices:
        return ""
    return response.choices[0].message.content or ""


def generate_workout_plan(
    goal: FitnessGoal,
    experience: ExperienceLevel,
    days_per_week: int,
    equipment: Equipment,
    injuries: str = "",
    session_minutes: int = 45,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.6,
    variation: bool = False,
    api_key: str | None = None,
) -> GenerationResult:
    """Generate a personalised weekly workout plan.

    Args:
        goal: What the user is training for.
        experience: The user's training history level.
        days_per_week: Training days available, 1-7.
        equipment: What the user can train with.
        injuries: Free-text injuries or limitations; empty string if none.
        session_minutes: Minutes available per session, 15-120.
        model: Groq model id to use.
        temperature: Sampling temperature; higher gives more variation.
        variation: True when the user asked for a different plan from the same inputs.
        api_key: Overrides `GROQ_API_KEY` when provided.

    Returns:
        A `GenerationResult`. `ok` is False with a user-facing `error` for invalid
        input, API failure, or an unusable model reply — this function does not raise.
    """
    request = WorkoutRequest(
        goal=goal,
        experience=experience,
        days_per_week=days_per_week,
        equipment=equipment,
        injuries=injuries,
        session_minutes=session_minutes,
    )

    problems = request.validate()
    if problems:
        return GenerationResult(
            ok=False,
            error="Please fix these before generating:\n\n"
            + "\n".join(f"- {p}" for p in problems),
            model=model,
        )

    try:
        client = build_client(api_key)
        text = _complete(
            client=client,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(request, variation=variation),
            model=model,
            temperature=temperature,
        )
    except MissingAPIKeyError as exc:
        return GenerationResult(ok=False, error=str(exc), model=model)
    except Exception as exc:  # noqa: BLE001 - every failure must stay friendly
        return GenerationResult(ok=False, error=_friendly_api_error(exc), model=model)

    usable, warnings = _check_plan_text(text, request.days_per_week)
    if not usable:
        return GenerationResult(ok=False, error=FALLBACK_MESSAGE, model=model)

    return GenerationResult(
        ok=True, text=text.strip(), warnings=warnings, model=model
    )


def swap_exercise(
    goal: FitnessGoal,
    experience: ExperienceLevel,
    equipment: Equipment,
    exercise: str,
    injuries: str = "",
    day_label: str = "",
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    api_key: str | None = None,
) -> GenerationResult:
    """Suggest two constraint-respecting replacements for a single exercise.

    Args:
        goal: The user's training goal, so the substitute serves the same purpose.
        experience: Keeps the substitute at an appropriate complexity.
        equipment: The substitute must be performable with this equipment.
        exercise: The exercise the user wants to replace.
        injuries: Free-text injuries or limitations; empty string if none.
        day_label: Optional day the exercise came from, for context in the prompt.
        model: Groq model id to use.
        temperature: Sampling temperature.
        api_key: Overrides `GROQ_API_KEY` when provided.

    Returns:
        A `GenerationResult` holding the markdown alternatives, or a friendly error.
    """
    if not exercise or not exercise.strip():
        return GenerationResult(
            ok=False,
            error="Type the name of the exercise you'd like to swap out first.",
            model=model,
        )

    request = WorkoutRequest(
        goal=goal,
        experience=experience,
        days_per_week=1,  # not used by the swap prompt; keeps the request valid
        equipment=equipment,
        injuries=injuries,
    )

    try:
        client = build_client(api_key)
        text = _complete(
            client=client,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_swap_prompt(request, exercise, day_label),
            model=model,
            temperature=temperature,
        )
    except MissingAPIKeyError as exc:
        return GenerationResult(ok=False, error=str(exc), model=model)
    except Exception as exc:  # noqa: BLE001 - every failure must stay friendly
        return GenerationResult(ok=False, error=_friendly_api_error(exc), model=model)

    if not text.strip():
        return GenerationResult(
            ok=False,
            error="The model didn't suggest anything usable. Try again.",
            model=model,
        )
    return GenerationResult(ok=True, text=text.strip(), model=model)
