.PHONY: up down run test clean

up:
	docker compose up -d

down:
	docker compose down

# One-command pilot run: brings the DB up, then runs the full orchestration
# pipeline (schema + fixtures + promote + refresh views + export dossiers).
run: up
	python -m iris_pilot.cli run --setup

test:
	pytest

clean:
	rm -f exports/*.json
