# Correção do CNES e estabelecimento no Radar SUS

## O que corrige
- O coletor passa a buscar o texto integral das publicações oficiais do DOU, além do resumo no CONASEMS.
- A pesquisa por palavras-chave é feita também no texto do ato, ajudando a identificar códigos CNES e termos que não aparecem no resumo.
- O CNES 2409194 é resolvido para HOSPITAL DR LUIZ ANTONIO, NATAL/RN.
- A tabela e a página individual passam a ter um campo próprio `estabelecimento`.
- O histórico antigo sem a nova coluna continua aceito, e o sincronizador não apaga um estabelecimento existente quando a planilha não o informa.
- A busca continua ignorando sábados e domingos e mantém a janela de IDs atualmente usada pelo monitor.

## Como aplicar
1. No Supabase SQL Editor, execute `correcao_cnes_estabelecimento.sql`.
2. No GitHub, substitua estes quatro arquivos pelos correspondentes deste pacote:
   - `monitor_conasems/monitor/monitor.py`
   - `monitor_conasems/monitor/sincronizar_supabase.py`
   - `monitor_conasems/assets/app.js`
   - `monitor_conasems/assets/detail.js`
3. Faça Commit changes.
4. Rode o workflow manualmente para o período que contém a portaria, usando o modo `periodo`.
5. Confira o registro `id=408` no Radar e no Supabase.

## Nota
O arquivo SQL também atualiza o registro `id=408` com os dados identificados para o CNES 2409194. A atualização automática futura deve voltar a preencher os dados a partir do texto do DOU.
