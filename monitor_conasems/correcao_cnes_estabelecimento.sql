-- Radar SUS: adiciona o campo de estabelecimento e corrige a publicação informada.
begin;

alter table public.publicacoes
  add column if not exists estabelecimento text;

create index if not exists publicacoes_estabelecimento_idx
  on public.publicacoes (estabelecimento);

-- Preserva o estabelecimento já salvo quando uma carga antiga não informa esse campo.
create or replace function public.preservar_estabelecimento_existente()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'UPDATE' and (new.estabelecimento is null or btrim(new.estabelecimento) = '') then
    new.estabelecimento := old.estabelecimento;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_preservar_estabelecimento_existente on public.publicacoes;
create trigger trg_preservar_estabelecimento_existente
before update on public.publicacoes
for each row execute function public.preservar_estabelecimento_existente();

-- Corrige o registro citado na página do Radar SUS.
update public.publicacoes
set cnes = '2409194',
    estabelecimento = 'HOSPITAL DR LUIZ ANTONIO',
    municipio = 'NATAL/RN'
where id = 408;

commit;

select id, data, titulo, cnes, estabelecimento, municipio, link
from public.publicacoes
where id = 408;
