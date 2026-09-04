# 🏋️ Workout Plan Generator

A single-page Streamlit app that turns structured answers about your training
situation into a weekly workout plan you could actually follow, generated with an
LLM through the **Groq API**.

Built for the **AI Engineering Cohort — Session 2** assignment.

---

## What it does

You answer the questions a personal trainer would ask — goal, experience, days
available, session length, equipment, any injuries — and the app returns a
day-by-day plan with named exercises, sets, reps, rest, warm-ups, a progression
rule for the next four weeks, and a safety note if you mentioned a limitation.

| | |
|---|---|
| **Structured inputs** | Goal, experience level, days/week, minutes/session, equipment access, optional injuries |
| **Model** | `openai/gpt-oss-120b` on Groq (switchable to `qwen/qwen3.8-27b` or `openai/gpt-oss-20b`) |
| **Extras** | Regenerate for a different plan · plan persists across reruns · download as `.md` · swap a single exercise |

---

## Quickstart

```bash
git clone https://github.com/<your-username>/workout-plan-generator.git
cd workout-plan-generator

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then paste your key into .env
streamlit run app.py
```

Get a free Groq API key at **https://console.groq.com/keys**. Put it in `.env`:

```
GROQ_API_KEY=gsk_your_key_here
```

`.env` is gitignored, so the key never leaves your machine. If you'd rather not
create a file, you can paste the key into **Advanced settings** in the sidebar.

The app opens at http://localhost:8501.

---

## Project structure

Logic is separated from the UI so the interesting parts can be tested without a
browser or an API key.

```
workout-plan-generator/
├── app.py            # Streamlit UI only — widgets, session_state, rendering
├── models.py         # Enums + WorkoutRequest dataclass + input validation
├── prompts.py        # System prompt, rule blocks, prompt builders  ← the real work
├── groq_client.py    # Typed generate_workout_plan(), API + response error handling
├── tests/            # 42 offline tests — logic, prompts, error paths, and the UI
├── requirements.txt
├── .env.example
└── README.md
```

| Module | Why it's separate |
|---|---|
| `models.py` | One definition of the input contract. The same enums render the dropdowns *and* feed the prompt, so the UI and the prompt can never disagree about what "Home dumbbells" means. |
| `prompts.py` | Prompt text is the product here, so it lives in its own file where it can be read, reviewed and iterated on without touching app plumbing. |
| `groq_client.py` | Imports no Streamlit. That's what makes the whole error-handling surface unit-testable. |
| `app.py` | Contains no prompt text and no API calls — just input collection and rendering. |

---

## Prompt design — the actual exercise

Concatenating the answers into a sentence ("make me a 3 day plan for building
muscle with no equipment") produces plans that quietly break the constraints. The
prompt here is built in layers, each one targeting a specific failure mode. The
**Iteration log** at the end of this section records what actually happened when
each layer was tested against real Groq responses.

### 1. Equipment as an allow-list *and* a forbid-list

Stating *"the user has no equipment"* leaves the model free to reach for whatever
"chest day" looks like in its training data — barbell bench press included. So each
equipment option carries an explicit list of what's available **and** a list of what
is forbidden:

```
AVAILABLE: bodyweight only. Floor space, a wall, a sturdy chair or step…
FORBIDDEN: dumbbells, barbells, kettlebells, weight plates, benches, racks,
machines, cables, pull-up bars, resistance bands…
If you need more difficulty, use tempo, pauses, unilateral variations,
range of motion, or higher reps — never added weight.
```

That last line matters: taking equipment away leaves the model with no way to make
training harder, so it reaches for equipment anyway unless you hand it a legal
alternative. Pull-up bars and resistance bands are named explicitly because they
*feel* like bodyweight training and are the most likely things to slip into a
"no equipment" plan.

### 2. Answers as a spec block, not prose

The inputs go in as labelled fields rather than a sentence:

```
GOAL: Lose fat
EXPERIENCE_LEVEL: Beginner
DAYS_PER_WEEK: 3
SESSION_MINUTES: 45
EQUIPMENT_ACCESS: No equipment
INJURIES_OR_LIMITATIONS: bad knees, no jumping
```

In prose, whichever value sits in the middle of the sentence is the one that gets
dropped. As a labelled block, every value has equal weight and the system prompt
can refer to fields by name (`DAYS_PER_WEEK`) in its rules.

### 3. Interpretation is supplied, not left to the model

"Intermediate" and "build muscle" mean different things to different coaches, so the
prompt injects the interpretation instead of hoping: rep ranges, rest periods,
weekly set volume, exercise count per session, and which advanced techniques are
allowed. Without it, different inputs tend to converge on the same generic
"3 sets of 10" plan — plans that differ in their *headings* but not their content.

### 4. A fixed output contract

The reply must follow one markdown template — summary line, then `### Day N` with a
sets/reps/rest/notes table, warm-up and cool-down, then rest days, a four-week
progression rule, and practical notes. Specifying the *table columns* is what forces
real numbers into every row — an open-ended format invites "as many reps as you can
manage" instead of a prescription.

### 5. Self-verification before answering

The single highest-value block. The model re-reads its own draft against the hard
constraints before replying:

```
- [ ] The number of `### Day N` sections exactly equals DAYS_PER_WEEK.
- [ ] Not one exercise requires forbidden equipment.
- [ ] Not one exercise loads a stated injury.
- [ ] Every row has real numbers for sets, reps and rest.
…If any check fails, fix the plan before replying.
```

This targets day-count drift in particular: ask for 2 days and a model will lean
towards the 4-day splits that dominate its training data unless it is made to count
its own sections.

### 6. Injuries handled by substitution, not warnings

The model's instinct is to keep the exercise and append *"be careful with your
knees"*. That's useless advice. The prompt forbids it: pick a different movement
that trains the same pattern safely, and say why in the notes column.

### 7. Scope guard

The model is told it is a coach and not a clinician — no diagnosis, no claims of
treating or curing anything, no calories or supplements. When injury input is
present, a short disclaimer is appended as the final line, and the app footer
carries a standing one.

### Iteration log

Five adversarial input combinations were run against the live API and audited
**programmatically** rather than by eye — a script checked each reply for day count,
day numbering, forbidden-equipment keywords, injury-contraindicated keywords, vague
prescriptions ("AMRAP", "as many as you can"), table structure, the conditional
disclaimer, and medical overreach.

| # | Input combination | Why it's hard |
|---|---|---|
| A | Build muscle · beginner · 2 days · no equipment · 30 min · *bad knees, no jumping* | Minimal equipment plus a movement ban |
| B | Lose fat · beginner · **7 days** · full gym · 60 min | Tempts over-programming a novice |
| C | Build muscle · advanced · 6 days · full gym · **20 min** | Tiny time budget vs. high training age |
| D | Endurance · intermediate · 4 days · home dumbbells · *shoulder impingement, no overhead pressing* | Endurance goal with no cardio machines |
| E | Lose fat · intermediate · 5 days · no equipment · *post lumbar surgery, no spinal loading or bending* | Hardest: bodyweight-only with a spinal contraindication |

**Round 1 — 4 of 5 clean.** Day counts were correct in every case, including the 7-day
and 6-day requests; no vague prescriptions appeared anywhere; the conditional disclaimer
fired exactly when injuries were present and never otherwise. Case E produced two real
violations:

1. **A caution offered in place of a substitution.** The plan contained
   `| Superman (prone arm/leg lift) | 3 | 12-15 | 45 s | Posterior chain, avoid excessive lumbar arch |`
   — a loaded spinal extension prescribed to someone who had said *no spinal loading or
   bending*, softened with a warning. The prompt already forbade exactly this, and the
   model did it anyway; the instruction wasn't concrete enough about what the evasion
   looks like.
2. **The constraint leaked outside the day tables.** The equipment forbid-list was
   honoured perfectly across all five day tables, then broke in **Rest & Recovery**:
   *"a 20-min easy bike/elliptical session"*. The model doesn't classify
   active-recovery suggestions as "exercises", so a rule about exercises didn't reach them.

**The fixes:**

| Violation | Change |
|---|---|
| Caution instead of substitution | The injury rule now says *"A caution is NOT a fix"* and quotes the actual hedges the model reaches for — "avoid excessive…", "be careful with…", "don't overdo…" — declaring that writing one is itself proof the exercise is wrong. Extended to unloaded positions, not just loaded ones. |
| Recovery-section leak | The equipment rule now enumerates its scope: day tables, warm-ups, cool-downs, conditioning blocks, finishers, *and* rest/active-recovery days — plus "never offer forbidden equipment as an 'option' or an 'alternative'". |
| Both | The self-verification checklist now names the leaky sections explicitly and adds: *"no Notes cell warns about the injured area instead of simply avoiding it."* |

**Round 2 — 5 of 5 clean.** Same five inputs, zero equipment violations, zero injury
violations, correct day counts, disclaimer behaviour intact. Case E's plan replaced the
Superman with knee- and spine-safe alternatives and dropped the elliptical suggestion.

### Two things the iteration taught me about *auditing* prompts

- **Keyword matching without context produces false alarms.** The audit flagged
  `| Incline Push-Up | … | Chest emphasis, no overhead |` and *"avoids any knee-stressful
  or jumping movements"* as violations. Both were the model **confirming** compliance.
  Every flag needs reading in context before it counts.
- **Normalise unicode before matching.** `gpt-oss` writes non-breaking hyphens (U+2011)
  and narrow no-break spaces (U+202F), so searching for `"pull-up bar"` silently misses
  `"pull‑up bar"`. A prompt audit that isn't dash-normalised will report success it hasn't
  earned.

### A bug the UI tests caught

`has_plan` is evaluated at the top of the script, but Streamlit re-executes the whole
file on every interaction, and a widget rendered *before* the click is handled keeps
whatever state it was given. So the Regenerate button rendered as `disabled` in the
very run that produced the plan, and stayed greyed out until the user happened to touch
another widget. Fixed with an explicit `st.rerun()` after a generation, so the script
re-evaluates the button against the plan it just stored. Locked down by
`test_generating_renders_the_plan_and_enables_the_extras`.

### On the regenerate button

Same inputs at a higher temperature with the variation instruction gave **16% exercise
overlap** (5 shared of 31 distinct movements) — genuinely different work, not a reshuffle.
The *split* stayed Push/Pull/Legs both times, which is reasonable: at 3 days there is
really only one sensible structure, and varying it just to look different would make the
plan worse.

### What I'd watch next

Long injury descriptions listing several unrelated limitations are the hardest case:
nothing in the prompt forces the model to account for each limitation separately. A
stricter fix would be a second programmatic pass that re-reads the generated plan
against the injury list before it is shown.

---

## Error handling

Three layers, each producing a friendly message and never a traceback.

| Layer | Handles | User sees |
|---|---|---|
| **Input validation** (`WorkoutRequest.validate`) | 0 or 8+ days, out-of-range session length, non-numeric values, over-long injury text | A bullet list of exactly what to fix — checked *before* any network call, so bad input never costs an API request |
| **API failure** (`_friendly_api_error`) | Missing key, bad key, rate limit, timeout, no connection, retired model, Groq 5xx, anything unexpected | A specific explanation and what to do about it, e.g. rate limit → "wait about a minute or switch to a smaller model" |
| **Bad output** (`_check_plan_text`) | Empty reply, no `choices` in the response, truncated text, no day structure | A fallback message suggesting a retry. A plan with the *wrong day count* is still shown, with a warning — hiding it would hide a prompt failure |

`generate_workout_plan` and `swap_exercise` never raise: they return a
`GenerationResult` with `ok`, `text`, `error` and `warnings`, so the UI has exactly
one shape to render and no path to an unhandled exception.

### Both of these fired for real during development

- **A retired model.** The original default was `llama-3.3-70b-versatile`, taken from
  Groq's own docs page — every call returned `404 … does not exist or you do not have
  access to it`. The error layer caught it without crashing, but reported it as a generic
  API error, because a 404 is `NotFoundError` and I had only mapped `BadRequestError`.
  Now `NotFoundError` has its own branch telling the user to pick another model, and the
  live list is worth checking with `client.models.list()` rather than trusting the docs.
- **A real rate limit.** Running five plans back to back on the free tier tripped
  `RateLimitError` mid-audit. The friendly message appeared exactly as intended, which is
  the one error path that's genuinely awkward to test on purpose.

---

## Tests

```bash
.venv/bin/python -m pytest -q     # 42 passed
```

No API key and no network needed — the Groq call is stubbed. Coverage:

- valid and invalid inputs, including `0` days and non-integer days
- every structured input actually appearing in the prompt
- the "no equipment" forbid-list, the injury disclaimer, the variation nudge
- swap prompts re-stating equipment and injury constraints
- empty / structureless / short replies rejected; day-count mismatch warned
- each Groq exception type mapping to its friendly message
- invalid input asserted **never** to reach the API

`tests/test_app_ui.py` additionally drives the real Streamlit app through
`streamlit.testing.v1.AppTest` with the API stubbed, covering the wiring unit tests
can't see: that widget values actually arrive at `generate_workout_plan`, that
Regenerate is disabled until a plan exists and enabled immediately after one, that it
requests a variation at a higher temperature, and that a failed call renders an error
banner with no plan leaking through. That last group caught a real bug — see below.

---

## Tech stack

Python 3.10 · Streamlit · Groq (`openai/gpt-oss-120b`) · python-dotenv · pytest

---

## Disclaimer

This app produces general fitness guidance generated by a language model. It is not
medical advice. Consult a qualified professional before starting a new training
programme, especially with an existing injury.
