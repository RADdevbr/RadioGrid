# RadioGrid

Ferramenta local para composição automática de painéis 2×2 de imagens de RM/TC
exportadas do Horos/OsiriX ou capturadas via screenshot no macOS.

O RadioGrid monitora pastas em background (ou recebe imagens por importação
manual), agrupa as imagens em uma fila e — a cada grupo de 4 — gera
automaticamente um painel composto 2×2 (640×500 px, formato WebRIS-safe, sem
distorção). Um dashboard
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

## Versão web (sem instalar nada)

Para o fluxo **só arrastar/importar → montar o 2×2 → exportar** (sem
monitoramento de pastas e sem armazenar imagens), existe uma versão 100%
client-side em [`web/index.html`](web/index.html):

- Roda inteiramente **no navegador** — a composição 2×2 usa a **Canvas API**
  (mesma regra do app nativo: canvas 640×500, tiles de 320×250, fundo preto,
  cada imagem encaixada sem distorção e centralizada).
- **Privacidade:** as imagens **nunca saem do dispositivo** — não há servidor,
  banco de dados nem upload. Nada é persistido (fecha a aba, some tudo).
- Funciona em **macOS, Windows, Linux e celular**, sem Python e sem `pip`.

Uso: abra `web/index.html` com duplo-clique, ou publique no **GitHub Pages**.
O workflow [`.github/workflows/pages.yml`](.github/workflows/pages.yml) publica
a pasta `web/` automaticamente — basta habilitar uma vez em
**Settings → Pages → Source = "GitHub Actions"**.

## Formato WebRIS-safe

Os painéis são gerados num **canvas 640×500 px** (largura nativa da coluna)
pensado para ser **colado no editor de laudos do WebRIS** (Rede D'Or). Como já
sai na largura da coluna (~640 px), o WebRIS **não reescala** a imagem — sem
distorção e sem estourar a área de impressão — e ela é exibida como **640×500**.

Isso importa porque o WebRIS anexa um bloco de assinatura (~140 px) **fora** do
editor, e a imagem é um bloco indivisível: ela e a assinatura precisam caber
**juntas na mesma página**, senão a assinatura é empurrada sozinha para uma
página nova. Medindo o PDF real do laudo, o corpo útil vai de y≈301 px (1ª linha
de conteúdo, após o cabeçalho) a y≈900 px. Histórico das medições: um painel 8:7
renderizava 640×**560** (alto demais, empurrava a assinatura); um teste a 525 px
**coube**. Por isso a altura nativa **500 px** deixa margem segura.

> Por que 640 px nativo em vez de exportar a 2× (1280)? Porque o
> redimensionamento do WebRIS variou entre instâncias — em alguns o 1280 era
> reduzido a 640, em outros exibido a ~800 px (distorcendo e saindo da área de
> impressão). Mandando já em 640 px o WebRIS não reescala, e o resultado é
> previsível.

### Toggle de formato (versão web)

No topo do dashboard web há um seletor **"Formato exportado"** (botões menores
que Painel/Comparativo), válido tanto para o Painel quanto para o Comparativo:

- **WebRIS 640×500** (padrão) — largura nativa: o WebRIS não reescala, sem
  distorção, e a assinatura fica na mesma página. Recomendado.
- **Nítido 1280×840** — exporta a 2× (mais nítido). O WebRIS reduz para a largura
  da coluna; em instâncias que reduzem para 640 px, vira 640×420. Use **se o seu
  WebRIS reduz para 640**. Passa pela checagem `fitForWebRIS`.
- **Quadrado 600×600** — formato clássico (renderiza 600×600). Mais alto: **pode**
  empurrar a assinatura para outra página. Útil fora do WebRIS. Não é clampado.

A imagem única em proporção livre é sempre encaixada na caixa do formato
(`PANEL_W × PANEL_H`), então mesmo uma imagem "em pé" não estoura a altura.

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

## Reordenar e reeditar painéis

- **Reordenar na fila:** arraste as miniaturas dentro de uma fila ativa para
  definir a ordem do 2×2. O número em cada miniatura indica a posição
  (1 = cima-esquerda, 2 = cima-direita, 3 = baixo-esquerda, 4 = baixo-direita).
- **Reabrir um painel:** o botão **"Editar"** de um painel já gerado traz as
  imagens-fonte de volta para a fila. Assim você pode reordenar e gerar um
  **novo output** (`..._panel_2.png`, `..._panel_3.png`, …) sem perder o anterior.
  (Funciona enquanto as imagens-fonte ainda existirem em disco.)
