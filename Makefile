.PHONY: up down logs test build

up: .env
	docker compose up --build -d
	@echo "Frontend: http://localhost:3000"
	@echo "Backend:  http://localhost:8000"

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

test:
	cd backend && python -m pytest -q

.env:
	@echo "Missing .env - run: cp .env.example .env  and add your API keys" && exit 1
