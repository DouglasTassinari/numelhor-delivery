#!/usr/bin/env bash
# Roda UMA vez, quando o Codespace é criado: instala as dependências, cria o .env e o usuário do painel.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m pip install --quiet --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
  cat > .env <<EOF
SECRET_KEY=${SECRET}
APP_ENV=production
TRUST_PROXY=1
PORT=5000
PUBLIC_BASE_URL=http://127.0.0.1:5000
EOF
  chmod 600 .env
fi

bash .devcontainer/start.sh --only-env

# Usuário do painel com senha temporária (troca obrigatória no 1º acesso)
if [ ! -f LINK-E-SENHA.txt ]; then
  OUT=$(python -m flask --app run criar-admin dono)
  SENHA=$(printf '%s\n' "$OUT" | sed -n 's/^Senha temporária: //p')
  URL=$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)
  cat > LINK-E-SENHA.txt <<EOF
NUMELHOR — SITE DE TESTE NO GITHUB CODESPACES
=============================================

Link do site (para você e para o cliente):
  ${URL}/

Painel do dono:
  ${URL}/admin
  Usuário: dono
  Senha temporária: ${SENHA}
  (no primeiro acesso o painel pede para criar a sua senha definitiva)

IMPORTANTE para o cliente conseguir abrir o link:
  Aba "PORTAS" (PORTS) aqui embaixo -> clique com o botão direito na porta 5000
  -> "Visibilidade da porta" (Port Visibility) -> "Público" (Public).

O site liga sozinho sempre que este Codespace é ligado.
Este arquivo NÃO vai para o GitHub.
EOF
  chmod 600 LINK-E-SENHA.txt
fi
