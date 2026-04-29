#!/bin/bash
# Atualização manual do relatório de leads
# Uso: ./atualizar-leads.sh (rode a partir da pasta omni-apresentacoes)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OMNI Brasil — Atualização manual de leads"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

export PIPEDRIVE_API_KEY="fd0d8ec92950c2fc3053b6d126ce54a4a0e03d37"
export CHATWOOT_TOKEN="oLg7zw97T5TcomRRmfYrwecy"

echo "▶ Gerando relatório atualizado..."
python3 scripts/update-leads-report.py

echo ""
echo "▶ Subindo para o GitHub..."
git add leads-responderam-tentativa-conectar.html
git diff --staged --quiet && echo "  Sem mudanças detectadas." && exit 0

HORA=$(TZ="America/Sao_Paulo" date +'%d/%m/%Y %H:%M')
git commit -m "chore(manual): atualiza relatório de leads — $HORA BRT"
git pull origin main --no-rebase -X ours -q
git push origin main

echo ""
echo "✅ Relatório atualizado com sucesso!"
echo "   https://gringoles777.github.io/omni-apresentacoes/leads-responderam-tentativa-conectar.html"
echo ""
