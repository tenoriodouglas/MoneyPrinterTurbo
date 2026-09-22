# Trilhas de fundo (BGM) — pastas de humor

> Este é o único arquivo em português do diretório. Ele é para o dono do repo,
> não para o código.

## 1. Por que estas pastas existem

O acervo antigo deste diretório são 29 arquivos, `output000.mp3` até
`output029.mp3` (o `output026.mp3` não existe — a sequência tem buracos). Eles
foram medidos, um por um:

- **Todos têm exatamente 180 segundos de duração.** Os 29.
- **Todos ficam entre -19,7 e -23,9 LUFS**, uma faixa estreitíssima de volume
  percebido.

Isso não é um acervo de 29 músicas. É **uma ideia só, renderizada 29 vezes pelo
mesmo gerador**. Duração idêntica ao segundo e loudness praticamente igual não
acontecem por acaso entre faixas de origens diferentes.

A consequência prática é a que importa: hoje o pipeline sorteia uma faixa ao
acaso (`bgm_type = "random"`, definido em `growth/plan.py` para todos os packs),
e o espectador tem a impressão de que **todo vídeo do canal usa a mesma música**.
O sorteio é aleatório no código e não soa aleatório no ouvido, porque o que ele
sorteia são 29 variações da mesma textura. Trocar 29 por 200 arquivos do mesmo
gerador não resolveria nada; o que resolve é ter texturas *diferentes* e
escolhidas por tema.

Daí as pastas de humor: em vez de um balde único sorteado ao acaso, cada pack
aponta para um humor, e o sorteio acontece dentro daquele humor.

### Como o motor enxerga estas pastas (verificado, não suposto)

- `app/services/bgm.py`, função `_list_bgm_files()`, usa `os.listdir()` — uma
  varredura **plana, sem recursão**. Subpastas não entram no acervo padrão.
  Verificado na prática: com um `.mp3` real dentro de `calm/`, o
  `list_bgm_files()` continuou devolvendo **29** e nenhum caminho com `calm`.
- `resolve_bgm_file("calm/faixa.mp3")` **resolve normalmente**, porque
  `file_security.resolve_path_within_directory()` junta o caminho relativo ao
  diretório base e só verifica, via `commonpath`, se o resultado continua dentro
  dele. Uma subpasta continua dentro. Verificado na prática.
- `get_bgm_file()` (em `app/services/video.py`) testa `bgm_file` **antes** do
  sorteio: se vier um caminho, ele é usado; se vier vazio, cai no sorteio geral.

Ou seja: as subpastas são invisíveis para o acervo padrão e endereçáveis por
caminho. É exatamente o comportamento que a feature precisa, e **nenhum arquivo
colocado aqui dentro muda o sorteio antigo**.

## 2. As pastas e quem usa cada uma

Quatro humores, escolhidos lendo os 18 packs de `niches/` e agrupando por
necessidade real. São 9 temas × 2 idiomas (`en` e `pt-BR`); o par de idiomas de
um mesmo tema sempre usa o mesmo humor, porque o que muda é a língua da narração,
não o clima do assunto.

| Pasta | Clima | Packs |
|---|---|---|
| `calm/` | Discreto, mínimo, quase ausente. Um colchão que não chama atenção. | `health-longevity`, `b2b-software`, `real-estate` (+ `-pt`) |
| `upbeat/` | Movimento leve para frente, otimista, sem euforia nem "hype" de anúncio. | `ai-tools`, `digital-marketing`, `personal-finance` (+ `-pt`) |
| `dark/` | Tensão, drone grave, ambiente de horror. | `ghost-stories` (+ `-pt`) |
| `mystery/` | Suspense não resolvido, clima documental de arquivo. Sem susto. | `ufo-sightings` (+ `-pt`) |

Por que estes quatro, e não mais:

- **`calm`** — `health-longevity` (mecanismo, corpo, adesão), `b2b-software`
  (auditoria de custo, processo) e `real-estate` (custo real, ponto de
  equilíbrio) são packs em que a narração carrega tudo e há número para
  acompanhar. Música com personalidade só atrapalha.
- **`upbeat`** — `ai-tools`, `digital-marketing` e `personal-finance` vendem
  ganho: tempo economizado, oferta reescrita, juros compostos. Pedem energia
  contida, não solenidade.
- **`dark`** — `ghost-stories` é o único caso em que a trilha faz trabalho
  dramático de verdade, e o pack admite isso: é o único com `bgm_volume = 0.2`,
  acima de todos os outros. Um comentário no próprio pack diz que "the ambient
  bed does real work in this genre".
- **`mystery`** — `ufo-sightings` parece vizinho de `ghost-stories`, mas não é.
  Os ângulos do pack são `official-record`, `radar-and-instrument`,
  `witness-testimony`: é relatório, não assombração. Trilha de horror ali soa
  sensacionalista e desmente o tom sóbrio que o pack persegue.

**`free-theme` e `free-theme-pt` ficam de fora de propósito.** O tema é definido
pelo operador na hora de rodar, então o humor certo também só é conhecido na
hora. Sem `mood`, esses dois caem no acervo geral — e, quando o operador souber
o tema do dia, pode apontar o humor manualmente.

Nenhuma pasta foi criada para um pack só, e nenhuma pasta foi criada sem pack que
a use.

### O campo no pack

A seção `[music]` já existe em `growth/niche.py` (dataclass `MusicStyle`,
validada por `_build_music_style`). Para usar uma pasta de humor, basta:

```toml
[music]
mood = "calm"
```

Os três campos aceitos são `mood`, `prompt` e `provider`. As regras que o
parser aplica hoje:

- **`mood`** tem que ser **um único nome de pasta** sob `resource/songs/`, só
  com letras, dígitos, `.`, `_` e `-`. Nada de barra, `..` ou nome começando
  com ponto — o valor vira segmento de caminho, então é validado como tal. Os
  quatro nomes deste diretório passam.
- **`mood` apontando para pasta vazia ou inexistente não quebra o pack**: o
  parser só emite um `WARNING` dizendo que os renders caem nas músicas
  embutidas até alguém adicionar faixas. É o que garante a seção 6.
- **`provider`** só aceita `"sonilo"` ou `"elevenlabs"`, normalizado para
  minúsculas.
- **`provider` sem `prompt` faz o pack se recusar a carregar.** Isso é
  deliberado: sem prompt o render cairia calado no loop genérico, que é
  exatamente o bug que essa feature veio corrigir.
- **`prompt`** tem teto de 2000 caracteres (o mesmo de
  `VideoParams.video_music_prompt`).
- **Declarar `mood` e `prompt` juntos é útil**, e o código diz isso: o `mood` é
  o fallback de quando o provedor não está configurado.

A adoção nos packs está em andamento (`ghost-stories` → `dark` e
`ufo-sightings` → `mystery` já declaram a seção). A tabela acima é o mapeamento
completo; se algum pack ainda não tiver `[music]`, ele simplesmente continua no
acervo genérico até ganhar a seção.

Há também um helper `mood_tracks("calm")` em `growth/niche.py` que devolve a
lista de faixas de uma pasta. Serve para conferir rapidamente o que o motor
enxerga depois de você copiar arquivos para cá.

## 3. Como preencher as pastas

Esta é a parte que decide se a feature vai ser usada ou vai ficar parada. As
pastas nascem vazias e continuam vazias até alguém pôr som nelas.

**Nada foi baixado.** As fontes abaixo estão listadas com a condição de licença
que eu conheço; termos de site mudam sem aviso, então **leia a licença no
momento do download e guarde a prova** (print da página, o arquivo de licença, o
link, a data). Se um dia chegar uma reclamação, a prova é o que resolve.

### Regra que vale para tudo: "grátis" não quer dizer "pode monetizar"

O erro mais caro do catálogo Creative Commons é este:

- **CC0 / domínio público** — pode monetizar, **sem** atribuição.
- **CC BY** — pode monetizar, **com** atribuição obrigatória.
- **CC BY-SA** — pode monetizar, com atribuição, e obriga a compartilhar
  derivados sob a mesma licença. Complicação desnecessária aqui.
- **CC BY-NC** (qualquer coisa com **NC**) — **não pode**. NC é
  *non-commercial*, e vídeo monetizado é uso comercial. Isso vale mesmo que a
  faixa esteja num site chamado "free music".

Um catálogo agregador não tem "uma licença": cada faixa tem a sua. A licença é
por faixa, sempre.

### Fontes com atribuição NÃO exigida

| Fonte | Licença | Observação |
|---|---|---|
| **YouTube Audio Library** (dentro do YouTube Studio) | Licença própria do YouTube; a maioria das faixas sem atribuição, algumas com (o próprio catálogo marca cada uma) | **A melhor escolha para risco de Content ID**, porque é o próprio YouTube que fornece. Exige conta YouTube para baixar. O uso fora do YouTube (TikTok) é menos claro — leia os termos antes de reusar o mesmo vídeo lá. |
| **Pixabay** (seção Music) | Pixabay Content License; uso comercial permitido, sem atribuição | Proíbe redistribuir a faixa como faixa (usar no vídeo é ok). Muito popular, então veja o aviso de Content ID na seção 4. |
| **Mixkit** (seção Music) | Mixkit Free License; uso comercial permitido, sem atribuição | Também proíbe redistribuição avulsa. Catálogo menor e mais curado. |
| Faixas marcadas **CC0** em qualquer catálogo | CC0 | Sem obrigação nenhuma. Confirme que é CC0 e não CC BY. |

### Fontes com atribuição EXIGIDA

| Fonte | Licença | Observação |
|---|---|---|
| **Incompetech** (Kevin MacLeod) | CC BY 4.0 na via gratuita | Crédito obrigatório, no formato que o site especifica. Acervo enorme e bem organizado por humor. Historicamente **muito** presente em Content ID — leia a seção 4 antes de adotar. |
| **Free Music Archive (FMA)** | **Varia por faixa**: CC0, CC BY, CC BY-NC… | Agregador. Filtre por licença e **descarte tudo que tiver NC**. |
| **ccMixter** | **Varia por faixa**, muita coisa NC | Mesmo cuidado do FMA. |
| **Purple Planet** | Gratuito com crédito obrigatório | Também vende licença sem crédito. |
| **Uppbeat** | Plano gratuito exige crédito e limita downloads por mês | Diferencial real: oferece **liberação explícita de Content ID** vinculada ao canal. Se o crédito não incomodar, é o caminho de menor atrito. |

### Fontes sobre as quais eu **não** tenho certeza

Digo em vez de insinuar:

- **Bensound** — tem camada gratuita com atribuição, mas historicamente com
  restrições ao uso monetizado que dependiam do plano. **Não confirme comigo,
  confirme na página de licença deles** antes de usar em canal monetizado.
- **Musopen e gravações de música clássica em geral** — a *composição* estar em
  domínio público (Bach, Chopin) **não** põe a *gravação* em domínio público. A
  interpretação da orquestra tem direito próprio. Sempre verifique a licença da
  gravação específica, não a do compositor.
- **Internet Archive** — cada item tem um status diferente, muitos deles
  simplesmente errados ou em branco. Não é uma fonte confiável para material
  monetizado sem checagem item a item.
- Qualquer faixa **rippada do YouTube, do Spotify ou de um vídeo de terceiros** —
  não. Nem com crédito.

## 4. O aviso que custa dinheiro: Content ID

**Licença e Content ID são coisas separadas, e a segunda é a que tira o
dinheiro.**

Se o sistema de Content ID do YouTube reconhecer a impressão digital de uma
faixa, ele reivindica o vídeo automaticamente — e, na reivindicação padrão, **a
receita daquele vídeo passa a ir para o reclamante**, não para você. Isso
acontece **mesmo quando a licença permitia o uso**. O robô não lê licença; ele
compara formas de onda.

O caso típico é justamente com música "grátis": um artista publica a faixa sob
CC BY, depois distribui o próprio catálogo por uma distribuidora que registra
tudo no Content ID; ou um terceiro usa a faixa num álbum e registra. A partir
daí, qualquer vídeo com aquela faixa é reivindicado. Você está com a razão, e
mesmo assim sem a receita até resolver.

Você pode contestar, e com licença guardada costuma ganhar — mas a contestação
leva dias ou semanas, e é justamente nos primeiros dias que um vídeo curto faz
quase toda a sua audiência. **Ganhar a disputa depois da janela de tráfego é
perder assim mesmo.** Por isso uma fonte muito usada é um risco prático mesmo
com licença impecável.

### Como checar uma faixa ANTES de adotá-la

Faça isso uma vez por faixa, antes de deixá-la numa pasta de humor. É barato e
protege todos os vídeos futuros que sortearem aquela faixa.

1. **O teste que vale (o único conclusivo):** monte um vídeo curto qualquer com
   a faixa e suba no YouTube como **"não listado"** ou **privado**. O Content ID
   roda igual em vídeo não listado. Abra o YouTube Studio e olhe a coluna
   **"Restrições"** do vídeo. Se aparecer reivindicação, descarte a faixa —
   e você descobriu isso sem gastar um vídeo de verdade.
2. **Espere o processamento terminar** antes de concluir. A varredura não é
   instantânea; um "sem restrições" nos primeiros segundos não significa nada.
   Confira de novo depois de algumas horas.
3. **Busque o nome da faixa + o artista + "content id"** antes mesmo do upload.
   Faixas problemáticas costumam ter reclamação pública de outros criadores.
4. **Prefira fontes que se comprometem com isso por escrito.** O YouTube Audio
   Library (é o próprio YouTube) e serviços com liberação por canal, como o
   Uppbeat, são os que dão garantia real. Um site que só diz "royalty free" não
   está dizendo nada sobre Content ID.
5. **Guarde a licença junto da faixa.** Se um dia precisar contestar, você vai
   querer o link, a data e o texto da licença em mãos, não uma lembrança.

Vale lembrar que o TikTok tem detecção própria e regras próprias para uso
comercial de música — aprovado no YouTube não quer dizer aprovado lá.

## 5. Como escolher uma faixa

### Duração — e o que o motor faz com ela

Os vídeos deste projeto têm entre **65 e 100 segundos** (`target_seconds` nos
packs chega a 100). O que o motor faz está em `app/services/video.py`:

```python
bgm_effects = [
    afx.MultiplyVolume(params.bgm_volume),
    afx.AudioFadeOut(3),
]
if bgm_file_override is None:
    bgm_effects.append(afx.AudioLoop(duration=video_clip.duration))
```

Traduzindo:

- **Faixa mais curta que o vídeo → é repetida em loop** até cobrir a duração.
  Um trecho de 30 s dá três voltas num vídeo de 100 s, e a emenda aparece toda
  vez que ele reinicia. É o defeito mais audível que dá para introduzir aqui.
- **Faixa mais longa que o vídeo → é cortada** na duração do vídeo.
- **O fim sempre tem fade-out de 3 segundos**, então o corte no final nunca é
  abrupto. Você não precisa procurar faixa que "termine bem" — precisa de faixa
  que **comece** bem, porque o começo entra inteiro e sem fade.

Recomendação: **pelo menos 100 segundos, de preferência 2 minutos ou mais**.
Assim nunca há repetição dentro de um vídeo e o fade cuida do fim. Faixa curta
só serve se fizer loop limpo de verdade (o que é raro fora de material vendido
como loop).

### Melodia e voz brigam com a narração

- **Nada com vocais.** Nem letra, nem "oohs/aahs", nem vocal picotado. O
  ouvinte processa voz como fala, e duas falas simultâneas cancelam uma à outra.
  É o erro que mais estraga o vídeo.
- **Melodia forte também briga**, mesmo instrumental: um tema marcante ocupa a
  mesma faixa de frequência da voz (mais ou menos 200 Hz a 4 kHz) e disputa a
  atenção. Procure **textura**, não canção: pads, drones, arpejos suaves,
  percussão discreta, piano esparso.
- **Cuidado com dinâmica.** O pipeline aplica **volume fixo**
  (`MultiplyVolume`), **sem ducking e sem sidechain**: a trilha não abaixa
  sozinha quando a narração entra. Uma faixa que começa mínima e explode num
  refrão vai enterrar a narração exatamente no refrão. Prefira faixas de
  dinâmica plana, do começo ao fim.
- **Sem transições dramáticas** (risers, impactos, silêncios). Elas sugerem um
  corte que o vídeo não tem, porque a música não foi editada junto com ele.

### Volume medido (loudness)

O acervo antigo está entre **-19,7 e -23,9 LUFS**, e os `bgm_volume` dos packs
(0,12 a 0,2) foram ajustados nessa premissa. Como o multiplicador é fixo, uma
faixa masterizada em -9 LUFS vai sair **muito** mais alta que as outras com o
mesmo `bgm_volume`. Se puder, normalize o que você adicionar para perto de
**-20 LUFS** antes de largar na pasta. Isso mantém todos os humores consistentes
entre si e evita ter que reajustar pack por pack.

### Quantidade

Comece com **3 a 5 faixas por humor**. É o bastante para o sorteio não repetir
de forma óbvia, e é pouco o bastante para você conseguir checar o Content ID de
cada uma direito. Melhor quatro faixas verificadas do que trinta duvidosas — foi
exatamente o excesso sem variedade que criou o problema da seção 1.

## 6. Deixar uma pasta vazia é seguro

Não há obrigação de preencher as quatro. Uma pasta vazia **não quebra nada**: o
pack cai no acervo genérico dos 29 arquivos, que é exatamente o comportamento de
hoje. `get_bgm_file()` só usa um caminho se ele vier preenchido; vazio, cai no
sorteio de sempre.

Então dá para fazer um humor por vez: encha o `dark/` para `ghost-stories`, veja
se a diferença aparece no vídeo, e só depois mexa no resto. Nada regride
enquanto isso.

Os `.gitkeep` existem só para o git registrar pastas vazias — o git não versiona
diretório, só arquivo. Não apague; sem eles as pastas somem num clone novo.

## 7. Por que as 29 faixas antigas não foram distribuídas nos humores

Elas continuam onde estavam, na raiz, e nenhuma foi movida para `calm/`,
`upbeat/`, `dark/` ou `mystery/`.

O motivo é simples: **quem organizou estas pastas não ouviu as faixas.** Dá para
medir duração e LUFS por programa; não dá para julgar clima assim. Distribuir os
29 arquivos entre os humores com base no número do arquivo seria um palpite
apresentado como curadoria — e um palpite errado é pior que pasta vazia, porque
some: você passaria a confiar que `dark/` foi escolhido a ouvido quando não foi,
e o pack de assombração ganharia uma trilha alegre sem ninguém perceber por quê.

Some-se a isso o que a medição já mostrou: as 29 são a mesma textura. Espalhar
uma textura única por quatro humores não produziria quatro humores.

Essa escolha é sua, e leva minutos: ouça alguns dos 29, e se algum servir a um
humor, copie (não mova — mover muda o acervo genérico) para a pasta certa.

## 8. A alternativa paga: música gerada por IA

Em vez de apontar uma pasta, um pack pode pedir música **gerada sob medida para
o tema de cada vídeo**. Nesse caso cada vídeo recebe uma trilha única, e o
problema da repetição desaparece por construção — assim como o risco de Content
ID, já que a faixa nunca existiu antes.

Os dois provedores estão registrados em `app/services/task.py`, no dicionário
`_VIDEO_MUSIC_PROVIDERS`, e são os **únicos** dois nomes que o pack aceita:

- **`sonilo`** (serviço em `app/services/sonilo.py`)
- **`elevenlabs`** (serviço em `app/services/elevenlabs_music.py`)

No pack, isso é a mesma seção `[music]` da seção 2:

```toml
[music]
provider = "sonilo"
prompt = "colchão sombrio de drone grave, sem melodia, sem percussão"
mood = "dark"          # opcional, mas recomendado: é o fallback
```

Lembre das regras do parser: **`provider` sem `prompt` derruba o carregamento do
pack** (de propósito), e o `prompt` tem teto de 2000 caracteres. Manter um
`mood` junto é a rede de segurança para quando o provedor não estiver
configurado.

No motor, isso vira `bgm_type = "sonilo"` ou `"elevenlabs"` (no lugar de
`"random"`) e o prompt chega pelo campo neutro `video_music_prompt`; para o
Sonilo ainda existe o campo legado `sonilo_bgm_prompt`, lido só quando o neutro
está vazio.

Dois detalhes que importam:

- **Custa por vídeo.** É uma chamada de API a cada geração. Num canal que
  publica todo dia, isso é um custo recorrente — ao contrário das pastas, que
  são pagas uma vez (em tempo de curadoria) e reusadas para sempre.
- **Há fallback.** Se o provedor falhar, o task registra um aviso
  (`sonilo_bgm_failed` / `elevenlabs_bgm_failed`) e o vídeo sai assim mesmo. Não
  é ponto único de falha.

Regra prática: pastas de humor para o volume do dia a dia, IA para o vídeo em
que a trilha realmente importa.

---

## Nota sobre `.gitignore` (por que não existe um aqui)

**Decisão: não foi criado `.gitignore` neste diretório. Áudio aqui deve ser
commitado.**

Não é preferência, é a convenção já estabelecida no repo, verificada:

1. `git ls-files resource/songs` devolve **as 29 faixas**. Elas são versionadas
   hoje.
2. O `.gitattributes` da raiz declara `*.mp3 binary` — o repo já prevê e trata
   mp3 versionado.
3. O `.gitignore` da raiz **não** exclui `resource/songs/`. Exclui
   `/storage/`, que é outra coisa.
4. O próprio `app/services/bgm.py` documenta a separação, em `uploaded_bgm_dir()`:
   as músicas embutidas são **recurso de código** e ficam em `resource/songs`;
   o que o usuário sobe é **dado de runtime** e vai para `storage/`, montado no
   Docker, "para não sujar a árvore do Git".

As faixas de humor são recurso de código pela definição do próprio projeto:
fazem parte do pack, precisam existir num clone novo e numa outra máquina, e um
pack que referencia `dark/` sem a faixa junto simplesmente cai no acervo
genérico e perde a feature em silêncio. Ignorá-las quebraria isso e contrariaria
a convenção existente.

Duas ressalvas honestas, para você decidir com os olhos abertos:

- **Peso.** As 29 atuais somam cerca de 56 MB. Áudio commitado infla o histórico
  para sempre, e git não guarda delta útil de mp3. Se um dia isso incomodar, a
  saída certa é **Git LFS** (que mantém os arquivos versionados e rastreáveis),
  não um `.gitignore` (que os faz sumir do clone).
- **Licença.** Commitar uma faixa é redistribuí-la. Algumas licenças que
  permitem uso em vídeo **proíbem redistribuir a faixa avulsa** — é o caso
  explícito de Pixabay e Mixkit. Um repositório **privado** não é publicação e
  não muda de figura; se este repo um dia virar público, revise as faixas de
  humor antes, ou mova as de licença restritiva para `storage/` e aponte o pack
  por caminho.
