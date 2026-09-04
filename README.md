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
| **Model** | `llama-3.3-70b-versatile` on Groq (switchable to `openai/gpt-oss-120b` or `llama-3.1-8b-instant`) |
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
├── tests/            # 35 offline tests covering validation, prompts, error paths
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
prompt here is built in layers, each one closing a specific failure I hit while
iterating.

### 1. Equipment as an allow-list *and* a forbid-list

Saying *"the user has no equipment"* is not enough — the model still returns
barbell bench press, because bench press is what "chest day" looks like in its
training data. So each equipment option carries an explicit list of what's
available **and** a list of what is forbidden:

```
AVAILABLE: bodyweight only. Floor space, a wall, a sturdy chair or step…
FORBIDDEN: dumbbells, barbells, kettlebells, weight plates, benches, racks,
machines, cables, pull-up bars, resistance bands…
If you need more difficulty, use tempo, pauses, unilateral variations,
range of motion, or higher reps — never added weight.
```

That last line matters: taking equipment away leaves the model with no way to make
things harder, so it reaches for equipment anyway unless you hand it a legal
alternative. Naming pull-up bars and resistance bands specifically was necessary —
those two kept slipping into "no equipment" plans because they *feel* like
bodyweight training.

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
allowed. Without this, every combination of inputs converged on the same generic
"3 sets of 10" plan — the plans differed in their *headings* but not their content.

### 4. A fixed output contract

The reply must follow one markdown template — summary line, then `### Day N` with a
sets/reps/rest/notes table, warm-up and cool-down, then rest days, a four-week
progression rule, and practical notes. Specifying the *table columns* is what
forced real numbers into every row; before that, "as many reps as you can manage"
appeared constantly.

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

The day-count drift (asking for 2 days and getting 4, because most plans in the
training data are 4-day plans) largely stopped once this was added.

### 6. Injuries handled by substitution, not warnings

The model's instinct is to keep the exercise and append *"be careful with your
knees"*. That's useless advice. The prompt forbids it: pick a different movement
that trains the same pattern safely, and say why in the notes column.

### 7. Scope guard

The model is told it is a coach and not a clinician — no diagnosis, no claims of
treating or curing anything, no calories or supplements. When injury input is
present, a short disclaimer is appended as the final line, and the app footer
carries a standing one.

### What I'd watch next

Long injury descriptions listing several unrelated limitations are still the
weakest case — the model tends to honour the first one thoroughly and the later
ones loosely. A stricter fix would be a second validation pass that re-reads the
generated plan against the injury list programmatically.

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

---

## Tests

```bash
.venv/bin/python -m pytest -q     # 35 passed
```

No API key and no network needed — the Groq call is stubbed. Coverage:

- valid and invalid inputs, including `0` days and non-integer days
- every structured input actually appearing in the prompt
- the "no equipment" forbid-list, the injury disclaimer, the variation nudge
- swap prompts re-stating equipment and injury constraints
- empty / structureless / short replies rejected; day-count mismatch warned
- each Groq exception type mapping to its friendly message
- invalid input asserted **never** to reach the API

---

## Tech stack

Python 3.10 · Streamlit · Groq (`llama-3.3-70b-versatile`) · python-dotenv · pytest

---

## Disclaimer

This app produces general fitness guidance generated by a language model. It is not
medical advice. Consult a qualified professional before starting a new training
programme, especially with an existing injury.
