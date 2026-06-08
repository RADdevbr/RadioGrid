# RadioGrid

Ferramenta local para composição automática de painéis 2×2 de imagens de RM/TC
exportadas do Horos/OsiriX ou capturadas via screenshot no macOS.

O RadioGrid monitora pastas em background, lê o nome do paciente via OCR nativo
do macOS (Vision framework), agrupa imagens por paciente e — a cada grupo de 4 —
gera automaticamente um painel composto 2×2 (máx. 600×600 px, sem distorção). Um
dashboard web local exibe o status em tempo real, permite configurar as pastas
monitoradas e mostra alertas de inconsistência de nome.

**Privacidade:** tudo é processado localmente. O servidor escuta apenas em
`127.0.0.1` e nenhum dado sai da máquina.

## Requisitos

- Python 3 (apenas a biblioteca padrão — sem dependências externas)
- **macOS** para os recursos nativos (OCR via Vision, composição via NSImage,
  notificações, "Abrir no Finder"). Requer as command line tools com `swiftc`.
- Em **Linux** o app roda em modo *stub* para desenvolvimento/teste (ver abaixo).

## Uso

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
radiogrid.py      # servidor HTTP + watcher (polling) + pipeline de painéis
index.html        # dashboard dark-mode clínico (servido pelo radiogrid.py)
macos_bridge.py   # isolamento de plataforma (macOS real / stub Linux)
test_radiogrid.py # testes leves (unittest)
config.json       # criado automaticamente
state.json        # criado automaticamente
output/           # painéis gerados
```

### Camada macOS (`macos_bridge.py`)

Toda a lógica macOS-específica vive em `macos_bridge.py`, que **detecta o sistema
operacional em tempo de execução**:

- **macOS (Darwin):** OCR via Swift + Vision framework (compilado uma vez e
  cacheado), composição 2×2 via NSImage, notificações via `osascript` (som
  "Glass") e "Abrir no Finder" via `open -R`.
- **Linux / outros:** stubs seguros — OCR deriva o nome do paciente do nome do
  arquivo (`JOAO_SILVA_*.png` → "JOAO SILVA"), a composição copia a primeira
  imagem como placeholder, notificações e Finder apenas imprimem no terminal.

Assim o mesmo código roda sem alterações tanto no ambiente de desenvolvimento
Linux quanto no Mac do usuário final.

## Testes

```bash
python3 -m unittest test_radiogrid -v
```

## Como funciona

1. O watcher faz polling (`os.scandir`) nas pastas configuradas a cada N segundos.
2. Cada nova imagem PNG/JPG passa pelo OCR → nome normalizado → fila do paciente.
3. Eventos são enviados ao dashboard em tempo real via SSE.
4. Ao atingir 4 imagens: verifica consistência de nome, notifica (macOS + toast +
   som no dashboard), gera o painel 2×2 em `output/` e zera a fila.
5. O histórico de painéis fica disponível no dashboard com thumbnail, "Abrir no
   Finder" e "Copiar para a área de transferência".
