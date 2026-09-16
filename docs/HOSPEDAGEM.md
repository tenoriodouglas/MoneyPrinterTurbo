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

## O que o app exige de verdade

- **CPU**: é o único gargalo. Render é ffmpeg, puro CPU.
- **GPU**: não é necessária. Só entra se você usar geração de vídeo por IA ou
  transcrição Whisper em modelo grande.
- **RAM**: ~600 MB por render. 1 GB é apertado, 2 GB já é confortável.
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
