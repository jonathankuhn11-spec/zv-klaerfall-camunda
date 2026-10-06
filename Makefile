# Komfortziele – alles läuft auch ohne make (siehe README).
.PHONY: install test lint lint-models models samples diagrams demo up down deploy worker ingest check clean

install:            ## Python-Paket inkl. Dev-Tools und Node-Lint-Tools installieren
	pip install -e ".[dev]"
	npm install

test:               ## Unit- und Pipeline-Tests
	pytest

lint:               ## Ruff + Mypy (strict)
	ruff check src tests tools
	ruff format --check src tests tools
	mypy

lint-models:        ## BPMN gegen Camunda 8.9 prüfen, DMN prüfen
	npm run lint

models:             ## BPMN/DMN/Formular neu generieren
	python tools/generate_models.py

samples:            ## Beispiel-pain.001-Dateien neu generieren
	python tools/generate_samples.py

diagrams:           ## Diagramme nach docs/img rendern (benötigt playwright + chromium)
	python tools/render_diagrams.py

demo:               ## Kompletter Durchlauf ohne Camunda
	zv demo --offline

up:                 ## Camunda 8 lokal starten (Operate/Tasklist: http://localhost:8080, demo/demo)
	docker compose -f docker/docker-compose.yaml up -d

down:               ## Camunda stoppen und Daten löschen
	docker compose -f docker/docker-compose.yaml down -v

deploy:             ## Modelle in den Cluster deployen
	zv deploy

ingest:             ## Beispieldateien als Prozessinstanzen einreichen
	zv ingest

worker:             ## Job-Worker starten (Strg+C beendet)
	zv worker

check: lint lint-models test   ## Alles, was die CI prüft (ohne Integration)

clean:
	rm -rf .data .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
