#!/usr/bin/env bash
# Shared helpers for install and auto-update scripts.

is_raspberry_pi() {
  if [[ ! -r /proc/device-tree/model ]]; then
    return 1
  fi
  local model
  model="$(tr -d '\0' < /proc/device-tree/model)"
  [[ "${model}" == *[Rr]aspberry* ]]
}

upgrade_venv_package() {
  local python="$1"
  local extra="${2:-gpio}"
  "${python}" -m pip install -q --upgrade pip
  "${python}" -m pip install -q --upgrade --no-cache-dir -e ".[${extra}]"
}
