"""Streamlit UI for the Workout Plan Generator.

This file is deliberately thin: it collects structured input, calls into
`groq_client`, and renders the result. No prompt text and no API handling live
here, so the logic can be tested without Streamlit.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from groq_client import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    GenerationResult,
    generate_workout_plan,
    swap_exercise,
)
from models import (
    MAX_DAYS_PER_WEEK,
    MAX_INJURY_CHARS,
    MAX_SESSION_MINUTES,
    MIN_DAYS_PER_WEEK,
    MIN_SESSION_MINUTES,
    Equipment,
    ExperienceLevel,
    FitnessGoal,
)

st.set_page_config(page_title="Workout Plan Generator", page_icon="🏋️", layout="wide")

# --- Stretch goal: persist the last plan across Streamlit reruns -------------
# Streamlit re-executes this whole script on every interaction, so anything that
# must survive a click has to live in session_state.
for key, default in {
    "plan": None,          # GenerationResult of the current plan
    "plan_inputs": None,   # the inputs that produced it, for regenerate/swap/download
    "swap_result": None,   # GenerationResult of the last exercise swap
    "swap_query": "",      # the exercise name that was swapped
}.items():
    st.session_state.setdefault(key, default)


def collect_inputs() -> dict:
    """Render the input widgets and return the structured values as a dict."""
    st.sidebar.header("Tell me about you")

    goal = st.sidebar.selectbox(
        "Fitness goal",
        options=list(FitnessGoal),
        format_func=lambda g: g.value,
        help="What this training block is actually for.",
    )
    experience = st.sidebar.selectbox(
        "Experience level",
        options=list(ExperienceLevel),
        format_func=lambda e: e.value,
        index=0,
        help="Beginner: under 6 months. Intermediate: 6-24 months. Advanced: 2+ years.",
    )
    days_per_week = st.sidebar.slider(
        "Training days per week",
        min_value=MIN_DAYS_PER_WEEK,
        max_value=MAX_DAYS_PER_WEEK,
        value=3,
    )
    session_minutes = st.sidebar.slider(
        "Minutes per session",
        min_value=MIN_SESSION_MINUTES,
        max_value=MAX_SESSION_MINUTES,
        value=45,
        step=5,
    )
    equipment = st.sidebar.selectbox(
        "Equipment access",
        options=list(Equipment),
        format_func=lambda e: e.value,
    )
    injuries = st.sidebar.text_area(
        "Injuries or limitations (optional)",
        placeholder="e.g. bad knees, no overhead pressing, recovering shoulder",
        max_chars=MAX_INJURY_CHARS,
        height=90,
        help="Anything here is treated as a hard constraint, not a suggestion.",
    )

    with st.sidebar.expander("⚙️ Advanced settings"):
        model = st.selectbox("Groq model", options=AVAILABLE_MODELS, index=0)
        temperature = st.slider(
            "Creativity (temperature)",
            min_value=0.0,
            max_value=1.0,
            value=0.6,
            step=0.1,
            help="Lower sticks closer to standard programming; higher varies more.",
        )
        api_key_override = st.text_input(
            "Groq API key (optional)",
            type="password",
            help="Leave blank to use GROQ_API_KEY from your .env file.",
        )

    return {
        "goal": goal,
        "experience": experience,
        "days_per_week": days_per_week,
        "session_minutes": session_minutes,
        "equipment": equipment,
        "injuries": injuries,
        "model": model or DEFAULT_MODEL,
        "temperature": temperature,
        "api_key": api_key_override or None,
    }


def run_generation(inputs: dict, *, variation: bool = False) -> None:
    """Call the generator and store the outcome in session_state."""
    label = "Building a different plan…" if variation else "Building your plan…"
    with st.spinner(label):
        result = generate_workout_plan(
            goal=inputs["goal"],
            experience=inputs["experience"],
            days_per_week=inputs["days_per_week"],
            equipment=inputs["equipment"],
            injuries=inputs["injuries"],
            session_minutes=inputs["session_minutes"],
            model=inputs["model"],
            # A regenerate that reuses the same temperature tends to return a
            # near-identical plan, so nudge it up when asked for a variation.
            temperature=min(1.0, inputs["temperature"] + 0.25) if variation else inputs["temperature"],
            variation=variation,
            api_key=inputs["api_key"],
        )
    st.session_state.plan = result
    st.session_state.swap_result = None
    if result.ok:
        st.session_state.plan_inputs = inputs


def plan_filename(inputs: dict) -> str:
    """A dated, self-describing filename for the download button."""
    goal_slug = inputs["goal"].value.lower().replace(" ", "-")
    return f"workout-plan-{goal_slug}-{datetime.now():%Y%m%d-%H%M}.md"


def plan_as_markdown(result: GenerationResult, inputs: dict) -> str:
    """The plan plus a header recording the inputs it was generated from."""
    return "\n".join(
        [
            "# Workout Plan",
            "",
            f"- **Goal:** {inputs['goal'].value}",
            f"- **Experience:** {inputs['experience'].value}",
            f"- **Days per week:** {inputs['days_per_week']}",
            f"- **Session length:** {inputs['session_minutes']} minutes",
            f"- **Equipment:** {inputs['equipment'].value}",
            f"- **Injuries/limitations:** {inputs['injuries'].strip() or 'None reported'}",
            f"- **Generated:** {datetime.now():%d %b %Y, %H:%M} using `{result.model}`",
            "",
            "---",
            "",
            result.text,
            "",
            "---",
            "",
            "_General fitness guidance, not medical advice._",
        ]
    )


def render_swap_tool(inputs: dict) -> None:
    """Stretch goal: swap a single exercise without regenerating the whole plan."""
    st.divider()
    st.subheader("🔁 Swap an exercise")
    st.caption(
        "Don't like one movement? Get alternatives that respect the same equipment "
        "and injury constraints."
    )

    col_input, col_button = st.columns([3, 1])
    with col_input:
        exercise = st.text_input(
            "Exercise to replace",
            placeholder="e.g. Bulgarian split squat",
            label_visibility="collapsed",
        )
    with col_button:
        swap_clicked = st.button("Find alternatives", use_container_width=True)

    if swap_clicked:
        with st.spinner("Finding alternatives…"):
            st.session_state.swap_query = exercise
            st.session_state.swap_result = swap_exercise(
                goal=inputs["goal"],
                experience=inputs["experience"],
                equipment=inputs["equipment"],
                exercise=exercise,
                injuries=inputs["injuries"],
                model=inputs["model"],
                api_key=inputs["api_key"],
            )

    swap = st.session_state.swap_result
    if swap is None:
        return
    if swap.ok:
        st.markdown(swap.text)
    else:
        st.warning(swap.error)


def main() -> None:
    st.title("🏋️ Workout Plan Generator")
    st.caption(
        "Answer a few questions the way a trainer would ask them, and get a weekly "
        "plan built around your equipment, your schedule and your limitations."
    )

    inputs = collect_inputs()

    has_plan = st.session_state.plan is not None and st.session_state.plan.ok
    col_generate, col_regenerate, _ = st.columns([1, 1, 2])
    with col_generate:
        generate_clicked = st.button(
            "🎯 Generate Plan", type="primary", use_container_width=True
        )
    with col_regenerate:
        # Stretch goal: only offer a variation once there is something to vary.
        regenerate_clicked = st.button(
            "🔄 Regenerate",
            use_container_width=True,
            disabled=not has_plan,
            help="Same answers, a genuinely different plan.",
        )

    if generate_clicked:
        run_generation(inputs, variation=False)
        # `has_plan` above was evaluated before this click was handled, so the
        # Regenerate button has already been rendered as disabled. Rerun so the
        # script re-evaluates it against the plan we just stored.
        st.rerun()
    elif regenerate_clicked:
        # Regenerate from the inputs that produced the current plan, so editing a
        # widget without pressing Generate can't silently change what varies.
        run_generation(st.session_state.plan_inputs or inputs, variation=True)
        st.rerun()

    result: GenerationResult | None = st.session_state.plan
    if result is None:
        st.info(
            "Fill in the sidebar and press **Generate Plan**. Nothing is stored "
            "anywhere — your plan lives only in this browser session."
        )
        return

    if not result.ok:
        st.error(result.error)
        return

    used_inputs = st.session_state.plan_inputs or inputs

    for warning in result.warnings:
        st.warning(warning)

    st.markdown(result.text)

    st.download_button(
        "⬇️ Download plan (.md)",
        data=plan_as_markdown(result, used_inputs),
        file_name=plan_filename(used_inputs),
        mime="text/markdown",
    )

    render_swap_tool(used_inputs)

    st.divider()
    st.caption(
        f"Generated with `{result.model}` via Groq. This app gives general fitness "
        "guidance, not medical advice."
    )


if __name__ == "__main__":
    main()
