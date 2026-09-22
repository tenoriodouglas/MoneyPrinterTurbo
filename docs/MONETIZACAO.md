# Monetização: a matemática antes da expectativa

Este documento existe porque a parte difícil não é gerar vídeo. É que o
caminho óbvio para ganhar dinheiro com vídeo automatizado é o pior caminho,
e isso só fica visível quando você faz as contas.

## 1. A correção que muda tudo

Os CPMs altos que aparecem em toda pesquisa de nicho — finanças a US$ 15–30,
B2B a US$ 18 — são de **vídeo longo no YouTube**. Não valem para Shorts.

| Formato | RPM realista | Para ganhar US$ 500/mês |
|---|---|---|
| YouTube Shorts | US$ 0,01–0,10 | ~10 milhões de views/mês |
| TikTok Creator Rewards | US$ 0,40–1,00 | ~715 mil views/mês |
| YouTube vídeo longo (nicho caro) | US$ 3–16 | ~50 mil views/mês |

O RPM de Shorts é de 3% a 14% do RPM de vídeo longo. Para a maioria dos
canais, a receita direta de Shorts fica abaixo de 2% do faturamento total.

**Consequência prática:** se o objetivo é receita de anúncio em vídeo vertical,
**TikTok paga de 10 a 20 vezes mais que YouTube Shorts**. E se o objetivo é
receita de anúncio de verdade, o formato é vídeo longo 16:9 — que este repo
também gera, mudando `aspect` no pack de nicho.

## 2. As portas de entrada, e quando elas fecham mais

**YouTube Partner Program (hoje):** 1.000 inscritos **e** 4.000 horas de
exibição em 365 dias, **ou** 10 milhões de views de Shorts em 90 dias. Os dois
critérios não somam.

**A partir de 1º de fevereiro de 2027 aperta:** 1.000 inscritos mais 8.000
horas de exibição, ou 20 milhões de views de Shorts em 90 dias.

**TikTok Creator Rewards:** 18 anos, 10.000 seguidores, 100.000 views nos
últimos 30 dias, conta em bom estado, e **somente vídeos acima de 1 minuto**
são pagos. Por isso todos os packs deste repo miram 65–80 segundos.

## 3. O risco que mata o plano inteiro

Em julho de 2026 o YouTube renomeou a política de "conteúdo repetitivo" para
**"conteúdo inautêntico"**, deixando explícito que inclui conteúdo produzido
em massa. A detecção passou a ser **em nível de canal**, não vídeo a vídeo.
Em janeiro de 2026 houve uma varredura em que canais monetizados grandes foram
removidos do programa de uma vez, sem a escada habitual de avisos.

O alvo descrito é exatamente o que este repositório produz se usado de forma
ingênua: template rodando em escala, clipes reciclados, slideshow sem
narrativa, roteiro lido sem contribuição humana.

A linha que o YouTube traça é se **um humano acrescenta criatividade e valor
genuínos**. Não é "usou IA ou não" — é "isto é um template rodando sozinho ou
alguém pensou neste vídeo".

Por isso a camada `growth/` não gera "10 vídeos sobre finanças". Ela força um
ângulo editorial diferente por vídeo, exige número ou mecanismo específico em
cada roteiro, alterna vozes e ritmo visual, e mantém histórico para não repetir
pauta. Isso reduz o risco. **Não elimina.** O que elimina é você revisar, ter
opinião e cortar o que ficou genérico antes de publicar.

## 4. O ranking honesto de como ganhar dinheiro com isto

Da maior para a menor expectativa de retorno:

**1º — Vender a produção como serviço.** Comércio local, clínica, corretor,
academia: todos precisam de vídeo vertical semanal e nenhum quer produzir.
Um cliente pagando R$ 1.000–2.000/mês por 20 vídeos rende mais que qualquer
cenário de anúncio nesta lista, começa no primeiro mês, e não depende de
algoritmo nem de limite de seguidores. É por isso que o pack
`digital-marketing` existe: ele atrai exatamente quem compra isso.

**2º — Afiliados.** Sem limite mínimo de seguidores, funciona desde o vídeo 1.
Uma indicação de corretora ou SaaS paga de US$ 50 a 200 por conversão, e SaaS
costuma pagar comissão recorrente. Três conversões por mês superam centenas de
milhares de views em Shorts.

**3º — Geração de leads.** Vale onde o ticket é alto: imobiliário e B2B. Um
único lead qualificado para corretor ou consultoria vale mais que um ano de
anúncio nesse tamanho de canal.

**4º — TikTok Creator Rewards.** Realista, mas só depois de 10 mil seguidores
e 100 mil views mensais. Trate como bônus, não como plano.

**5º — YouTube Shorts.** Não construa nada em cima disso. Use o Shorts para
crescer audiência e empurrar para vídeo longo, afiliado ou serviço.

## 5. Expectativa realista de prazo

- **Mês 1–2:** nenhum dinheiro de anúncio. Você está calibrando nicho, voz e
  formato. Aqui já dá para faturar vendendo produção para um cliente local.
- **Mês 3–6:** primeiras conversões de afiliado, se houver link e chamada clara
  em todo vídeo. Talvez o limite do TikTok.
- **Mês 6–12:** se um canal pegou tração, YPP vira possível — e é quando vale
  migrar para vídeo longo 16:9, onde o CPM alto do nicho finalmente se aplica.

Quem promete resultado mais rápido que isso está vendendo curso.

## 6. As regras que não valem a pena quebrar

- **Divulgue conteúdo sintético** quando a plataforma pedir. Omitir é o tipo de
  coisa que tira monetização retroativamente.
- **Não publique o mesmo vídeo nas duas plataformas sem adaptar.** O TikTok
  penaliza marca d'água e conteúdo não original.
- **Nunca invente estatística, estudo ou preço.** Os prompts dos packs proíbem
  isso explicitamente, porque um número inventado destrói o canal quando alguém
  confere.
- **Saúde e finanças são YMYL.** Os packs `health-longevity` e `real-estate`
  já obrigam a dizer que é conteúdo educativo, não aconselhamento. Não remova
  essa instrução do prompt.
- **Publique menos do que você é capaz de gerar.** Este repo consegue 30 vídeos
  por dia. Um canal que publica 30 vídeos por dia é a definição de conteúdo
  produzido em massa. 1 a 3 por dia, revisados, é o que sobrevive.

## 7. Dois mercados ao mesmo tempo

A decisão foi rodar inglês e português em paralelo, com packs separados, para
comparar com dado real em vez de estimativa. Cada pack em inglês ganhou um par
em pt-BR com o id terminado em `-pt` — são nove pares, do `ufo-sightings-pt` ao
`b2b-software-pt`, mesmo formato e mesma duração, mercado diferente.

### O que o mercado brasileiro paga

Menos, e não é diferença de margem. Estes são os CPMs escritos em
`[economics]` nos próprios arquivos, cada pack ao lado do seu par:

| Pack | Inglês | pt-BR |
|---|---|---|
| `personal-finance` | US$ 15–30 | US$ 3–7 |
| `ai-tools` | US$ 8–20 | US$ 2–5 |
| `b2b-software` | US$ 12–18 | US$ 3–7 |
| `digital-marketing` | US$ 12–18 | US$ 2–5 |
| `health-longevity` | US$ 10–18 | US$ 1,50–4 |
| `real-estate` | US$ 10–16 | US$ 2,50–5 |
| `ufo-sightings` | US$ 3–8 | US$ 0,80–2,50 |
| `free-theme` | US$ 0,50–8 | US$ 0,20–4 |
| `ghost-stories` | US$ 2,50–7 | US$ 0,70–2,20 |

**Não existe um multiplicador único, e quem usar um está arredondando nove
pares diferentes em um número só.** A razão varia de pack para pack e até entre
as duas pontas da mesma faixa: vai de cerca de um sétimo (`health-longevity`,
ponta de baixo) a cerca de metade (`free-theme`, ponta de cima), com a maioria
caindo entre um quarto e um terço. `python -m growth niches` imprime a tabela
atual com o RPM estimado ao lado.

Vale a ressalva da seção 1: esses números são teto de vídeo longo, não o que um
Short paga. Um Short em português está sendo comparado com um Short em inglês —
os dois em centavos, um em menos centavos que o outro.

### A concorrência não compensa isso, e os packs não dizem que compensa

O argumento a favor do português é que a prateleira é menos disputada: menos
gente produzindo sobre esses assuntos nessa língua, então o mesmo vídeo teria
mais chance de ser visto. É um motivo razoável para testar. **Não é um crédito
que já dá para contabilizar**, e os arquivos deixam isso explícito:

- Nenhum dos nove packs em pt-BR registra `competition` menor que o par em
  inglês.
- Oito registram exatamente o mesmo valor.
- Um registra pior: `ai-tools` está `medium` em inglês e `ai-tools-pt` está
  `high`.

O único par marcado `low` dos dois lados é `b2b-software` — e ele já é `low` em
inglês, do lado em que o CPM é US$ 12–18 e não US$ 3–7. Se a prateleira em
português for mesmo mais vazia em algum desses nichos, isso vai aparecer na
retenção e nas views por vídeo depois de rodar. Até lá é hipótese, não desconto.

### O argumento que decide, e não é o RPM

Você revisa cada vídeo antes de publicar. **Revisar é muito mais fácil na sua
própria língua, e essa diferença provavelmente pesa mais que a diferença de
CPM.**

Um roteiro em português com um fato errado, uma atribuição que não fecha, uma
frase que soou de vendedor, um trecho que está tecnicamente certo e mesmo assim
soa falso: você pega na primeira leitura, sem esforço. O mesmo defeito em inglês
passa. Não é questão de saber inglês — revisar não é entender, é perceber que
alguma coisa está errada antes de saber o quê, e isso só acontece na língua em
que você pensa.

É um argumento de qualidade, não de receita. E qualidade aqui não é estética: é
o critério da seção 3. Um vídeo genérico publicado sai mais caro que um CPM
baixo, porque a detecção é em nível de canal — o vídeo ruim não cai sozinho,
ele leva o canal junto.

### Dobrar de mercado dobra a revisão, não o render

Render é barato e não pede atenção: a máquina roda sozinha enquanto você faz
outra coisa. Dobrar de mercado não dobra nenhum custo que importe.

O que dobra é a fila de vídeo esperando um humano, e o humano é um só.

**Consequência prática: o limite de 1 a 3 por dia da seção 6 é o total, não por
mercado.** Dois mercados a três vídeos cada não são seis vídeos revisados — são
três revisados e três publicados no escuro, que é exatamente o material que a
seção 3 descreve. Se hoje você revisa três por dia, com dois mercados isso vira
dois em um e um no outro, ou um dia de cada. A máquina não é o gargalo. Você é.

E como o mercado em inglês é o mais difícil de revisar, é também o que mais
perde quando a atenção é dividida.

### O que medir, e quando os números começam a valer

Nos primeiros meses não dá para comparar RPM: nenhum dos dois canais está
monetizado, pelos limites da seção 2. O que existe antes do dinheiro, medido
separado por mercado:

- **Retenção média** — quanto do vídeo as pessoas assistem. É o número que
  prevê todos os outros e o primeiro a reagir.
- **Views por vídeo, na mediana.** Não na média nem no total: um vídeo que
  estourou distorce os dois, e é o jeito mais comum de se enganar sozinho.
- **Seguidores por vídeo**, que é o que aproxima dos limites da seção 2.
- **Cliques em link de afiliado por mil views** — a seção 4 coloca afiliado
  acima de anúncio, então é a métrica de receita que aparece cedo.
- **Quantos vídeos você cortou na revisão, por língua.** É o único número que
  mede o argumento acima, e você tem ele desde o primeiro dia.

Prazo: **20 a 30 vídeos publicados por mercado antes de a mediana dizer alguma
coisa.** Vídeo curto é distribuição de cauda — poucos vídeos carregam quase
tudo, e amostra pequena é ruído. No ritmo revisado, isso dá algo entre dois e
três meses. Antes disso você tem impressão, não dado. E conferir o painel toda
semana não antecipa nada: só aumenta a chance de você matar o mercado certo por
causa de uma semana ruim.

### Compare o mesmo vídeo, não vídeos diferentes

Comparar dois mercados com conteúdos diferentes não compara nada. Se o canal em
português for melhor, você não sabe se foi o mercado ou se o assunto era melhor.

Dá para rodar o mesmo tema nas duas línguas. O assunto entra solto — `/tema` no
Telegram, `--theme` no terminal — e existem os dois packs de tema livre, um em
cada língua:

```bash
python -m growth run free-theme    --theme "naufrágios sem explicação" --count 1
python -m growth run free-theme-pt --theme "naufrágios sem explicação" --count 1
```

Mesmo assunto, mesmo formato, mesma duração: a diferença que sobrar é do
mercado. É a única versão dessa comparação que vale alguma coisa, e é barata —
são dois renders, não uma estratégia.

Uma observação de operação: o `/tema` do Telegram roda o pack `free-theme`, que
narra em inglês. O lado em português do mesmo assunto sai pelo terminal, com
`free-theme-pt`.
