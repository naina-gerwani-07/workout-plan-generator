"""UI-level tests driving the real Streamlit app through `AppTest`.

The Groq call is stubbed, so these run offline and consume no API quota. They
cover the wiring that unit tests can't see: that widget values actually reach
`generate_workout_plan`, that session_state survives a rerun, and that a failed
call surfaces as an error banner rather than a traceback.

Run with:  .venv/bin/python -m pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import groq_client  # noqa: E402
from groq_client import GenerationResult  # noqa: E402

APP = str(ROOT / "app.py")

STUB_PLAN = (
    "## Weekly Plan Summary\nBodyweight full body.\n\n**Split:** Full-body\n\n"
    "### Day 1 — Full Body\n| Exercise | Sets | Reps | Rest | Notes |\n"
    "| --- | --- | --- | --- | --- |\n| Wall Push-up | 3 | 10 | 60 s | knee-safe |\n\n"
    "### Day 2 — Full Body\n| Exercise | Sets | Reps | Rest | Notes |\n"
    "| --- | --- | --- | --- | --- |\n| Glute Bridge | 3 | 12 | 60 s | knee-safe |\n"
    + "x" * 200
)


@pytest.fixture
def stub_groq(monkeypatch):
    """Replace the Groq call and record the kwargs the UI passed to it.

    `app.py` does `from groq_client import generate_workout_plan`, and AppTest
    executes app.py on `run()`, so patching the module attribute beforehand is
    what the app picks up.
    """
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return GenerationResult(ok=True, text=STUB_PLAN, model="stub")

    monkeypatch.setattr(groq_client, "generate_workout_plan", fake)
    return calls


def test_app_loads_without_a_plan_or_an_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.info, "should invite the user to fill in the sidebar"
    assert not at.download_button, "nothing to download before a plan exists"


def test_regenerate_is_disabled_until_a_plan_exists(stub_groq):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert at.button[1].label.endswith("Regenerate")
    assert at.button[1].disabled is True


def test_widget_values_reach_the_generator(stub_groq):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.selectbox[0].select("Lose fat")
    at.sidebar.selectbox[1].select("Advanced")
    at.sidebar.slider[0].set_value(4)
    at.sidebar.slider[1].set_value(60)
    at.sidebar.selectbox[2].select("Full gym")
    at.sidebar.text_area[0].set_value("bad knees")
    at.run()
    at.button[0].click().run()

    assert not at.exception
    sent = stub_groq[-1]
    assert sent["goal"].value == "Lose fat"
    assert sent["experience"].value == "Advanced"
    assert sent["days_per_week"] == 4
    assert sent["session_minutes"] == 60
    assert sent["equipment"].value == "Full gym"
    assert sent["injuries"] == "bad knees"
    assert sent["variation"] is False


def test_generating_renders_the_plan_and_enables_the_extras(stub_groq):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()

    assert not at.exception
    assert any("Day 1" in m.value for m in at.markdown)
    # Regression: `has_plan` is evaluated before the click is handled, so without
    # the st.rerun() in app.py this button stays disabled after generating.
    assert at.button[1].disabled is False
    assert len(at.download_button) == 1
    assert at.session_state["plan"].ok is True


def test_regenerate_asks_for_a_variation_at_a_higher_temperature(stub_groq):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()
    first_temp = stub_groq[-1]["temperature"]

    at.button[1].click().run()
    assert not at.exception
    assert stub_groq[-1]["variation"] is True
    assert stub_groq[-1]["temperature"] > first_temp


def test_api_failure_shows_an_error_banner_and_no_plan(monkeypatch):
    monkeypatch.setattr(
        groq_client,
        "generate_workout_plan",
        lambda **_k: GenerationResult(ok=False, error="Groq is rate-limiting this key.", model="stub"),
    )
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()

    assert not at.exception
    assert [e.value for e in at.error] == ["Groq is rate-limiting this key."]
    assert not any("Day 1" in m.value for m in at.markdown)
    assert not at.download_button
