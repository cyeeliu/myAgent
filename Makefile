# Makefile — myAgent evaluation targets
#
# Usage:
#   make swe-bench-eval          # Run SWE-bench-lite (all tasks)
#   make swe-bench-eval-limited  # Run first 10 tasks
#   make swe-bench-custom        # Run custom tasks from evals/tasks/
#   make swe-bench-report        # Show latest report

PYTHON ?= python
DATASET ?= swe-bench-lite
LIMIT ?= 10

.PHONY: swe-bench-eval swe-bench-eval-limited swe-bench-custom swe-bench-report test

swe-bench-eval:
	$(PYTHON) -m evals.runner --dataset $(DATASET)

swe-bench-eval-limited:
	$(PYTHON) -m evals.runner --dataset $(DATASET) --limit $(LIMIT)

swe-bench-custom:
	$(PYTHON) -m evals.runner --custom evals/tasks/example_tasks.json

swe-bench-report:
	@cat evals/results/$(DATASET).md

test:
	MODEL_ID=test-model OPENAI_API_KEY=dummy $(PYTHON) -m pytest tests/ -q
