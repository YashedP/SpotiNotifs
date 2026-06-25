SHELL := /bin/bash

SERVICE_NAME := spotinotifs.service
TIMER_NAME := spotinotifs-notifier.timer
INSTALL_SCRIPT := deploy/install-systemd.sh
MIGRATE_SCRIPT := deploy/migrate-data.sh

.PHONY: help setup install run start stop restart status logs uninstall

help:
	@echo "Available targets:"
	@echo "  make setup      Validate configuration and build the container"
	@echo "  make install    Install services, migrate legacy startup, and enable boot startup"
	@echo "  make run        Run the web server in the foreground"
	@echo "  make start      Start the web service and notifier timer"
	@echo "  make stop       Stop the web service and notifier timer"
	@echo "  make restart    Restart the web service and notifier timer"
	@echo "  make status     Show service, timer, and Compose status"
	@echo "  make logs       Follow web service logs"
	@echo "  make uninstall  Remove systemd units without deleting configuration or data"

setup:
	@command -v docker >/dev/null || { echo "docker is required"; exit 1; }
	@test -f .env || { echo "Missing .env"; exit 1; }
	./$(MIGRATE_SCRIPT)
	docker compose build
	docker compose run --rm --no-deps server python -c \
		"from pathlib import Path; [compile(path.read_text(), str(path), 'exec') for path in map(Path, ('OAuth2.py', 'add_user.py', 'spotify.py', 'sql.py'))]"

install:
	-@sudo systemctl stop "$(TIMER_NAME)" "$(SERVICE_NAME)"
	@$(MAKE) setup
	./$(INSTALL_SCRIPT)

run:
	docker compose up --build server

start:
	sudo systemctl start "$(SERVICE_NAME)" "$(TIMER_NAME)"

stop:
	sudo systemctl stop "$(TIMER_NAME)" "$(SERVICE_NAME)"

restart:
	sudo systemctl restart "$(SERVICE_NAME)" "$(TIMER_NAME)"

status:
	@sudo systemctl status "$(SERVICE_NAME)" "$(TIMER_NAME)" --no-pager
	@sudo systemctl list-timers "$(TIMER_NAME)" --no-pager
	@docker compose ps

logs:
	sudo journalctl -u "$(SERVICE_NAME)" -f

uninstall:
	-@sudo systemctl disable --now "$(TIMER_NAME)" "$(SERVICE_NAME)"
	@sudo rm -f \
		"/etc/systemd/system/$(SERVICE_NAME)" \
		"/etc/systemd/system/spotinotifs-notifier.service" \
		"/etc/systemd/system/$(TIMER_NAME)"
	@sudo systemctl daemon-reload
