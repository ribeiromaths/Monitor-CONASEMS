function esc(s){
  return String(s??"").replace(/[&<>"']/g,m =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m])
  );
}
function cats(s){
  return String(s||"").split(";").map(x=>x.trim()).filter(Boolean);
}
function relClass(r){
  return r==="ALTA" ? "high" : r==="BAIXA" ? "low" : "med";
}
function formatDate(iso){
  if(!iso) return "—";
  const [y,m,d]=String(iso).slice(0,10).split("-");
  return y&&m&&d ? `${d}/${m}/${y}` : iso;
}
function getId(){
  return new URLSearchParams(location.search).get("id");
}
async function getPublication(){
  const id=getId();
  if(!id) return null;

  try{
    const client=window.supabase.createClient(
      window.RADAR_SUPABASE.url,
      window.RADAR_SUPABASE.publishableKey
    );

    const {data,error}=await client
      .from("publicacoes")
      .select("*")
      .eq("id", id)
      .maybeSingle();

    if(error) throw error;
    if(data) return data;
  }catch(err){
    console.warn("Falha no Supabase:",err);
  }

  const fallback=await fetch("data/portarias.json").then(r=>r.json());
  return fallback.find(x=>String(x.id)===String(id)) || null;
}

async function init(){
  const r=await getPublication();

  if(!r){
    document.title="Publicação não encontrada | Radar SUS";
    document.getElementById("detail").innerHTML=
      "<div class='notfound'><h1>Publicação não encontrada</h1><p>O endereço pode estar incompleto ou a publicação não está mais disponível.</p></div>";
    return;
  }

  document.title=(r.titulo||"Portaria")+" | Radar SUS";

  const tags=cats(r.categoria).map(c=>
    `<span class="tag">${esc(c)}</span>`
  ).join("");

  const analise=r.analise && r.analise.trim()
    ? `<div class="analysis"><h2>ANÁLISE INSTITUCIONAL</h2><p>${esc(r.analise)}</p></div>`
    : `<div class="analysis"><h2>ANÁLISE INSTITUCIONAL</h2><p>Campo reservado para a análise gerencial. A área de gestão será disponibilizada na próxima etapa.</p></div>`;

  document.getElementById("detail").innerHTML=`
    <article class="detail-card">
      <div class="detail-meta">
        <span class="badge ${relClass(r.relevancia)}">${esc(r.relevancia||"SEM CLASSIFICAÇÃO")}</span>
        <span class="date">${esc(formatDate(r.data))}</span>
        <span class="date">· ${esc(r.fonte||"CONASEMS")}</span>
      </div>

      <h1>${esc(r.titulo||r.portaria||"Publicação")}</h1>
      <div class="tags">${tags}</div>

      <div class="detail-summary">${esc(r.resumo||"Não há resumo disponível.")}</div>

      <div class="info-grid">
        <div class="info"><label>Portaria / ato</label><div>${esc(r.portaria||"Não identificado")}</div></div>
        <div class="info"><label>Órgão</label><div>${esc(r.orgao||"—")}</div></div>
        <div class="info"><label>Município</label><div>${esc(r.municipio||"Não identificado")}</div></div>
        <div class="info"><label>CNES</label><div>${esc(r.cnes||"Não identificado")}</div></div>
        <div class="info"><label>Valores</label><div>${esc(r.valores||"Não identificado")}</div></div>
        <div class="info"><label>Palavras encontradas</label><div>${esc(r.palavras||"—")}</div></div>
      </div>

      ${analise}
      ${r.link ? `<a class="official" href="${esc(r.link)}" target="_blank" rel="noopener noreferrer">Abrir publicação oficial ↗</a>` : ""}
    </article>`;
}

init().catch(()=>document.getElementById("detail").innerHTML=
  "<div class='notfound'><h1>Erro ao carregar publicação</h1></div>");
