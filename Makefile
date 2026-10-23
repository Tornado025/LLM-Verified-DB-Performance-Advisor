.PHONY: up down seed test test-db lint api web experiments clean
up:
	docker compose up -d db
down:
	docker compose down
seed:
	cd backend && python scripts/seed.py --scale 1
test:
	cd backend && pytest -m "not db and not llm"
test-db:
	cd backend && pytest -m db
lint:
	cd backend && ruff check . && ruff format --check . && mypy app
api:
	cd backend && uvicorn app.main:app --reload --port 8000
web:
	cd frontend && npm run dev
experiments:
	cd backend && python scripts/run_experiments.py --all
clean:
	docker compose down -v
