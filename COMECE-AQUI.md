# Comece aqui

Este repositório é o [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)
(licença MIT, de harry0703) com uma camada de estratégia por cima.

O projeto original resolve a parte técnica: assunto → roteiro → narração →
material de vídeo → legenda → render. Ele renderiza o que você mandar.

O que foi adicionado aqui resolve a parte que decide se o canal ganha dinheiro
ou é derrubado: **o que** produzir, com **qual ângulo**, e **como não virar
conteúdo produzido em massa**.

## Em 5 minutos

```bash
# 1. Preparar a máquina (Ubuntu/Debian, x86 ou ARM)
deploy/bootstrap-ubuntu.sh

# 2. Editar config.toml: pexels_api_keys e o provedor de LLM
nano config.toml

# 3. Ver os nichos disponíveis e quanto cada um vale
.venv/bin/python -m growth niches

# 4. Gerar o primeiro vídeo de ponta a ponta
.venv/bin/python -m growth run ai-tools --count 1
```

O vídeo sai em `storage/growth/out/<nicho>/<data>/`, junto de um
`captions.txt` com a legenda e as hashtags prontas para publicar.

## No WSL (Windows)

**Antes de tudo, confira a memoria.** O WSL nao herda a RAM da maquina: o
Windows impoe um teto, e o padrao pode ser uma fracao pequena. Abaixo de
~1,2 GB nem o `apt` consegue descompactar pacotes — ele leva OOM kill no meio e
deixa o sistema meio configurado.

```bash
free -h   # se "total" estiver abaixo de 1,2Gi, corrija antes de continuar
```

Para corrigir sem editar nada a mao, **de dentro do WSL**:

```bash
deploy/wsl-configure-memory.sh
```

Ele acha o seu perfil do Windows sozinho, le quanta RAM a maquina tem, reserva
metade para o WSL e escreve o `.wslconfig` no lugar certo. Se ja existir um, faz
backup e **mescla** em vez de sobrescrever, entao configuracoes suas nao se
perdem. Use `--dry-run` para so ver o que ele faria, ou `--memory 6GB` para
escolher o valor.

Escrever o arquivo por ali evita os dois jeitos de errar isso na mao: o Bloco de
Notas salvando como `.wslconfig.txt`, e o arquivo indo parar fora de
`C:\Users\<voce>\`.

Depois, no **PowerShell** (nao no WSL): `wsl --shutdown`. Reabra a distro e
confira com `free -h`.

Se preferir fazer pelo Windows, o conteudo minimo do arquivo e:

```ini
[wsl2]
memory=8GB
swap=2GB
```

Com memoria suficiente:

```bash
# O diretorio precisa ser do Linux, nunca /mnt/c
cd ~
git clone https://github.com/tenoriodouglas/MoneyPrinterTurbo.git
cd MoneyPrinterTurbo
deploy/wsl-setup.sh
```

Se o trabalho ainda nao estiver mesclado, use o branch indicado no pull request
aberto: `git checkout <branch-do-pr>` antes de rodar o `wsl-setup.sh`.

### O que o script trata

1. **Memoria.** Para com instrucao clara se estiver abaixo do minimo, em vez de
   deixar o `apt` morrer no meio.
2. **Caminho.** Repo em `/mnt/c` passa por uma camada de traducao para o
   filesystem do Windows, e o render faz muita I/O de arquivo pequeno. Em `~`
   fica no disco ext4 do proprio WSL.
3. **Versao do Python.** O projeto roda em **3.11, 3.12 ou 3.13**. Em **3.14 o
   pydantic quebra** — e 3.14 e o `python3` padrao do Kali rolling. Se o
   `python3` do sistema nao servir, o script usa o `uv` para baixar um Python
   3.11 independente da distro.
4. **systemd desligado.** Sem ele o timer nao instala. O
   `deploy/install-timer.sh` detecta e cai para cron sozinho.

O bootstrap instala so `ffmpeg`, `git`, `curl` e `ca-certificates`. Todas as
dependencias Python vem de wheels prontas, entao **nao** e preciso compilador,
`build-essential` nem `python3-dev`.

### Se uma tentativa anterior foi morta no meio

**Nao rode `apt --fix-broken install`.** Ele tenta refazer exatamente a
descompactacao que estourou a memoria, e falha de novo no mesmo ponto. O certo e
**remover** o que sobrou, o que nao consome memoria:

```bash
sudo dpkg --remove --force-depends build-essential gcc g++ gcc-16 g++-16 \
    gcc-x86-64-linux-gnu g++-x86-64-linux-gnu \
    gcc-16-x86-64-linux-gnu g++-16-x86-64-linux-gnu
sudo apt-get autoremove -y
sudo dpkg --audit          # nao deve imprimir nada
```

Nenhum desses pacotes e necessario: toda dependencia Python tem wheel pronta.
Pacotes que ja nao estao instalados so geram aviso, sem erro.

### O limite que nenhum script resolve

Timer ou cron **dentro** do WSL so disparam enquanto o Windows esta ligado e a
distro rodando. Para um lote que roda de verdade sem voce, agende pelo Windows:

```
Agendador de Tarefas -> Criar Tarefa -> Acao:
wsl.exe -d kali-linux -- bash -lc "cd ~/MoneyPrinterTurbo && .venv/bin/python -m growth run ai-tools --count 3"
```

## O que configurar

| Item | Custo | Onde |
|---|---|---|
| Material de vídeo (Pexels) | Grátis | `pexels_api_keys` em `config.toml` |
| Narração (edge-tts) | Grátis, sem chave | já é o padrão |
| Legenda | Grátis | `subtitle_provider = "edge"` |
| LLM (pauta + roteiro) | Centavos por vídeo | `llm_provider` + a chave do provedor |

Só o LLM custa dinheiro, e é o único item sem alternativa gratuita completa.

Preencha as duas com:

```bash
.venv/bin/python -m growth config \
  --pexels SUA_CHAVE_PEXELS \
  --llm gemini --llm-key SUA_CHAVE_GEMINI
```

Isso reescreve so as linhas certas, faz backup antes, limpa aspas e espacos que
vem junto no copiar-colar, recusa a mudanca se o resultado nao for TOML valido,
e deixa o arquivo em `chmod 600`. As chaves aparecem mascaradas na saida.

Editar a mao tambem funciona, mas repare: **o app reescreve o `config.toml` e
remove os comentarios na primeira execucao**, entao os numeros de linha mudam
depois do primeiro uso. Procure pelo nome da chave, nunca pela linha.

Depois de preencher as chaves, confirme tudo de uma vez:

```bash
.venv/bin/python -m growth doctor
```

Ele testa de verdade: faz uma chamada minima ao LLM, uma busca real de material e
uma sintese curta de voz. Cada falha vem com o que fazer. Sai com codigo 1 se
algo estiver quebrado, entao serve para portao em script.

## Os comandos

```bash
# Preencher as chaves sem editar TOML na mao
python -m growth config --pexels SUA_CHAVE --llm gemini --llm-key SUA_CHAVE

# Conferir maquina e config ANTES de gastar um render
python -m growth doctor

# Listar nichos ordenados por retorno estimado
python -m growth niches

# Gerar as pautas de um lote, sem renderizar (barato, revise antes)
python -m growth plan personal-finance --count 5

# Renderizar um plano já revisado
python -m growth produce storage/growth/plans/personal-finance-<data>

# Planejar e renderizar de uma vez
python -m growth run b2b-software --count 3

# Corte longo 16:9 para YouTube, onde o CPM alto realmente se aplica
python -m growth plan personal-finance --count 2 --aspect 16:9 --paragraphs 9

# Conferir se os videos podem faturar antes de publicar
python -m growth review

# Ver tudo que já foi produzido
python -m growth ledger
```

O fluxo recomendado é `plan` → ler as pautas → `produce`. As pautas são baratas
de gerar e caras de renderizar; revisar no meio economiza tempo e melhora muito
o resultado.

## Os nichos

Seis packs em `niches/*.toml`, escolhidos por CPM alto e por funcionarem com
material de banco de imagens:

| Nicho | CPM (US$) | Concorrência | Receita principal |
|---|---|---|---|
| `personal-finance` | 15–30 | alta | afiliados |
| `b2b-software` | 12–18 | **baixa** | geração de leads |
| `digital-marketing` | 12–18 | alta | venda de serviço |
| `ai-tools` | 8–20 | média | afiliados |
| `health-longevity` | 10–18 | alta | afiliados |
| `real-estate` | 10–16 | média | geração de leads |
| `ufo-sightings` | 3–8 | média | afiliados |

**Para começar, `ai-tools` ou `b2b-software`.** Não são os de CPM mais alto,
mas são os de menor concorrência — e num canal novo a concorrência importa
mais que a tabela de CPM.

Cada pack define o nicho, a voz editorial, os ângulos que revezam entre vídeos,
frases proibidas, termos de busca de material, vozes de narração e como aquele
nicho ganha dinheiro. Criar um nicho novo é copiar um `.toml` e editar — não
mexe em código.

### Imagem gerada em vez de banco de imagens

Buscar clipe em banco por palavra-chave traz material genérico: a voz descreve
uma coisa e a tela mostra outra. O pack `ufo-sightings` não busca nada — ele
**gera cada cena a partir do roteiro**, no mesmo estilo de desenho em todos os
vídeos, o que também dá identidade visual ao canal.

```bash
# Aplica o estilo do pack (chave gratuita em https://enter.pollinations.ai/)
python -m growth config --niche ufo-sightings --image-key SUA_CHAVE

# Confere o endpoint de imagem antes de gastar um render
python -m growth doctor --niche ufo-sightings

python -m growth run ufo-sightings --count 1
```

Custa zero: o modelo Flux do Pollinations é gratuito e o endpoint é compatível
com OpenAI. Para dar esse tratamento a outro nicho, copie a seção `[images]` do
pack de UFO e ajuste o `prompt_template` — ele precisa conter `{term}`.

**Atenção ao trocar de nicho:** `openai_image_*` são configurações globais do
app, não por tarefa. Rodar `--niche` de novo troca o estilo ativo, então rode
um nicho ilustrado de cada vez.

## Tema livre

Para um assunto que nenhum pack cobre, dá para mandar o assunto direto, sem
criar pack nenhum. Pelo Telegram:

```
/tema histórias de fantasma em hospitais abandonados
/tema naufrágios que nunca foram explicados
```

Pelo terminal é o `--theme`, que funciona em `plan` e em `run`:

```bash
# Planejar e renderizar de uma vez
python -m growth run free-theme --theme "histórias de fantasma" --count 1

# Só a pauta, para ler antes de gastar um render
python -m growth plan free-theme --theme "naufrágios sem explicação" --count 1
```

O pack `free-theme` é neutro de propósito: ele entrega o formato e os limites
— como abrir, um assunto por roteiro, o que não dizer, ritmo e duração — e o
tema entrega o assunto.

**E é só isso que ele entrega: tema livre reaproveita o formato, não o
julgamento editorial.** Esse é o ponto que decide a qualidade do que sai.
Compare com o `ufo-sightings`: as regras dele são de documentário — nunca
afirmar, sempre atribuir ("o piloto relatou", "o memorando dizia"), nunca
inventar um caso. Essas regras estão certas para relatos documentados e
erradas para folclore. Numa história de fantasma não existe arquivo para
atribuir nem testemunha para citar: exigir fonte faz o roteiro inventar um
caso que não existe, ou ficar sem graça. Ali o enquadramento honesto é outro
— *isto é uma história que as pessoas contam* — e um pack neutro não sabe
disso, porque ele não sabe do que você está falando.

Três consequências práticas:

- **Histórias de fantasma já têm pack.** Foi esse assunto que motivou o
  `/tema`, e ele passou no teste: existe `ghost-stories` em `niches/`, escrito
  para folclore. Para esse assunto use `python -m growth run ghost-stories`, e
  não `/tema histórias de fantasma` — o tema livre já cumpriu o papel dele ali.
- **Tema livre é um vídeo, não um lote.** Um lote existe para revezar ângulos
  e encher uma semana de programação; um tema livre existe para você ouvir
  como o assunto soa. E como as regras editoriais aqui são genéricas, um lote
  só multiplica o que o enquadramento errou. Pelo Telegram isso é garantido: o
  `/tema` não aceita quantidade, e pedir mais é mandar outro `/tema`. No
  terminal o `run` continua aceitando `--count`, e o padrão dele é 3 — passe
  `--count 1` até o resultado convencer.
- **Cada tema guarda o próprio "já cobri isso".** O histórico é por tema, e
  não só por nicho (`storage/growth/history/`). Pedir o mesmo tema duas vezes
  dá assuntos novos, e um tema não deixa o outro parecendo repetitivo.

### Quando o tema merece um pack

Tema que você vai repetir merece pack próprio. É um arquivo TOML em `niches/`
— não mexe em código. Copie `niches/ufo-sightings.toml`, que é o exemplo mais
completo e justamente o de um assunto onde a regra editorial pesa mais que o
formato.

Dois campos carregam esse julgamento, e são os primeiros a reescrever:

- **`[content].system_prompt`** — as regras duras do roteiro: como abrir, o
  que nunca afirmar, o que atribuir e a quem, como terminar. É aqui que mora a
  diferença entre "relato documentado" e "história que as pessoas contam".
- **`[content].angles`** — os ângulos que revezam entre os vídeos, um por
  vídeo. Sem eles, dez vídeos do mesmo nicho saem com a mesma forma, que é
  exatamente o que as plataformas detectam.

O resto (`[economics]`, `[video]`, `[images]`, `[platform]`, `[monetization]`)
você ajusta depois, com o canal já andando.

## Por que não é só "gerar 10 vídeos por dia"

Em julho de 2026 o YouTube passou a tratar conteúdo produzido em massa como
**conteúdo inautêntico**, com detecção em nível de canal. A camada `growth/`
foi desenhada contra isso: ângulo editorial diferente por vídeo, exigência de
número ou mecanismo específico em cada roteiro, alternância de vozes e de
ritmo visual, e histórico por nicho para nunca repetir pauta.

Isso reduz o risco. Não elimina. Leia `docs/MONETIZACAO.md` antes de publicar
o primeiro vídeo — principalmente a parte de por que receita de anúncio em
Shorts é o pior caminho e o que funciona melhor.

## Pelo Telegram

Dispara os lotes pelo celular, sem SSH. Enquanto o render corre, o bot vai
dizendo onde está — etapa atual, quantas cenas já ficaram prontas, porcentagem
aproximada e quanto tempo ainda falta — e no fim manda o vídeo. Como o bot faz
*polling*, o servidor **não precisa de porta aberta, domínio nem certificado** —
o que elimina a parte mais chata de configurar uma VPS gratuita.

```bash
# 1. Crie o bot no @BotFather no Telegram e copie o token
python -m growth config --telegram-token SEU_TOKEN

# 2. Rode e mande qualquer coisa ao bot; ele responde com o id do SEU usuario
python -m growth bot

# 3. Autorize esse id e rode como servico
python -m growth config --telegram-user SEU_USER_ID
deploy/install-bot.sh
```

Comandos: `/start` e `/help` (os dois mostram o menu), `/niches`,
`/run <nicho> [n]`, `/tema <texto livre>` (um vídeo), `/status`,
`/review [n]` (n entre 1 e 20), `/last [n]`.

Quatro coisas de propósito:

- **A trava é pelo seu usuário do Telegram, não pela conversa.** Em conversa
  privada os dois números são iguais, mas o id de um grupo pertence ao grupo:
  autorizar a conversa entregaria o bot a todos os membros dele. Quem não está
  na lista recebe apenas o próprio id, para você decidir se libera.
- **Um lote por vez.** Dois renders competindo por 2 núcleos terminam depois do
  que os mesmos dois em sequência.
- **Vídeo acima de 50 MB não sobe.** É o teto do Bot API. Nesse caso o bot
  manda o caminho no servidor em vez de falhar calado — acontece com cortes
  longos 16:9, não com os verticais de ~24 MB.
- **O bot fala sem ser perguntado.** Ele avisa ao mudar de fase, a cada 5 cenas
  desenhadas e, de qualquer jeito, no mínimo a cada 5 minutos. Esse mínimo
  existe porque o render final passa ~12 minutos sem imprimir uma linha
  sequer, e silêncio desse tamanho parece que travou. Duas ressalvas sobre o
  que ele mostra: a porcentagem é **estimativa**, calculada com os pesos
  medidos de cada fase, e não uma contagem do que falta — por isso vem com `~`.
  E abaixo de ~8% concluído não aparece tempo restante, e sim `calculando ⏱`:
  com 3% feito, poucos segundos de variação mudam a estimativa em dez minutos,
  então um número ali seria confiante e errado. Não é bug.

## Documentação

- **`docs/MONETIZACAO.md`** — a matemática real de quanto cada caminho paga,
  os limites das plataformas, e o ranking honesto de como isto dá dinheiro.
- **`docs/HOSPEDAGEM.md`** — medições reais de CPU e RAM por vídeo, e onde
  rodar isto (incluindo se dá para usar VPS gratuita).
- `README.md` — documentação do projeto original: WebUI, API, Docker, todos os
  provedores suportados.

## Rodando sozinho

```bash
deploy/install-timer.sh ai-tools 3      # 3 vídeos por dia, 07:00
```

Isso instala um timer do systemd que renderiza o lote diário. **Nada é
publicado automaticamente** — os vídeos ficam em `storage/growth/out/` para
você revisar. Isso é de propósito: publicar sem revisar é exatamente o
comportamento que as plataformas penalizam.
