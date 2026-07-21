# cv-updater — Plan and GitHub issues

The actionable plan behind `TODO.md` §5 ("Job-posting-driven CV tailoring tool") and the
§9 entry "İş ilanına göre CV güncelleyici".

## What it does

```
job.txt ──▶ [1] posting analysis (LLM, structured output)
                     │
master.json ─────────┼──▶ [2] deterministic pre-filter (tag / keyword scoring)
 (superset of every  │
  experience, bullet └──▶ [3] LLM selection + rephrasing (honesty guardrail)
  and skill)                 │
                             ├──▶ tailored.json  (importable into Reactive Resume)
                             └──▶ match_report.md (why these bullets, which keywords are missing)
                                          │
                       PDF ──▶ [4] ATS self-check (extract text back, verify keywords + ligatures)
```

**Double value:** a tool actually used on every application *and* a portfolio project
(structured output, guardrail design, a verification layer — a level above "I prompted an LLM").

## Technical decisions

| Topic | Decision | Why |
|---|---|---|
| Language / packaging | Python 3.12 + `uv` | same ecosystem as doqqy |
| CLI | `typer` | already used in doqqy |
| Schemas | `pydantic` v2 | maps directly onto structured output |
| LLM | Anthropic SDK, `client.messages.parse(output_format=...)`, model `claude-opus-4-8` | validated JSON output |
| Resume schema | [Reactive Resume](https://github.com/AmruthPillai/Reactive-Resume) JSON — live schema at `https://rxresu.me/schema.json` | generated file imports directly |
| PDF text | `pypdf` (with `pdfplumber` fallback) | ATS self-check |
| Render v1 | tailored.json → manual import into Reactive Resume | ship the MVP fast |
| Render v2 | RenderCV (YAML → Typst → PDF) | full automation, ligature control on our side |
| Tests | `pytest` | guardrails must be testable |

**Reactive Resume schema at a glance** (v2 format, detailed in `issues/02`):
`picture`, `basics`, `summary`, `sections{experience, education, skills, projects, languages,
certifications, awards, publications, volunteer, interests, references, profiles}`,
`customSections[]`, `metadata{template, layout, page, css, theme, typography}`.
Section items carry an HTML-string `description`; every item has an `id` and a `hidden` flag.

## Issue list

| # | File | Phase | Depends on |
|---|---|---|---|
| 01 | Project skeleton: uv + typer + pydantic + pytest | 0 | — |
| 02 | `master.json` schema: Reactive Resume compatible + `x_meta` extension | 0 | 01 |
| 03 | Move your own CV into `master.json` + `validate` command | 0 | 02 |
| 04 | Posting input: file / stdin / paste + normalization | 1 | 01 |
| 05 | Posting analysis: `JobPosting` structured output (LLM step #1) | 1 | 04 |
| 06 | Deterministic pre-filter: tag + keyword scoring (no LLM) | 2 | 03, 05 |
| 07 | LLM tailoring step + **honesty guardrail** (the core engineering problem) | 2 | 06 |
| 08 | `tailored.json` generation + Reactive Resume schema validation | 2 | 07 |
| 09 | `match_report.md`: match score, selection rationale, missing keywords | 2 | 07 |
| 10 | ATS self-check: extract text back from the PDF, verify, score | 3 | 08 |
| 11 | Render v2: RenderCV, YAML → Typst → PDF (ligature-free) | 3 | 08 |
| 12 | Application log: one record per application under `applications/` | 3 | 08, 09 |
| 13 | Post-MVP roadmap (tracking issue) | 4 | — |

**MVP line: 01–10.** 11–12 are quality-of-life, 13 is a tracking issue.

Every issue ends with a **📄 Documentation** section: the relevant doc is created or updated
before the issue is closed (`docs/INDEX.md` is kept current each time).

## Uploading

Once the repo exists:

```powershell
cd C:\Users\Emirhan\Documents\github\cv-updater\issues
.\upload-issues.ps1 -Repo "Emirhan-K/cv-updater"
```

The script creates the phase labels (`phase-0`..`phase-4`, `docs`) first, then uploads the 13 issues
in order. Requires `gh auth login`.

## Privacy note

`master.json` holds personal data (phone, email, address). If the repo goes public:
keep `master.json` in `.gitignore` and commit only an anonymized `master.example.json`.
This is an acceptance criterion in issue 02.
