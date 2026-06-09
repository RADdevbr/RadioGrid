#!/bin/bash
# RadioGrid — inicializador de duplo-clique para macOS.
#
# Dê um duplo-clique neste arquivo no Finder para abrir o RadioGrid.
# Na primeira vez ele prepara um ambiente isolado e instala o Pillow
# (usado para montar o painel 2×2) — depois é só esperar o navegador abrir.
#
# Observação: por ser um arquivo baixado, no primeiro uso o macOS pode pedir
# para você clicar com o botão direito > "Abrir" e confirmar.

set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  RadioGrid"
echo "============================================"

# Cria um ambiente Python isolado na primeira execução.
if [ ! -d ".venv" ]; then
  echo "Primeira execução: preparando o ambiente (pode levar ~1 minuto)..."
  python3 -m venv .venv
fi

# Ativa o ambiente isolado.
# shellcheck disable=SC1091
source .venv/bin/activate

# Garante o Pillow instalado (montagem do painel, sem depender do swiftc/Xcode).
if ! python -c "import PIL" >/dev/null 2>&1; then
  echo "Instalando dependências (Pillow)..."
  pip install --quiet --upgrade pip
  pip install --quiet pillow
fi

echo "Abrindo o RadioGrid no navegador..."
python radiogrid.py "$@"
