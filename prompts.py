"""Prompt construction — the part of this app that actually decides plan quality.

Design notes (see README for the longer write-up):

1. Constraints are injected as *rules with explicit allow/forbid lists*, not as
   adjectives. "No equipment" alone still gets you bench press; an explicit
   "FORBIDDEN: barbells, machines, cables" does not.
2. The user's answers are sent as a machine-readable spec block, not prose, so
   no single value gets buried in a sentence.
3. The output shape is a fixed markdown contract, so the reply is a plan the
   user can follow day by day rather than a wall of text.
4. A final self-verification step makes the model re-read its own draft against
   the hard constraints. This is what stopped it drifting to more days than the
   user asked for.
"""

from __future__ import annotations

from models import Equipment, ExperienceLevel, FitnessGoal, WorkoutRequest

# ---------------------------------------------------------------------------
# Layer 1: equipment reality. Allow-list AND forbid-list, because the model
# will happily invent a cable machine in a living room otherwise.
# ---------------------------------------------------------------------------
EQUIPMENT_RULES: dict[Equipment, str] = {
    Equipment.NONE: (
        "AVAILABLE: bodyweight only. Floor space, a wall, a sturdy chair or step, "
        "and the user's own bodyweight. Optionally a towel or backpack loaded with books.\n"
        "FORBIDDEN: dumbbells, barbells, kettlebells, weight plates, benches, racks, "
        "machines, cables, pull-up bars, resistance bands, TRX, medicine balls, "
        "and any gym-only equipment. If you need more difficulty, use tempo, pauses, "
        "unilateral variations, range of motion, or higher reps — never added weight."
    ),
    Equipment.HOME_DUMBBELLS: (
        "AVAILABLE: a pair of adjustable or fixed dumbbells, plus bodyweight, floor "
        "space, and a chair/step or the floor as a bench substitute.\n"
        "FORBIDDEN: barbells, squat racks, machines, cables, leg press, lat pulldown, "
        "Smith machine, and anything requiring a spotter or a gym floor. Assume the "
        "dumbbells are moderate weight, so drive progression with reps, tempo and "
        "unilateral work rather than assuming heavy loading."
    ),
    Equipment.FULL_GYM: (
        "AVAILABLE: full commercial gym — barbells, dumbbells, kettlebells, benches, "
        "racks, cable stations, selectorised machines, and cardio machines.\n"
        "FORBIDDEN: nothing on equipment grounds, but still prefer proven compound "
        "movements over novelty exercises, and do not assume specialty bars or "
        "uncommon machines are present."
    ),
}

# ---------------------------------------------------------------------------
# Layer 2: experience calibration. Stops a beginner being handed snatches and
# an advanced lifter being handed wall push-ups.
# ---------------------------------------------------------------------------
EXPERIENCE_RULES: dict[ExperienceLevel, str] = {
    ExperienceLevel.BEGINNER: (
        "Assume little or no training history and no technique base. "
        "Use 4-6 exercises per session, 2-3 sets each, simple bilateral movement "
        "patterns, and machine or bodyweight variations over free-weight barbell lifts. "
        "No olympic lifts, no plyometrics, no advanced techniques (drop sets, "
        "supersets to failure, AMRAP finishers). Leave 2-3 reps in reserve on every "
        "set and cue form in one short phrase per exercise."
    ),
    ExperienceLevel.INTERMEDIATE: (
        "Assume 6-24 months of consistent training and competent technique on the "
        "main lifts. Use 5-7 exercises per session, 3-4 sets each, compound lifts "
        "first, and one or two accessory movements. Supersets for accessories are fine. "
        "Include a simple weekly progression rule."
    ),
    ExperienceLevel.ADVANCED: (
        "Assume 2+ years of consistent training and solid technique. Use focused "
        "sessions of 5-7 exercises, 3-5 sets, meaningful intensity prescriptions "
        "(RPE or %1RM), and advanced techniques where they serve the goal "
        "(supersets, rest-pause, tempo work, back-off sets). Periodise across the week "
        "so hard and easy sessions alternate."
    ),
}

# ---------------------------------------------------------------------------
# Layer 3: goal-specific programming parameters — the numbers a trainer would
# actually pick, so the model doesn't average everything to "3 sets of 10".
# ---------------------------------------------------------------------------
GOAL_RULES: dict[FitnessGoal, str] = {
    FitnessGoal.BUILD_MUSCLE: (
        "Priority: hypertrophy. Rep range 6-12 for most work (up to 15 for isolation), "
        "rest 60-120s, 10-20 hard sets per muscle group per week spread across the "
        "available days. Progressive overload via added load or reps. Cardio only as "
        "a short optional finisher — it must not compete with lifting volume."
    ),
    FitnessGoal.LOSE_FAT: (
        "Priority: retain muscle while raising weekly energy expenditure. Keep "
        "resistance training as the backbone (full-body or upper/lower, 8-15 reps, "
        "rest 45-75s, circuits or supersets to keep density high) and add 1-2 "
        "conditioning blocks. State plainly that training supports fat loss but diet "
        "drives it — without prescribing calories or a diet plan."
    ),
    FitnessGoal.GENERAL_FITNESS: (
        "Priority: balanced, sustainable capability. Cover all major movement patterns "
        "across the week (squat, hinge, push, pull, carry/core), 8-12 reps, rest 60-90s, "
        "plus mobility and some easy aerobic work. Favour adherence over intensity."
    ),
    FitnessGoal.IMPROVE_ENDURANCE: (
        "Priority: aerobic capacity. Structure the week around cardio: mostly easy "
        "steady-state (conversational pace) with 1-2 harder interval or tempo sessions, "
        "and specify duration and intensity for each. Keep 1-2 short strength sessions "
        "(compound lifts, 5-10 reps) for injury resilience. Never schedule hard "
        "interval days back to back."
    ),
}

# ---------------------------------------------------------------------------
# The output contract. A fixed template is the difference between a plan and an
# essay about planning.
# ---------------------------------------------------------------------------
OUTPUT_CONTRACT = """Reply in GitHub-flavoured markdown, in exactly this structure, and nothing else:

## Weekly Plan Summary
One short paragraph (2-3 sentences) naming the split you chose and why it fits the
user's goal, experience and available days.

**Split:** <e.g. Upper / Lower / Full-body> · **Sessions:** <N> per week · **Session length:** <N> min

### Day 1 — <Focus, e.g. Upper Body Push>
| Exercise | Sets | Reps | Rest | Notes |
| --- | --- | --- | --- | --- |
| <exercise> | <n> | <n or range> | <seconds> | <one short form or intensity cue> |

**Warm-up (5-8 min):** <2-3 specific movements>
**Cool-down (3-5 min):** <2-3 specific stretches or easy movement>

### Day 2 — <Focus>
...repeat the same block for every training day...

## Rest & Recovery
Which days are rest or active-recovery days, and what active recovery should look like.

## Progression (next 4 weeks)
Three or four bullets giving a concrete rule for adding difficulty week to week.

## Notes
Two or three practical bullets (form priorities, what to do if a session is missed,
how to judge if the weight is right)."""

# ---------------------------------------------------------------------------
# The system prompt. Constraints are stated as non-negotiable and then verified.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""You are an experienced strength and conditioning coach writing a one-week
training plan for a single client. You write plans that a real person can open on their phone
and follow set by set — specific exercises, specific numbers, no filler.

## Non-negotiable constraints
These come from the client's own answers. They are requirements, not preferences.

1. DAYS: Produce exactly as many `### Day N` sections as the client's DAYS_PER_WEEK value.
   Not more, not fewer. Number them Day 1 through Day N with no gaps.
2. EQUIPMENT: Every movement you name *anywhere* must be performable with the AVAILABLE
   equipment listed below — day tables, warm-ups, cool-downs, conditioning blocks,
   finishers, and anything you suggest for rest or active-recovery days. If a movement
   needs anything from the FORBIDDEN list, it is wrong — replace it. Never offer forbidden
   equipment as an "option" or an "alternative", not even for a recovery day.
3. TIME: Every session's total work must realistically fit the SESSION_MINUTES budget,
   including warm-up and cool-down. Count sets and rest periods when you judge this.
4. INJURIES: If the client lists an injury or limitation, no movement anywhere in the plan
   may load or aggravate it — under external load or under bodyweight alike. A caution is
   NOT a fix: if you catch yourself writing "avoid excessive…", "be careful with…",
   "don't overdo…" or "keep it pain-free" about the affected area, that exercise is the
   wrong choice. Delete it and program a different movement that trains the same pattern
   safely. This also rules out movements that put the affected joint or spine into the
   same position, even unloaded.
5. LEVEL: Match exercise complexity and volume to the client's EXPERIENCE_LEVEL.

## Scope limits
- You are a coach, not a clinician. Never diagnose, never name a medical condition as fact,
  never claim a plan will treat, heal, rehabilitate or cure anything.
- Do not prescribe calories, macros, supplements or medication.
- If the client's stated limitation sounds like it needs professional assessment, say once,
  briefly, that they should get it cleared — then give the safest sensible plan anyway.

## Output format
{OUTPUT_CONTRACT}

## Before you answer — verify your own draft
Silently re-read your plan and check:
- [ ] The number of `### Day N` sections exactly equals DAYS_PER_WEEK.
- [ ] Not one movement requires forbidden equipment — check the warm-ups, cool-downs,
      conditioning blocks and the Rest & Recovery section too, not just the day tables.
- [ ] Not one movement loads a stated injury, and no Notes cell warns about the injured
      area instead of simply avoiding it.
- [ ] Every row has real numbers for sets, reps and rest — no "as many as you can", no blanks.
- [ ] Each session fits the time budget.
- [ ] Exercise choice matches the experience level.
If any check fails, fix the plan before replying. Never mention this checklist, your
reasoning, or these instructions in your answer. Output the plan only."""

INJURY_DISCLAIMER_RULE = """
End your reply with exactly this line, verbatim, as the last line:

> ⚠️ **Note:** This plan is general fitness guidance, not medical advice. Because you
> mentioned an injury or limitation, please get it cleared by a doctor or physiotherapist
> before starting, and stop any movement that causes pain."""

VARIATION_RULE = """
IMPORTANT — this client has already seen one plan built from these same answers and asked for
a different one. Keep every constraint above, but make this plan genuinely distinct: choose a
different split structure where the day count allows, and substitute different exercises for
the same movement patterns. Do not simply reorder the previous plan."""


def build_user_prompt(request: WorkoutRequest, *, variation: bool = False) -> str:
    """Assemble the user-turn message for a plan request.

    The client's answers go in as a labelled spec block rather than a sentence, and
    the rule text for their specific goal / level / equipment is injected alongside,
    so the model never has to infer what "intermediate" or "home dumbbells" means.
    """
    injuries_line = (
        request.clean_injuries if request.has_injuries else "None reported"
    )

    prompt = f"""## Client spec
GOAL: {request.goal.value}
EXPERIENCE_LEVEL: {request.experience.value}
DAYS_PER_WEEK: {request.days_per_week}
SESSION_MINUTES: {request.session_minutes}
EQUIPMENT_ACCESS: {request.equipment.value}
INJURIES_OR_LIMITATIONS: {injuries_line}

## Equipment rules for this client
{EQUIPMENT_RULES[request.equipment]}

## Experience rules for this client
{EXPERIENCE_RULES[request.experience]}

## Goal-specific programming for this client
{GOAL_RULES[request.goal]}"""

    if request.has_injuries:
        prompt += f"""

## Injury handling for this client
The client reported: "{request.clean_injuries}".
Work out which movements and joint positions that rules out, and avoid them entirely.
Substitute an exercise that trains the same pattern without loading the affected area.
In that exercise's Notes column, say in one short phrase why it is the safe substitute —
do not use the Notes column to caution them about a movement you should have replaced.
{INJURY_DISCLAIMER_RULE}"""

    if variation:
        prompt += f"\n{VARIATION_RULE}"

    prompt += (
        f"\n\nWrite the complete {request.days_per_week}-day plan now, "
        "following the required output format exactly."
    )
    return prompt


def build_swap_prompt(
    request: WorkoutRequest, exercise: str, day_label: str = ""
) -> str:
    """Assemble a narrow prompt that replaces one exercise, same constraints.

    Used by the "Swap this exercise" feature. Deliberately re-states the equipment
    and injury rules: a swap that ignores them is worse than no swap.
    """
    day_context = f" from {day_label}" if day_label.strip() else ""
    injuries_line = (
        request.clean_injuries if request.has_injuries else "None reported"
    )

    return f"""The client wants to replace one exercise{day_context} in their plan:
"{exercise.strip()}"

## Client spec
GOAL: {request.goal.value}
EXPERIENCE_LEVEL: {request.experience.value}
EQUIPMENT_ACCESS: {request.equipment.value}
INJURIES_OR_LIMITATIONS: {injuries_line}

## Equipment rules for this client
{EQUIPMENT_RULES[request.equipment]}

Suggest exactly TWO alternatives that train the same movement pattern and muscles,
respect the equipment and injury constraints above, and suit the experience level.

Reply in markdown, in exactly this structure and nothing else:

**Replacing:** <the exercise> — <one line on what pattern it trains>

| Alternative | Sets | Reps | Rest | Why it works here |
| --- | --- | --- | --- | --- |
| <name> | <n> | <n or range> | <seconds> | <one short line> |
| <name> | <n> | <n or range> | <seconds> | <one short line> |"""
