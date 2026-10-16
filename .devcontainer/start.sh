#!/usr/bin/env bash
# Roda SEMPRE que o Codespace liga: acerta o endereço público e liga o site em segundo plano.
set -euo pipefail
cd "$(dirname "$0")/.."

# Endereço https que o Codespaces dá para a porta 5000
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  URL="https://${CODESPACE_NAME}-5000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  if grep -q '^PUBLIC_BASE_URL=' .env; then
    sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${URL}|" .env
  else
    echo "PUBLIC_BASE_URL=${URL}" >> .env
  fi
fi

[ "${1:-}" = "--only-env" ] && exit 0

# Liga o site (se ainda não estiver ligado)
if ! curl -fs -o /dev/null http://127.0.0.1:5000/saude; then
  setsid nohup python run.py > /tmp/numelhor.log 2>&1 < /dev/null &
  for _ in $(seq 1 30); do
    curl -fs -o /dev/null http://127.0.0.1:5000/saude && break
    sleep 1
  done
fi

if curl -fs -o /dev/null http://127.0.0.1:5000/saude; then
  echo "Site NuMelhor ligado: $(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)/"
else
  echo "O site não ligou. Veja o erro com: tail -50 /tmp/numelhor.log"
  exit 1
fi
