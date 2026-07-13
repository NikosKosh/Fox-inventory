#!/usr/bin/env bash
set -Eeuo pipefail

printf '\n\033[1;36m========== DOCKER INSTALLATION ==========\033[0m\n'
sudo apt-get update
sudo apt-get install -y ca-certificates curl python3 openssl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
printf '\n\033[1;32m========== DOCKER INSTALLED ==========\033[0m\n'
printf 'A new login session is required for docker group membership to take effect.\n'
