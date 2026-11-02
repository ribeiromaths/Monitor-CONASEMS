-- RADAR SUS — CHECKLIST DE VALIDAÇÃO DO BANCO
-- Execute no Supabase > SQL Editor, uma consulta por vez.

-- 1) Confirma se a coluna ESTABELECIMENTO existe.
select column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name = 'publicacoes'
  and column_name = 'estabelecimento';

-- Resultado esperado: 1 linha, column_name = estabelecimento, data_type = text.


-- 2) Confere a portaria corrigida (registro 408).
select
    id,
    data,
    titulo,
    cnes,
    estabelecimento,
    municipio,
    link
from public.publicacoes
where id = 408;

-- Resultado esperado:
-- cnes = 2409194
-- estabelecimento = HOSPITAL DR LUIZ ANTONIO
-- municipio = NATAL/RN


-- 3) Total de publicações no banco.
select count(*) as total_publicacoes
from public.publicacoes;


-- 4) Quantos registros têm CNES preenchido.
select count(*) as registros_com_cnes
from public.publicacoes
where nullif(trim(cnes), '') is not null;


-- 5) Quantos registros têm estabelecimento preenchido.
select count(*) as registros_com_estabelecimento
from public.publicacoes
where nullif(trim(estabelecimento), '') is not null;


-- 6) Busca todos os registros que citam o CNES 2409194.
select
    id,
    data,
    titulo,
    cnes,
    estabelecimento,
    municipio,
    link
from public.publicacoes
where cnes ilike '%2409194%'
   or coalesce(estabelecimento, '') ilike '%LUIZ ANTONIO%'
order by data desc;


-- 7) Confere se existem LINKS duplicados.
-- Resultado esperado: nenhuma linha.
select link, count(*) as quantidade
from public.publicacoes
where link is not null
group by link
having count(*) > 1;


-- 8) Amostra das publicações mais recentes.
select
    id,
    data,
    portaria,
    titulo,
    relevancia,
    cnes,
    estabelecimento,
    municipio,
    link
from public.publicacoes
order by data desc, id desc
limit 25;
