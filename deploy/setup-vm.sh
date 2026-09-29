#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 VM (2 vCPU / 4 GB is enough).
#   curl -fsSL https://raw.githubusercontent.com/fairuz-anadi/bup_hampton/main/deploy/setup-vm.sh | bash
# Then edit ~/bup_hampton/.env and run deploy/update.sh.
set -euo pipefail

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Firewall: SSH + HTTP(S) only. Everything else stays inside the compose network.
if command -v ufw >/dev/null; then
  sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable
fi

cd "$HOME"
[ -d bup_hampton ] || git clone https://github.com/fairuz-anadi/bup_hampton.git
cd bup_hampton

if [ ! -f .env ]; then
  ip=$(curl -fsS https://api.ipify.org || echo "127.0.0.1")
  cat > .env <<EOF
OPERATOR_KEY=$(openssl rand -base64 24 | tr -d '/+=')
POSTGRES_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')
PUBLIC_HOST=${ip}.sslip.io
SIMULATOR_START_MODE=paused
EOF
  chmod 600 .env
  echo "Wrote .env with random secrets. PUBLIC_HOST=${ip}.sslip.io (change it if you have a domain)."
fi

echo "Log out and back in (docker group), then run:  cd ~/bup_hampton && ./deploy/update.sh"
