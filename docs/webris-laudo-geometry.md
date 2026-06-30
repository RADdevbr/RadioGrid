# Geometria da página do laudo WebRIS (Rede D'Or)

Medições extraídas de um **PDF real** exportado pelo WebRIS (laudo de RM), para
fundamentar regras de **quebra de página** (ex.: `laudo-guard`) e o
dimensionamento de imagens coladas no laudo.

> Todos os valores são **geometria pura** (coordenadas em px), sem dados de
> paciente. São estimativas medidas de **um** laudo — confirme em mais amostras
> antes de tratar como exatos (ver "Incertezas" no fim).

## Sistema de coordenadas

- Papel: **A4**, `595.9 × 841.9 pt` = **`795 × 1123 px` a 96 dpi**.
- Fator: `1 pt = 96/72 = 1.3333 px`. Todos os valores abaixo em **px @96dpi**.
- Origem no **canto superior esquerdo**; eixo **y cresce para baixo**.

## Faixas verticais da página (repetidas em toda página)

| Zona            | y (px)        | Conteúdo                                                        |
|-----------------|---------------|----------------------------------------------------------------|
| Cabeçalho       | `0 – ~245`    | Logo (`21–101`), dados do paciente (`125–185`), título do exame (`205–223`), linha-régua + nº da página (`223–239`) |
| **Corpo útil**  | **`~301 – ~900`** | Texto do laudo / imagens / assinatura (fluxo de conteúdo)   |
| Margem inferior | `~900 – ~1045`| Espaço reservado (conteúdo não chega aqui)                     |
| Rodapé fixo     | `~1045 – 1123`| Disclaimer (`1050–1093`), endereço, URL                        |

- **Início do corpo:** `y ≈ 301 px`. É onde a 1ª linha de conteúdo aparece
  (medido: 1º parágrafo do texto **e** a imagem, quando é o 1º item da página,
  começam ambos em `y≈301`). Ou seja, há ~56 px de respiro após a régua do
  cabeçalho (`~245`).
- **Fundo do corpo:** `y ≈ 973 px` (refinado — ver "Como o fundo foi estreitado"
  e incertezas). Conteúdo que ultrapassaria esse limite transborda para a
  página seguinte.
- **Altura do corpo útil:** `~973 − 301 ≈ 672 px` por página.

## Coluna de conteúdo (largura)

- Texto: `x ≈ 80 – 712` (≈ **632 px**).
- Imagem colada (impressa): `x ≈ 80 – 720` (= **640 px**) — preenche a coluna.
- Rodapé (mais largo): `x ≈ 36 – 759`.
- **Regra de escala de imagem:** o WebRIS reduz a imagem para a **largura da
  coluna (~640 px no PDF)** mantendo a proporção. Se a imagem for ≤ 640 px de
  largura, é exibida no tamanho natural; se for maior, é **reduzida**.
  *Atenção — comportamento instável:* o fator de redução **variou entre
  instâncias/inserções**: um mesmo canvas 1280 px foi exibido ora a **640 px**,
  ora a **~686 px**, ora a **~800 px**. Acima de ~640–712 px a imagem
  **distorce e sai da área de impressão**. **Conclusão prática:** exporte a
  imagem já em **640 px de largura nativa** — assim o WebRIS não reescala e o
  resultado é previsível (é o que o RadioGrid faz por padrão).

## Bloco de assinatura

- Altura: **`~135–140 px`** (medido `y≈288–428` quando sozinho na página:
  "Laudo e / Revisão por:" + assinatura + nome + CRM/RQE).
- É anexado **automaticamente pelo WebRIS, fora do editor**, ao final do laudo.
- Comporta-se como um bloco no fim do fluxo de conteúdo (vem depois do texto e
  de qualquer imagem).

## Regra de quebra de página (o ponto central p/ o `laudo-guard`)

O fluxo de conteúdo é: **texto do laudo → imagem(ns) → bloco de assinatura**.
A **imagem é um bloco indivisível**: ou cabe inteira no espaço restante da
página atual, ou desce **inteira** para a próxima.

Para a **imagem e a assinatura ficarem na mesma página**:

```
y_topo_conteudo(≈301) + altura_renderizada_imagem + folga(≈15) + assinatura(≈140) ≤ fundo_corpo(≈973)
=> altura_renderizada_imagem ≤ ~517 px   (na prática use ≤ 500 px de margem)
```

Quando a imagem desce sozinha para uma página nova, ela começa em `y≈301`;
mesmo assim a assinatura só permanece junto se a desigualdade acima for
satisfeita.

### Como o fundo do corpo (`~973`) foi estreitado

Observações reais cercam o limite por cima e por baixo:

- Imagem **540 px** de altura → **NÃO** coube → `301 + 540 + 140 = 981 > fundo`
  → `fundo_corpo < 981`.
- Imagem **525 px** de altura → **coube** → `301 + 525 + 140 = 966 ≤ fundo`
  → `fundo_corpo ≥ 966`.
- Imagem **560 px** → não coube (margem maior).

Logo `fundo_corpo ∈ [966, 981]`, ~**973** (bem mais apertado que a estimativa
inicial de 861–996).

### Caso seguro (alvo do RadioGrid)

- Imagem **`640 × 500`** em largura **nativa** (WebRIS não reescala).
- `y[301 → 801]` + folga + assinatura(`~140`) ≈ `y≈956` ≤ `~973` → **cabe**, com
  ~17 px de margem, e sem risco de distorção/overflow de largura.

## Resumo de constantes

| Constante                         | Valor px @96dpi | Notas                              |
|-----------------------------------|-----------------|------------------------------------|
| Página (A4)                       | `795 × 1123`    | `1 pt = 1.3333 px`                 |
| Topo do corpo                     | `~301`          | 1ª linha de conteúdo               |
| Fundo do corpo                    | `~973`          | estreitado p/ [966, 981]           |
| Altura do corpo                   | `~672`          | `973 − 301`                        |
| Largura da coluna (PDF)           | `~640`          | **exporte nativo a 640** (sem reescala) |
| Altura do bloco de assinatura     | `~135–140`      | anexado fora do editor             |
| Altura máx. de imagem (c/ assin.) | `~517` (usar ≤ `500`) | p/ imagem + assinatura juntas |
| Linha de texto (aprox.)           | `~18`           | desconte `nº_linhas × 18` do teto  |

## Incertezas (validar com mais laudos)

1. **`fundo_corpo` (≈973):** estreitado para **[966, 981]** por testes de
   altura (525 coube, 540 não). Ainda assim depende da assinatura (~140) e do
   topo (~301) — confirme em laudos com mais texto chegando perto do rodapé.
2. **`topo_conteudo` (≈301):** pode variar se o cabeçalho tiver mais/menos
   linhas de dados do paciente, ou conforme o tipo de exame/título.
3. **Altura da assinatura (≈140):** pode mudar com nome/CRM/RQE mais longos ou
   com selo/carimbo adicional.
4. **Largura da coluna / reescala instável:** PDF mediu 640; o editor já exibiu
   640, ~686 e ~800 px para o mesmo canvas. Acima de ~640–712 px distorce e sai
   da impressão. **Exporte nativo a 640 px** para não depender disso.
5. Tudo isto vem de **um** laudo. Reconfirme em laudos com diferentes
   comprimentos de texto e nº de imagens.
