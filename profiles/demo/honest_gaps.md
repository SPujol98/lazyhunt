# Honest-✗ registry (demo profile, fictional candidate "Alex Rivera")

This document lists every requirement concept that the demo claims DB
(`profiles/demo/claims.json`) and synonym map (`profiles/demo/synonyms.json`)
intentionally leave unmapped, with the reason it stays ✗.

**The rule: a gap leaves this list only when the candidate approves a new
claim.** Curation MUST NOT add an alias mapping merely to raise a score for a
requirement the candidate cannot genuinely back.

**Enforcement point**: an engine test such as
`tests/test_calibration.py::test_honest_gap_scores_zero` scores every entry
below against the claims DB and synonym map and asserts ✗.

## Cloud / infrastructure

`cloud`, `aws`, `azure`, `gcp`, `kubernetes` — no claim evidences hosted
cloud-platform or container-orchestration experience (Docker on a laptop only).

## Agent orchestration / MLOps

`langchain`, `langgraph`, `mcp`, `crewai`, `agent orchestration`, `mlops`,
`llmops` — Alex built a RAG service by hand but has no agent-framework or
MLOps tooling experience.

## Classical ML

`fine-tuning`, `ml`, `scikit-learn`, `tensorflow`, `keras` — no claim
evidences model training, fine-tuning or classical-ML frameworks; PyTorch is
claimed for inference only.

## Computer vision / edge hardware

`opencv`, `computer vision`, `gpu`, `edge devices`, `ip cameras`, `ffmpeg` —
no claim evidences computer-vision, GPU or video-pipeline work.

## CI/CD

`ci/cd` — Git is claimed, but no claim evidences a CI/CD pipeline.

## Frontend

`typescript`, `react`, `frontend` — every claim is backend, data or AI
focused; no frontend framework or language is evidenced.

## Low-code / RPA

`power platform`, `power automate`, `copilot studio`, `n8n`, `low-code` — no
claim evidences low-code or robotic-process-automation tooling.

## Statistics / forecasting

`estadistica`, `statistics`, `forecasting`, `series temporales` — no claim
evidences statistical modelling or time-series forecasting.

## Other languages / runtimes

`.net`, `c#`, `c++` — only Python, SQL and Bash are evidenced.

## Big data / data warehousing

`snowflake`, `dbt`, `pyspark`, `big data` — SQL, PostgreSQL and pandas are
evidenced; no claim covers a big-data or data-warehouse stack.

## Tooling not evidenced

`jupyter`, `openai`, `anthropic` — claims evidence a local Llama model only;
no hosted-provider API usage and no notebook workflow.

## Language

`german` — only Spanish, Catalan (basic) and English are evidenced.

## Soft-skill / trait claims not made

`proactive`, `proactividad`, `ownership`, `stakeholder management`,
`business acumen` — no claim states these traits or evidences this work.

## Code discipline

`clean code` — `skill-mypy` evidences static typing, a partial signal, not the
broader discipline, so the phrase stays unmapped.
