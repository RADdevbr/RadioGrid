# RadioGrid

Ferramenta local para composição automática de painéis 2×2 de imagens de RM/TC
exportadas do Horos/OsiriX ou capturadas via screenshot no macOS.

O RadioGrid monitora pastas em background (ou recebe imagens por importação
manual), agrupa as imagens em uma fila e — a cada grupo de 4 — gera
automaticamente um painel composto 2×2 (600×600 px, sem distorção). Um dashboard
web local exibe o status em tempo real e permite configurar as pastas
monitoradas. O nome do paciente é opcional (fila padrão `SEM NOME` quando vazio).

**Privacidade:** tudo é processado localmente. O servidor escuta apenas em
`127.0.0.1` e nenhum dado sai da máquina.

## Requisitos

- Python 3
- **Pillow** para montar o painel 2×2 (`pip3 install pillow`). O lançador
  `RadioGrid.command` instala isso automaticamente.
- Funciona em **macOS** e **Linux**. Recursos nativos do macOS (notificações,
  "Abrir no Finder", seletores nativos) são opcionais — em Linux há stubs.

## Uso

### macOS — duplo-clique (recomendado)

Dê um **duplo-clique** em **`RadioGrid.command`** no Finder. Na primeira vez ele
prepara um ambiente isolado e instala o Pillow automaticamente; depois abre o
dashboard no navegador. (No primeiro uso, se o macOS bloquear, clique com o botão
direito > **Abrir**.)

### Linha de comando

```bash
python3 radiogrid.py               # porta 7842, abre o browser automaticamente
python3 radiogrid.py --port 8080   # porta customizada
python3 radiogrid.py --no-browser  # não abre o browser
```

O dashboard fica em `http://localhost:7842`. Na primeira execução são criados
`config.json` (pastas monitoradas, output, intervalo de polling), `state.json`
(filas e histórico) e a pasta de output.

## Arquitetura

```
RadioGrid.command # lançador de duplo-clique para macOS (prepara venv + Pillow)
radiogrid.py      # servidor HTTP + watcher (polling) + pipeline de painéis
index.html        # dashboard dark-mode clínico (servido pelo radiogrid.py)
macos_bridge.py   # isolamento de plataforma (Pillow + macOS real / stub Linux)
test_radiogrid.py # testes leves (unittest)
config.json       # criado automaticamente
state.json        # criado automaticamente
imports/          # imagens importadas manualmente
output/           # painéis gerados
```

### Camada macOS (`macos_bridge.py`)

Toda a lógica macOS-específica vive em `macos_bridge.py`, que **detecta o sistema
operacional em tempo de execução**:

- **Composição do painel:** feita com **Pillow** (multiplataforma, sem depender
  de `swiftc`/Xcode). Se o Pillow não estiver instalado, há fallback para a
  composição nativa via Swift no macOS e, por fim, um stub.
- **macOS (Darwin):** notificações via `osascript` (som "Glass"), "Abrir no
  Finder" via `open -R`, e seletores nativos de pasta/arquivo via `osascript`.
- **Linux / outros:** stubs seguros — notificações e Finder apenas imprimem no
  terminal; seletores nativos ficam indisponíveis (use o upload do navegador).

Assim o mesmo código roda sem alterações tanto no ambiente de desenvolvimento
Linux quanto no Mac do usuário final.

## Testes

```bash
python3 -m unittest test_radiogrid -v
```

## Como funciona

1. O watcher faz polling (`os.scandir`) nas pastas configuradas a cada N segundos.
2. Cada nova imagem PNG/JPG entra na fila padrão (`SEM NOME`) — sem OCR.
3. Eventos são enviados ao dashboard em tempo real via SSE.
4. Ao atingir 4 imagens: gera o painel 2×2 em `output/` (notifica no macOS + toast
   + som no dashboard) e zera a fila.
5. O histórico de painéis fica disponível no dashboard com thumbnail, "Abrir no
   Finder" e "Copiar para a área de transferência".

## Importação manual

Além do watcher automático, é possível importar imagens manualmente pela seção
**"Importar imagens"** do dashboard. O nome do paciente é **opcional** (vazio =
fila padrão `SEM NOME`):

- **Arraste e solte** imagens na área indicada, ou clique em **"Escolher
  arquivos…"** (funciona em qualquer navegador/SO); ou
- **"📁 Escolher (macOS)"** abre o seletor nativo de arquivos do Finder.

As imagens importadas são copiadas para `imports/` e entram direto na fila.
O painel é gerado automaticamente ao atingir 4 imagens, ou manualmente (com 1–4)
pelo botão **"Gerar agora (n/4)"** de cada fila.
