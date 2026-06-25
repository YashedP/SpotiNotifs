#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_source="${project_dir}/deploy/systemd"
legacy_unit="${HOME}/.config/systemd/user/spotinotifs.service"
legacy_was_active=false

render_unit() {
    local source="$1"
    local destination="$2"
    local temporary

    temporary="$(mktemp)"
    sed "s|@PROJECT_DIR@|${project_dir}|g" "${source}" > "${temporary}"
    sudo install -Dm644 "${temporary}" "${destination}"
    rm -f "${temporary}"
}

if systemctl --user is-active --quiet spotinotifs.service; then
    legacy_was_active=true
    systemctl --user stop spotinotifs.service
fi

render_unit \
    "${unit_source}/spotinotifs.service" \
    "/etc/systemd/system/spotinotifs.service"
render_unit \
    "${unit_source}/spotinotifs-notifier.service" \
    "/etc/systemd/system/spotinotifs-notifier.service"
render_unit \
    "${unit_source}/spotinotifs-notifier.timer" \
    "/etc/systemd/system/spotinotifs-notifier.timer"

sudo systemctl daemon-reload
sudo systemctl enable docker.service
sudo systemctl enable spotinotifs.service spotinotifs-notifier.timer

if ! sudo systemctl restart spotinotifs.service; then
    if [[ "${legacy_was_active}" == true ]]; then
        systemctl --user start spotinotifs.service
    fi
    exit 1
fi

# Prevent Persistent=true from treating the installation itself as missed time.
sudo install -Dm644 /dev/null \
    /var/lib/systemd/timers/stamp-spotinotifs-notifier.timer
sudo systemctl start spotinotifs-notifier.timer

systemctl --user disable spotinotifs.service 2>/dev/null || true
rm -f "${legacy_unit}"
systemctl --user daemon-reload

if crontab -l >/dev/null 2>&1; then
    temporary_crontab="$(mktemp)"
    crontab -l | grep -vF "${project_dir}/run_spotify_notifier.sh" > "${temporary_crontab}" || true
    crontab "${temporary_crontab}"
    rm -f "${temporary_crontab}"
fi

rm -f \
    "${project_dir}/run_server.sh" \
    "${project_dir}/run_spotify_notifier.sh"
