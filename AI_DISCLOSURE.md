# AI assistance disclosure

This project was built with substantial help from Claude, an AI assistant made by Anthropic,
used through the Claude app. This file records what the assistant did, what I did, and how the
work was checked. Edit the "My own work" section to keep it accurate as the project goes on.

## What the assistant did

- Helped choose and scope the topic, checked feasibility against the course rubric and the development
  guidelines, and wrote the project plan.
- Wrote the first version of the code in this repository: the `chessfeat` library, the six pipeline stages,
  the pipeline runner, the model-server watcher, the backend, the web pages, the monitoring configuration and
  dashboard, the Docker and Compose files, the scripts and the tests.
- Wrote the first drafts of the documents in `docs/`, this README and the EDA notebook.
- Tested the code in its own development sandbox: unit and API tests, config checks, and an end-to-end run of
  the whole system as local processes with **synthetic, engine-generated games** (the sandbox could not reach
  Lichess or run Docker). Defects found that way were fixed.

## What was checked, and how

- Facts the design depends on were checked against primary sources, not memory: the Lichess blitz rule
  (scalachess source), image and package versions (registries), MLflow and DVC behaviour (by running them).
- The Lichess data format and access were checked on my own Mac: streaming the monthly file, reading a sample
  of games, and fetching one game from the export API with clocks. The game used in the parser tests
  (`bcRJNu4b`) comes from those checks.
- No result in this repository comes from the synthetic sandbox data: the test report has only blanks
  to be filled from my own runs, and the user manual has placeholders for my own screenshots.

## Impact

The assistant shaped the design and wrote most of the first version of the code and documents. The
decisions on topic, scope and constraints were mine, and the running, the real results, any later changes,
and the defence of every design choice are mine.

## My own work

- Chose the topic and the constraints (local only, minimal tooling, files at the repo root).
- Ran the feasibility checks on my Mac and reviewed the plan.
- To be completed by me before submission:
  - Ran the full system with Docker on real Lichess data and filled in every `FILL IN` in docs/test_report.md.
  - Added screenshots from that real run to `docs/images` and the user manual.
  - Read and understood every file; changes I made myself: (list them here).

## Responsibility (confirm before submitting)

I am responsible for this submission. I have reviewed the code and documents, I can explain every
part of them, and the results I report come from my own runs.
