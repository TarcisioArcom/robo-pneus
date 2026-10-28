# Robô de preços de pneus 185/65 R15 (novos, sem remold)

Pesquisa os preços de segunda a sexta, descarta remold/recapado/usado, calcula o preço por pneu
(kits de 2 ou 4 são divididos) envia o comparativo por e-mail com o histórico das pesquisas anteriores (e planilhas anexas) e publica uma versão completa no GitHub Pages, com histórico em CSV.

## Estrutura

```
robo.py                          robô (coleta, filtro, histórico, relatório, e-mail, Telegram)
config.json                      medida, termo de busca, palavras proibidas, faixa de preço, lojas VTEX
.github/workflows/robo-pneus.yml agendamento e execução no GitHub Actions
data/historico.csv               histórico de todas as coletas (gerado)
docs/index.html                  relatório comparativo (gerado, publicado no Pages)
```

## Passo a passo

1. **Crie o repositório** no GitHub (pode ser privado, mas o GitHub Pages gratuito exige repositório público;
   em conta Pro/Team o Pages funciona com privado) e envie estes arquivos.
2. **Crie uma conta na SerpApi** (serpapi.com). O plano gratuito cobre uma busca por dia útil com folga.
   Copie a API key.
3. Em **Settings → Secrets and variables → Actions → New repository secret**, crie:
   - `SERPAPI_KEY` (obrigatório)
   - `EMAIL_USUARIO`: seu e-mail do Yahoo, que será o remetente (ex.: `nome@yahoo.com.br`)
   - `EMAIL_SENHA_APP`: senha de app gerada no Yahoo (não é a senha normal; veja abaixo)
   - `EMAIL_DESTINO`: quem recebe; vários separados por vírgula. Se não criar, envia para o próprio `EMAIL_USUARIO`
   - `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` (opcional, para receber o top 5 no celular; crie o bot com o @BotFather)
   - `ML_ACCESS_TOKEN` (opcional, token da API do Mercado Livre; ele expira a cada 6 h, então só vale se você
     tiver um processo de renovação. O Google Shopping já traz muitas ofertas do Mercado Livre.)
4. Em **Settings → Actions → General → Workflow permissions**, marque *Read and write permissions*.
5. Em **Settings → Pages**, escolha *Deploy from a branch*, branch `main`, pasta `/docs`.
6. Em **Actions → Robô de preços de pneus → Run workflow**, rode uma vez para testar.
   O relatório fica em `https://SEU_USUARIO.github.io/NOME_DO_REPO/`.

## Senha de app do Yahoo

O GitHub não envia e-mails personalizados; o robô entra no SMTP do Yahoo e envia em seu nome.
O Yahoo não aceita a senha normal para isso, só uma senha de app:

1. Entre em login.yahoo.com → foto → **Informações da conta → Segurança da conta**.
2. Clique em **Gerar e gerenciar senhas de app** (se pedir, ative antes a verificação em duas etapas).
3. Dê o nome `Robo pneus`, clique em **Gerar senha** e copie os 16 caracteres.
4. Cole no secret `EMAIL_SENHA_APP`.

Servidor usado: `smtp.mail.yahoo.com`, porta 465 (SSL). Para outro provedor, crie os secrets/variáveis `SMTP_HOST` e `SMTP_PORTA`.

## Agendamento: GitHub ou cron-job.org

O workflow já vem com `cron: "0 19 * * 1-5"` (16:00 de Brasília, seg a sex). O agendamento do GitHub pode
atrasar de minutos a mais de uma hora em horários de pico. Para horário exato, use o cron-job.org:

1. No GitHub: **Settings (da conta) → Developer settings → Personal access tokens → Fine-grained tokens**.
   Acesso só a este repositório, permissão **Actions: Read and write**. Copie o token.
2. No cron-job.org, crie um job:
   - URL: `https://api.github.com/repos/SEU_USUARIO/NOME_DO_REPO/actions/workflows/robo-pneus.yml/dispatches`
   - Agendamento: personalizado, dias da semana seg a sex, 16:00, fuso `America/Sao_Paulo`
   - Em *Advanced*: método **POST**, corpo `{"ref":"main"}`, e os cabeçalhos
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer SEU_TOKEN`
     - `X-GitHub-Api-Version: 2022-11-28`
     - `Content-Type: application/json`
   - Resposta esperada: **HTTP 204**.
3. Apague o bloco `schedule:` do workflow para não rodar duas vezes. (Se rodar duas vezes no mesmo dia,
   o robô substitui a coleta do dia, sem duplicar o histórico.)

## Como o filtro funciona

- Só entra anúncio cujo título contenha a medida 185/65 aro 15 (aceita `185/65R15`, `185 65 15`, `185/65-15`).
- Sai qualquer título com as palavras de `palavras_proibidas` (remold, recapado, recauchutado, ressolado,
  reformado, usado, meia vida, câmara, roda etc.). Edite a lista no `config.json` se aparecer algo indesejado.
- Preço por unidade fora de `preco_minimo_unitario`/`preco_maximo_unitario` é descartado (pega acessórios
  e anúncios com preço errado).

## Adicionando lojas diretamente (VTEX)

Muitas lojas de pneus no Brasil usam a plataforma VTEX, que tem uma API pública de catálogo. Teste no navegador:

```
https://DOMINIO_DA_LOJA/api/catalog_system/pub/products/search?ft=185%2F65%20R15
```

Se voltar um JSON com produtos, adicione no `config.json`:

```json
"lojas_vtex": [
  {"nome": "Nome da Loja", "dominio": "www.dominiodaloja.com.br"}
]
```

## Histórico

Cada execução acrescenta as ofertas do dia em `data/historico.csv` (uma nova coleta no mesmo dia substitui a anterior).
O e-mail traz:
- o menor preço já registrado, com data, marca e loja;
- uma tabela com o menor preço de cada uma das últimas pesquisas (quantidade em `dias_historico_email` no `config.json`), com barra e variação;
- três anexos para Excel: ofertas do dia, `historico-resumo-por-dia.csv` (uma linha por pesquisa) e `historico-completo-ofertas.csv` (todas as ofertas já coletadas).

O relatório no GitHub Pages mostra o mesmo histórico, das últimas 60 pesquisas, além do gráfico.
O histórico começa a partir da primeira execução do robô; não é possível recuperar preços de dias anteriores a ela.
