let DATA = [], filtered = [], page = 1;
const PER_PAGE = 10;
const $ = id => document.getElementById(id);
const relRank = {ALTA:3, MEDIA:2, BAIXA:1};

function esc(s){
  return String(s ?? "").replace(/[&<>"']/g, m => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]
  ));
}
function categories(r){
  return String(r.categoria || "").split(";").map(x => x.trim()).filter(Boolean);
}
function relClass(r){
  return r === "ALTA" ? "high" : r === "BAIXA" ? "low" : "med";
}
function niceDate(s){ return s || "—"; }
function formatDateISO(iso){
  if(!iso) return "—";
  const [y,m,d] = String(iso).slice(0,10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}
function mapDbRow(r){
  return {
    ...r,
    id: String(r.id),
    dataISO: r.data ? String(r.data).slice(0,10) : "",
    data: formatDateISO(r.data),
    orgao: r.orgao || "",
    categoria: r.categoria || "",
    relevancia: r.relevancia || "",
    palavras: r.palavras || "",
    portaria: r.portaria || "",
    cnes: r.cnes || "",
    municipio: r.municipio || "",
    valores: r.valores || "",
    titulo: r.titulo || "",
    resumo: r.resumo || "",
    link: r.link || "",
    coleta: r.coleta_em || "",
    analise: r.analise || ""
  };
}

async function fetchAllSupabase(){
  const client = window.supabase.createClient(
    window.RADAR_SUPABASE.url,
    window.RADAR_SUPABASE.publishableKey
  );

  const all = [];
  const pageSize = 1000;
  let from = 0;

  while(true){
    const {data, error} = await client
      .from("publicacoes")
      .select("*")
      .order("data", {ascending:false})
      .range(from, from + pageSize - 1);

    if(error) throw error;
    if(!data || !data.length) break;

    all.push(...data);
    if(data.length < pageSize) break;
    from += pageSize;
  }

  return all.map(mapDbRow);
}

async function fetchFallback(){
  return fetch("data/portarias.json").then(r => {
    if(!r.ok) throw new Error("Fallback não disponível.");
    return r.json();
  });
}

function uniqueCats(){
  const set = new Set();
  DATA.forEach(r => categories(r).forEach(c => set.add(c)));
  $("category").innerHTML = '<option value="">Todas</option>';
  [...set].sort((a,b) => a.localeCompare(b,"pt-BR")).forEach(c =>
    $("category").insertAdjacentHTML("beforeend", `<option>${esc(c)}</option>`)
  );
}

function updateStats(){
  const all = DATA.length;
  const high = DATA.filter(x => x.relevancia === "ALTA").length;
  const dates = [...new Set(DATA.map(x => x.dataISO).filter(Boolean))];

  $("stats").innerHTML = `
    <div class="stat"><div class="num">${all}</div><div class="label">Publicações monitoradas</div></div>
    <div class="stat"><div class="num">${high}</div><div class="label">Alta relevância</div></div>
    <div class="stat"><div class="num">${dates.length}</div><div class="label">Dias com publicações</div></div>
    <div class="stat"><div class="num">${new Set(DATA.flatMap(x => categories(x))).size}</div><div class="label">Categorias identificadas</div></div>`;

  const latest = DATA.map(x => x.dataISO).filter(Boolean).sort().pop();
  if(latest) $("lastDate").textContent = formatDateISO(latest);
}

function matches(r){
  const q = $("q").value.trim().toLowerCase();
  if(q){
    const hay = [
      r.titulo,r.resumo,r.portaria,r.orgao,r.municipio,
      r.cnes,r.valores,r.palavras,r.categoria
    ].join(" ").toLowerCase();
    if(!hay.includes(q)) return false;
  }

  const s = $("dateStart").value, e = $("dateEnd").value;
  if(s && r.dataISO < s) return false;
  if(e && r.dataISO > e) return false;

  const c = $("category").value;
  if(c && !categories(r).includes(c)) return false;

  const rel = $("relevance").value;
  if(rel && r.relevancia !== rel) return false;

  return true;
}

function sortData(arr){
  const mode = $("sort").value;
  return arr.sort((a,b) => {
    if(mode === "relevance")
      return (relRank[b.relevancia]||0) - (relRank[a.relevancia]||0)
        || b.dataISO.localeCompare(a.dataISO);
    if(mode === "title")
      return a.titulo.localeCompare(b.titulo,"pt-BR");
    return b.dataISO.localeCompare(a.dataISO);
  });
}

function render(){
  filtered = sortData(DATA.filter(matches));
  page = Math.min(page, Math.max(1, Math.ceil(filtered.length/PER_PAGE)));

  $("resultCount").textContent =
    `${filtered.length} ${filtered.length===1 ? "publicação" : "publicações"}`;

  $("sectionTitle").textContent =
    $("q").value || $("dateStart").value || $("dateEnd").value ||
    $("category").value || $("relevance").value
      ? "Resultados filtrados"
      : "Todas as publicações";

  const start = (page-1)*PER_PAGE;
  const rows = filtered.slice(start, start+PER_PAGE);

  $("cards").innerHTML = rows.map(r => {
    const cats = categories(r).slice(0,3)
      .map(c => `<span class="cat">${esc(c)}</span>`).join("");

    return `<a class="card" href="portaria.html?id=${encodeURIComponent(r.id)}">
      <div class="card-top">
        <span class="badge ${relClass(r.relevancia)}">${esc(r.relevancia || "SEM CLASSIFICAÇÃO")}</span>
        ${cats}
        <span class="date">${esc(niceDate(r.data))}</span>
      </div>
      <h3>${esc(r.titulo || r.portaria || "Publicação sem título")}</h3>
      <p>${esc(r.resumo || "Sem resumo disponível.")}</p>
      <div class="card-bottom">
        <span>${esc(r.portaria || r.orgao || "CONASEMS")}</span>
        <span class="arrow">Ver detalhes →</span>
      </div>
    </a>`;
  }).join("");

  $("empty").classList.toggle("hidden", rows.length > 0);
  renderPagination();
}

function renderPagination(){
  const total = Math.ceil(filtered.length/PER_PAGE);
  const box = $("pagination");

  if(total <= 1){ box.innerHTML = ""; return; }

  let html = "";
  for(let i=1; i<=total; i++){
    if(i===1 || i===total || Math.abs(i-page)<=2)
      html += `<button class="${i===page?"active":""}" onclick="goPage(${i})">${i}</button>`;
  }
  box.innerHTML = html;
}

window.goPage = n => {
  page = n;
  render();
  window.scrollTo({top:500, behavior:"smooth"});
};

function clearFilters(){
  ["q","dateStart","dateEnd"].forEach(id => $(id).value="");
  $("category").value = "";
  $("relevance").value = "";
  $("sort").value = "date";
  page = 1;
  render();
}

async function init(){
  try {
    DATA = await fetchAllSupabase();
    if(!DATA.length) throw new Error("Nenhuma publicação no banco.");
  } catch(err) {
    console.warn("Supabase indisponível; usando dados locais.", err);
    DATA = await fetchFallback();
  }

  uniqueCats();
  updateStats();

  ["q","dateStart","dateEnd","category","relevance","sort"]
    .forEach(id => $(id).addEventListener(id==="q" ? "input" : "change", () => {
      page = 1;
      render();
    }));

  $("clear").onclick = clearFilters;
  render();
}

init().catch(e => {
  $("cards").innerHTML =
    `<div class="empty"><h3>Não foi possível carregar os dados</h3>
     <p>Verifique a conexão com o banco do Radar SUS.</p></div>`;
});
