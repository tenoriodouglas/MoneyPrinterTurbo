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
git clone -b claude/blissful-lamport-2qe81i \
  https://github.com/tenoriodouglas/MoneyPrinterTurbo.git
cd MoneyPrinterTurbo
deploy/wsl-setup.sh
```

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

Depois de preencher as chaves, confirme tudo de uma vez:

```bash
.venv/bin/python -m growth doctor
```

Ele testa de verdade: faz uma chamada minima ao LLM, uma busca real de material e
uma sintese curta de voz. Cada falha vem com o que fazer. Sai com codigo 1 se
algo estiver quebrado, entao serve para portao em script.

## Os comandos

```bash
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

**Para começar, `ai-tools` ou `b2b-software`.** Não são os de CPM mais alto,
mas são os de menor concorrência — e num canal novo a concorrência importa
mais que a tabela de CPM.

Cada pack define o nicho, a voz editorial, os ângulos que revezam entre vídeos,
frases proibidas, termos de busca de material, vozes de narração e como aquele
nicho ganha dinheiro. Criar um nicho novo é copiar um `.toml` e editar — não
mexe em código.

## Por que não é só "gerar 10 vídeos por dia"

Em julho de 2026 o YouTube passou a tratar conteúdo produzido em massa como
**conteúdo inautêntico**, com detecção em nível de canal. A camada `growth/`
foi desenhada contra isso: ângulo editorial diferente por vídeo, exigência de
número ou mecanismo específico em cada roteiro, alternância de vozes e de
ritmo visual, e histórico por nicho para nunca repetir pauta.

Isso reduz o risco. Não elimina. Leia `docs/MONETIZACAO.md` antes de publicar
o primeiro vídeo — principalmente a parte de por que receita de anúncio em
Shorts é o pior caminho e o que funciona melhor.

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
