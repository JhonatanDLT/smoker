.PHONY: install test test-integration demo lint clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest

test-integration:
	SMOKER_TEST_DSN=$${SMOKER_TEST_DSN:-postgresql:///smoker_demo} .venv/bin/pytest -m integration

demo:
	./scripts/demo.sh

realistic:
	./scripts/run_realistic.sh

fixtures:
	./scripts/run_fixtures.sh

lint:
	.venv/bin/ruff check src tests scripts

clean:
	rm -rf proof .pytest_cache .ruff_cache build dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
