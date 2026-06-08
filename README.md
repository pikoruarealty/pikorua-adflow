# pikorua-adflow

Agentic digital marketing pipeline for Pikorua Realty, built on [CrewAI](https://crewai.com).

Takes a campaign brief (property, platform, goal, budget, city) and autonomously generates audience personas, competitor analysis, ad copy variants, WhatsApp scripts, and email sequences. Human review is required before any deployment.

---

## Setup

Requires Python >=3.10 <3.14.

```bash
pip install uv
crewai install
```

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

---

## Running the pipeline

```bash
python -m pikorua_adflow.main
```

Or via the CrewAI CLI:

```bash
crewai run
```

Outputs are saved to `outputs/pending_review/<timestamp>/` for human review before any deployment step.

---

## Project structure

```
src/pikorua_adflow/
├── crews/
│   ├── audience_crew/    # Stage 1: persona, competitor analysis, trend hooks
│   └── content_crew/     # Stage 2: ad copy, WhatsApp, email variants
├── tools/                # SerperDev, Meta API, Qdrant wrappers
├── utils/                # Brand voice loader, output saver, cost tracker
├── api/                  # FastAPI portal endpoint (Task 1.6)
└── main.py               # Pipeline entry point
project_context/          # Brand voice, data audit, examples (fill before prod use)
outputs/pending_review/   # Human review folder — outputs land here
docs/runbook.md           # Operations runbook (Task 5.1)
```

---

## Phase status

See [tasklist.md](../tasklist.md) for the full task list and progress tracker.
