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
