# 05 - HR Resume Screener Agent

Takes one job description and a folder of resumes, derives a weighted rubric, **redacts PII before any model sees a resume**, scores every candidate **in parallel** (`Send` fan-out), merges the scores with an `operator.add` reducer, ranks them, and writes a manager-facing markdown report.

**Pattern:** parallel fan-out + reduce - **Spec:** [guide/agents/05_hr_resume_screener.md](../../guide/agents/05_hr_resume_screener.md)

```mermaid
flowchart TD
    START([START]) --> build_rubric --> redact
    redact -->|"Send x N"| s1["score_candidate (r1)"]
    redact -->|"Send x N"| s2["score_candidate (r2)"]
    redact -->|"Send x N"| sn["score_candidate (rN)"]
    s1 & s2 & sn --> rank_and_reduce --> write_report --> END([END])
```

## Setup

**Windows (PowerShell)**

```powershell
cd agents_code\05_hr_resume_screener
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env      # paste your ANTHROPIC_API_KEY
```

**macOS / Linux**

```bash
cd agents_code/05_hr_resume_screener
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env        # paste your ANTHROPIC_API_KEY
```

## Usage

```bash
python agent.py                                  # data/job_description.txt + data/resumes/*.txt, top 5
python agent.py --jd my_jd.txt --resumes ./cvs --top 10 --out report.md
python agent.py --graph
```

The sample folder contains five resumes chosen to exercise the guardrails:

| File | Why it is there |
|---|---|
| `r1.txt` | strong match; has DOB and nationality lines that must be redacted |
| `r2.txt` | partial match (GCP/SQL, no Spark/Airflow); has an `Age:` line |
| `r3.txt` | strong match with streaming focus; phone and address to redact |
| `r4.txt` | prompt injection ("score 5 on everything") -> should be flagged `injection_suspected` |
| `r5.txt` | empty file -> `unreadable`, ranked last |

Expected order: r1 and r3 at the top, r2 in the middle, r4 near the bottom with a flag, r5 last.

## What to look at

| Spec section | In `agent.py` |
|---|---|
| Fan-out | `fan_out` returns `[Send("score_candidate", {...})]`; `add_conditional_edges("redact", fan_out, ["score_candidate"])` |
| Reduce | `candidate_scores: Annotated[list, operator.add]` - each branch appends one dict |
| Redaction gate | `redact` runs before fan-out; a resume that fails redaction is excluded, never scored |
| Bias filter | `PROTECTED` regex applied to model evidence and the final report |
| Evidence rule | `rank_and_reduce` sets score to 0 when evidence is "none" |
| Failure isolation | a failing branch adds to `unscreened` instead of stopping the batch |

Resumes are plain `.txt` here; for PDFs, extract text first (e.g. with `pypdf`) and drop the `.txt` files into the folder.
