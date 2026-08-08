# cv-updater

A CLI tool that tailors a CV to a job posting. `master.json` holds the **superset** of every
experience, every bullet and every skill; the tool parses a posting into structured data, scores
the candidate material with a deterministic pre-filter, and the LLM step **selects and rephrases**
from that set — it never invents new content.

> **The honesty guardrail is the heart of the project.** The model may rank, select and rephrase
> existing material; it cannot invent an experience, a skill, a date or a metric.

```
posting.txt ─▶ [1] posting analysis ························ LLM
                  └── hallucination filter + tag mapping      deterministic
                                    │
master.json ────────▶ [2] pre-filter: tag scoring ··········· deterministic
 (superset of every                 │                         (halves the text sent to the LLM)
  experience, bullet                ▼
  and skill)             [3] selection + rephrasing ········· LLM
                                    │
                         [4] HONESTY GUARDRAIL ·············· deterministic
                                    │                         on a violation the run stops HERE
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
             tailored.json                   match_report.md
      (imported into Reactive Resume)   (fit score, reasoning, missing keywords)
                    │
                    ▼
              [5] render: RenderCV → Typst → PDF ············ deterministic  (optional)
                    │
                    ▼
              [6] ATS SELF-CHECK ··························· deterministic
                    │  read the text back out of the PDF: ligatures, keywords,
                    ▼  contact details, link visibility, headings, reading order, dates
              cv.pdf + ats_report.md
```

**Nothing the model says reaches the next step unverified.** Each of the two LLM steps is followed
by a deterministic audit.

## Install

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Emirhan-K/cv-updater
cd cv-updater
uv sync
uv sync --extra render --extra pdf   # optional: PDF generation and the fallback text extractor
```

There are two optional extras: `render` (RenderCV + Typst — for `cv-updater render`) and `pdf`
(pdfplumber — the fallback text extractor for the ATS check). The whole pipeline runs without
either of them.

For the LLM steps, copy `.env.example` to `.env`. Two options: a local gateway
(`LLM_BASE_URL` + `LLM_API_KEY`) or Anthropic directly (`ANTHROPIC_API_KEY`). `--help` and
`validate` work with no key at all.

## Status

**Phases 0–3 complete (issues 01–12): the tool runs end to end, from posting to PDF.** Posting
analysis, pre-filter, guardrailed tailoring, `tailored.json` + `match_report.md`, a single-command
PDF, and an ATS self-check of the PDF it just produced. The tests never touch the network.

Day-to-day use is in [`docs/USAGE.md`](docs/USAGE.md), the full pipeline in
[`docs/PIPELINE.md`](docs/PIPELINE.md), and how the guardrail works in
[`docs/GUARDRAILS.md`](docs/GUARDRAILS.md).

## Commands

| Command | What it does | LLM? |
|---|---|---|
| `cv-updater validate` | Validates `master.json` against the schema and runs the content lint | no |
| `cv-updater tailor <posting>` | Full pipeline: produces `tailored.json` + `match_report.md` | yes |
| `cv-updater score <slug>` | Runs only the pre-filter and shows the scoring as a table | no |
| `cv-updater report <slug>` | Regenerates the match report | no |
| `cv-updater render <slug>` | `tailored.json` → PDF via RenderCV/Typst, plus an automatic ATS check | no |
| `cv-updater check <pdf>` | Extracts the text back out of a PDF and scores ATS fit; exits 1 on a critical finding | no |
| `cv-updater log list/status/stats` | Application log: tracking, status, "above which score do replies come in" | no |

The guardrail is **strict** by default: if a violation is found the run stops and no
`tailored.json` is written. `--repair` reverts the offending text to its source and continues —
that is not a relaxation, it is throwing the invented text away.

`python -m cv_updater` gives the same interface.

## Example run

```bash
uv run cv-updater validate                    # is the superset healthy?

# give it the posting in one of three ways:
Get-Clipboard | uv run cv-updater tailor -    # from the clipboard (PowerShell)
uv run cv-updater tailor postings/acme.txt    # from a file
uv run cv-updater tailor --paste              # opens $EDITOR

uv run cv-updater score acme                  # inspect the pre-filter by eye; never hits the LLM
uv run cv-updater tailor posting.txt --lang en  # English CV from a Turkish posting
uv run cv-updater tailor posting.txt --repair   # don't stop on a violation, repair and continue
```

Every run writes to `out/<slug>-<timestamp>/`; previous output is never overwritten. A run
directory holds: `job_raw.txt` · `job_clean.txt` · `job.json` · `candidates.json` · `plan.json` ·
`violations.json` · **`tailored.json`** · **`match_report.md`**.

`--no-llm` skips both LLM steps, reusing the most recent analysis and plan — so you don't burn
quota while working on the steps that come after them.

## Why a superset instead of one CV

A single CV forces one framing of your history on every posting. The superset keeps every framing
at once: each experience carries multiple bullet variants — the same job shown from an AI angle or
a backend angle — in both Turkish and English, each tagged from a controlled vocabulary
(`data/tags.yaml`). The pre-filter scores those tags against the posting and hands the LLM a
shortlist rather than the whole history.

That split is what makes the guardrail enforceable: because every candidate bullet already exists
as text with a stable id, the verification step can check that what came back is grounded in what
went in.

## Development

```bash
uv run pytest                     # full suite, never touches the network
uv run pytest tests/test_cli.py   # single file
uv run ruff check .               # lint
uv run ruff format .              # formatting
```

## Documentation

Entry point: [`docs/INDEX.md`](docs/INDEX.md). Roadmap and issue list:
[`docs/ROADMAP.md`](docs/ROADMAP.md) →
[github.com/Emirhan-K/cv-updater/issues](https://github.com/Emirhan-K/cv-updater/issues).

## Privacy

`data/master.json` contains personal data — phone, email, address — and is in `.gitignore`. Only
the anonymized `data/master.example.json` is committed.

## Licence

[Apache-2.0](LICENSE).
