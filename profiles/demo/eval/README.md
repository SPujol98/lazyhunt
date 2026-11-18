# Demo eval bank (synthetic)

A small set of **invented** job postings, labeled for the fictional demo
persona Alex Rivera (`profiles/demo`). It lets you try `judge-eval`, the
harness that compares the LLM offer judge against expected scores and verdicts,
without any personal data.

Everything here is synthetic: the companies are fictional (`example.com`
domains), the postings were written for this repository, and the labels are
**hand-derived expectations**, not measurements and not a real person's
decisions. Use them to see how the harness works and to smoke-test prompt
changes, not as a benchmark.

## Layout

```
eval/
  labels.json    one entry per offer: file, score, verdict, walls, fit
  offers/*.txt   the full text of each fictional posting (Spanish, English, Catalan)
```

`labels.json` has the same shape as the original eval bank (`tolerance`,
`offers[]` with `file`, `score`, `verdict`, `walls`, `fit`). The extra keys
`expected_gates` and `expected_stack_wall` are only read by
`tests/test_demo_eval.py`, which checks that the deterministic gates
(eligibility, language, location, stack wall) fire on the hard-wall offers.

## What the set covers

- Clear fits (apply): junior RAG/LLM, Python backend, junior data engineering.
- Gray zone (apply with caveats): 3 years asked, cloud as a soft wall, fluent
  Catalan, a frontend-heavy full-stack role, a freelance contract.
- Hard walls (discard): Senior/lead, 4+ years, Java or .NET as the daily core,
  a mandatory MSc, US work authorization, mandatory German, 100% on-site Madrid.

The labels follow `profiles/demo/judge/rubric_persona.md`: up to about 3 years
of experience is a minor penalty, a degree with equivalence wording is
accepted, and only a degree without equivalence, 4+ years, seniority or a
daily core without claims are hard walls.

## Running it

Point the bot at the demo profile (`profile_dir = "profiles/demo"` in
`config.toml`, or the matching environment variable), then:

```bash
uv run cli.py judge-eval
```

With `profile_dir` set, the command reads `<profile_dir>/eval/labels.json`
automatically. Without it, it keeps using the legacy `eval/labels.json`. An
explicit `--labels path/to/labels.json` always wins.

This uses your configured LLM and spends quota: it prints a warning first, then
judges every offer and reports MAE, verdict agreement and how many offers fall
within `tolerance`. The test suite never calls a real LLM; it runs the harness
against a fake client.
