#!/usr/bin/env bash
# Bootstrap an Ubuntu host with dependencies for the Crypto Premarket Alert Bot.

set -euo pipefail

BOT_USER=${BOT_USER:-premarket}
APP_DIR=${APP_DIR:-/opt/crypto-premarket}
PYTHON_VERSION=${PYTHON_VERSION:-3.11}
INSTALL_PLAYWRIGHT=${INSTALL_PLAYWRIGHT:-1}

if [[ $(id -u) -ne 0 ]]; then
  echo "This script must be run as root (use sudo)" >&2
  exit 1
fi

apt-get update

LIBASOUND_PRIMARY=libasound2
LIBASOUND_FALLBACK=libasound2t64

resolve_libasound_package() {
  local package=$1
  local candidate

  if ! apt-cache show "${package}" >/dev/null 2>&1; then
    return 1
  fi

  candidate=$(apt-cache policy "${package}" | awk '/Candidate:/ {print $2; exit}')
  if [[ -z "${candidate}" || "${candidate}" == "(none)" ]]; then
    return 1
  fi

  return 0
}

if ! resolve_libasound_package "${LIBASOUND_PRIMARY}"; then
  if resolve_libasound_package "${LIBASOUND_FALLBACK}"; then
    LIBASOUND_PRIMARY=${LIBASOUND_FALLBACK}
    LIBASOUND_FALLBACK=libasound2
  else
    echo "Neither ${LIBASOUND_PRIMARY} nor ${LIBASOUND_FALLBACK} have installable candidates." >&2
    exit 1
  fi
fi

COMMON_PACKAGES=(
  "python${PYTHON_VERSION}"
  "python${PYTHON_VERSION}-venv"
  python3-pip
  git
  curl
  build-essential
  rsync
  libnss3
  libatk1.0-0
  libatk-bridge2.0-0
  libcups2
  libdrm2
  libxkbcommon0
  libxcomposite1
  libxdamage1
  libxrandr2
  libgbm1
)

apt-get install -y "${COMMON_PACKAGES[@]}"

if ! apt-get install -y "${LIBASOUND_PRIMARY}"; then
  if [[ "${LIBASOUND_PRIMARY}" != "${LIBASOUND_FALLBACK}" ]]; then
    echo "Failed to install ${LIBASOUND_PRIMARY}; retrying with ${LIBASOUND_FALLBACK}..." >&2
    if ! apt-get install -y "${LIBASOUND_FALLBACK}"; then
      echo "Failed to install either ${LIBASOUND_PRIMARY} or ${LIBASOUND_FALLBACK}." >&2
      exit 1
    fi
  else
    echo "Failed to install ${LIBASOUND_PRIMARY}." >&2
    exit 1
  fi
fi

if ! id "${BOT_USER}" &>/dev/null; then
  useradd --system --create-home --shell /bin/bash "${BOT_USER}"
fi

mkdir -p "${APP_DIR}"
chown "${BOT_USER}:${BOT_USER}" "${APP_DIR}"

# Copy repository contents into the application directory.
rsync -a --delete --exclude '.git' ./ "${APP_DIR}/"

cd "${APP_DIR}"
python${PYTHON_VERSION} -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

if [[ ${INSTALL_PLAYWRIGHT} -eq 1 ]]; then
  playwright install chromium || echo "Playwright install failed (optional dependency)"
fi

install -d -m 750 logs
chown "${BOT_USER}:${BOT_USER}" logs

cat <<MSG
Setup complete.
Repository copied to ${APP_DIR} and virtualenv initialised at ${APP_DIR}/.venv.
Update the .env and config.yaml files, then enable the systemd units in deploy/systemd/.
MSG
