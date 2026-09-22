# Onde rodar: medição real, não estimativa

Todos os números abaixo saíram de um render executado neste repositório, não
de benchmark de terceiros.

## O que foi medido

Máquina de teste: 4 vCPU Intel Xeon @ 2.80GHz, 15 GB RAM, Debian, ffmpeg 6.1.

| Item | Valor medido |
|---|---|
| Vídeo gerado | 1080×1920, H.264 + AAC, 38,07 s |
| Tempo total (TTS + legenda + render) | 310 s |
| Razão render/duração | **~8,2× o tempo do vídeo** |
| Pico de RAM | **589 MB** |
| CPU efetivamente usada | ~1,6 núcleo (161%) |
| Tamanho do arquivo final | 2,9 MB |

Extrapolando para o formato que interessa (65–80 s, mínimo do TikTok para
pagamento): **~9 a 10 minutos de CPU por vídeo**.

Uma ressalva honesta: o teste usou imagens estáticas como material. Com clipes
de vídeo do Pexels há decodificação do material de origem, então o custo real
fica acima disso — trate 10 min/vídeo como piso, não como média.

## Medição de um vídeo ilustrado completo

O pack `ufo-sightings` gera as cenas em vez de buscar em banco, e o perfil é
diferente. Um vídeo real de 91 s, na mesma máquina de 4 vCPU:

| Etapa | Tempo | Observação |
|---|---|---|
| Pauta + roteiro + narração + legenda | ~2 min | quase tudo espera de rede |
| Geração das imagens | ~21 s por cena | **rede**, não CPU: o GPU é do provedor |
| Combinação + render final | ~12 min | **é aqui que a CPU trabalha** |
| **Total** | **~17 min** | com 8 cenas |

Medido na fase de combinação: **pico de 586 MB de RAM, 243% de CPU** (ou seja,
usa cerca de 2,4 núcleos). Arquivo final de 24 MB.

Com as ~21 cenas que o pack passou a pedir, a geração sobe para ~7 min e o
total fica em **~20 a 22 min por vídeo**.

Duas conclusões que mudam a escolha de servidor:

1. **Não precisa de GPU nem de máquina grande.** O trabalho pesado de imagem
   acontece no provedor. O que sobra local é ffmpeg.
2. **O gargalo é CPU de render**, e ele escala com a duração do vídeo, não com
   o número de cenas.

## Quantos núcleos valem a pena

A mesma combinação de vídeo, com a CPU limitada por `taskset`:

| Núcleos | Tempo | CPU usada | Pico de RAM |
|---|---|---|---|
| 4 | 155 s | 272% | 573 MB |
| 2 | 224 s | 184% | 572 MB |

**Metade dos núcleos custa +44% de tempo, não +100%.** A razão está na coluna
do meio: o render satura perto de **2,7 núcleos** e não usa mais que isso.

Isso inverte a intuição de que uma máquina de 16 núcleos seria muito melhor que
uma VPS de 2. Para **um** vídeo por vez ela não é: os núcleos extras ficam
ociosos. Só ajudariam rodando vários renders em paralelo — o que o bot
deliberadamente não faz, porque dois renders disputando os mesmos núcleos
terminam depois que os mesmos dois em sequência.

Consequência prática: **2 núcleos bastam**, e a escolha entre servidor e máquina
local deixa de ser sobre velocidade. Passa a ser sobre disponibilidade.

## O que o app exige de verdade

- **CPU**: é o único gargalo. Render é ffmpeg, puro CPU.
- **GPU**: não é necessária. Só entra se você usar geração de vídeo por IA ou
  transcrição Whisper em modelo grande.
- **RAM**: **mínimo real de 1,2 GB**, e o `deploy/bootstrap-ubuntu.sh` para de
  propósito abaixo disso. Não é só o render (que tem pico de ~600 MB): com
  menos que isso o próprio gerenciador de pacotes leva OOM kill no meio da
  descompactação e deixa o sistema meio configurado. 2 GB é confortável.
- **Python**: **3.11, 3.12 ou 3.13**. Em **3.14 o pydantic quebra** ao montar o
  schema, então o bootstrap recusa essa versão. Isso importa porque 3.14 já é o
  `python3` padrão de distros rolling como o Kali; nesse caso o script usa `uv`
  para baixar um Python 3.11 independente da distro.
- **Compilador**: não é preciso. Toda dependência tem wheel pronta, então o
  bootstrap não instala `build-essential` nem `python3-dev` — esse conjunto são
  ~450 MB de download e 1,5 GB em disco, à toa.
- **Disco**: cache de material + saídas. 30–50 GB dá conta com folga.
- **Banda**: download de material é entrada (grátis em todo provedor); upload
  dos vídeos é ~5 MB cada.

## Comparativo

| Opção | Custo | Viável? | Observação |
|---|---|---|---|
| **Seu próprio PC/notebook** | R$ 0 | **Sim, melhor opção** | Mais CPU que qualquer free tier. Precisa ficar ligado durante o lote. |
| **Oracle Cloud Always Free** (ARM Ampere) | R$ 0 | **Sim** | Único free tier que aguenta. Ver ressalvas abaixo. |
| Hetzner CX22 | ~€4/mês | Sim | x86, 2 vCPU/4 GB. Sem sorteio de capacidade. Melhor custo-benefício pago. |
| Google Cloud e2-micro | R$ 0 | **Não** | 1 GB RAM e 0,25 vCPU compartilhada. Entra em swap e o render leva horas. |
| AWS t2.micro | R$ 0 por 12 meses | **Não** | 1 GB RAM, mesmo problema, e expira. |
| Fly.io / Render free | R$ 0 | **Não** | Contêiner efêmero, hiberna no meio do lote. |
| GitHub Actions | R$ 0 | **Não** | Os termos de uso proíbem usar os runners para esse tipo de carga. |

## Ressalvas do Oracle Always Free

Três coisas que você precisa saber antes de contar com ele:

1. **O limite caiu.** Em 15 de junho de 2026 a Oracle reduziu o Ampere A1 de
   4 OCPU/24 GB para **2 OCPU/12 GB**, sem anúncio público. Ainda sobra folga
   para esse uso, mas não conte com a configuração antiga que aparece em
   tutoriais mais velhos.
2. **Capacidade é sorteio.** A região costuma responder "Out of Capacity" para
   instâncias ARM. Pode levar dias tentando, e existem scripts de retry.
3. **Conta ociosa pode ser reclamada.** Mantenha uso real ou um lote agendado.

Com 2 OCPU ARM, a estimativa é de **10–15 min por vídeo de 70 s**, ou seja,
folgadamente **20 a 40 vídeos por dia** — muito acima do que qualquer canal
saudável deveria publicar.

## Recomendação

**Comece no seu próprio computador.** É grátis, é mais rápido que qualquer
free tier, e nas primeiras semanas você vai querer revisar cada vídeo antes de
postar mesmo. VPS só resolve um problema que você ainda não tem.

**Migre para a Oracle Always Free** quando o lote passar a rodar sozinho todo
dia e você não quiser deixar a máquina ligada. Use `deploy/bootstrap-ubuntu.sh`.

**Pague a Hetzner (~€4/mês)** se a Oracle não liberar capacidade ARM ou se
você quiser previsibilidade. É menos de um café por mês e elimina o problema.

## Por que um VPS resolve o que o WSL não resolve

A diferença não é potência, é o agendador. No WSL, timer e cron só disparam
enquanto o Windows está ligado e a distro rodando — desligou a máquina, o lote
não acontece. Num VPS há systemd de verdade, e `deploy/install-timer.sh`
instala um timer que dispara sozinho, com `Persistent=true` para recuperar um
disparo perdido caso a máquina tenha ficado fora do ar.

Uma vez no servidor, o ciclo diário não depende de você:

```bash
deploy/install-timer.sh ufo-sightings 2 07:00
```

O que **continua** dependendo de você é publicar. Nada é postado
automaticamente, de propósito: os vídeos ficam em `storage/growth/out/` para
serem revistos. Rode `python -m growth review` antes de subir qualquer coisa.

## Limpeza de disco, que passa despercebida

Cada vídeo final tem ~24 MB, e o diretório da tarefa guarda as imagens
geradas, os clipes intermediários e o áudio — bem mais que isso. Dois vídeos
por dia enchem alguns GB por mês.

Nos 200 GB da Oracle isso demora a incomodar; nos 40 GB de um VPS pequeno, não.
Limpe as tarefas antigas periodicamente — as saídas finais já estão copiadas
para `storage/growth/out/`:

```bash
find storage/tasks -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
```

## Custo de API, que é o custo que sobra

A infraestrutura pode ser R$ 0. O que resta é:

- **Material de vídeo**: Pexels e Pixabay têm API gratuita. Custo zero.
- **Voz**: `edge-tts` é gratuito e não pede chave. Custo zero.
- **Legenda**: `edge` ou Whisper local. Custo zero.
- **LLM**: é o único item pago, e mesmo assim é centavos. Cada vídeo consome
  uma chamada de pauta (dividida pelo lote) e uma de roteiro. Com um provedor
  barato isso fica na casa de centavos de dólar por vídeo. Há provedores com
  free tier que cobrem um volume baixo sem custo.

Ou seja: dá para rodar isso com custo mensal praticamente zero. O recurso
escasso aqui não é dinheiro nem CPU — é a atenção que você dá a cada vídeo
antes de publicar.
