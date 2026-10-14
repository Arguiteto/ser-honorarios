/**
 * PONTE SISGRAN → CHAOS
 * ---------------------------------------------------------------------------
 * O portal SISGRAN 2026 (PLANURB/PMCG) não libera leitura vinda de outros sites,
 * então o Chaos (no GitHub Pages) não consegue consultar direto pelo navegador.
 * Também baixa as pranchas em PDF da Mapoteca para a Planta do mapa (?pdf=...).
 * Este script, publicado como "App da Web" na conta Google do escritório, faz a
 * consulta no lugar do navegador e devolve o resultado para o Chaos.
 *
 * Só repassa consultas de leitura (GET) ao endereço sisgranmaps.campogrande.ms.gov.br,
 * nos caminhos da API pública listados em PERMITIDOS. Nada é gravado e nenhum dado
 * do escritório sai daqui.
 *
 * Como publicar (uma vez só):
 *  1. script.google.com → Novo projeto → apague o conteúdo e cole este arquivo inteiro.
 *  2. Dê o nome "Ponte SISGRAN" e salve (Ctrl+S).
 *  3. Implantar → Nova implantação → tipo "App da Web".
 *       Executar como: Eu
 *       Quem pode acessar: Qualquer pessoa
 *  4. Autorize (o Google pede permissão para "conectar a um serviço externo").
 *  5. Copie a URL que termina em /exec e coloque em SISGRAN_PONTE no index.html.
 */

const BASE = 'https://sisgranmaps.campogrande.ms.gov.br';
const PERMITIDOS = [
  /^\/sisgran\/api\/v1\/maps\/wms\?/,
  /^\/sisgran\/api\/v1\/maps\/config$/,
  /^\/sisgran\/api\/v1\/territory\/[A-Za-z0-9_\-\/%.?=&]+$/,
  /^\/sisgran\/api\/v1\/search\?/
];
// Pranchas da Mapoteca (PDF) no CDN da prefeitura: o CDN não deixa o navegador baixar direto (CORS),
// então a ponte baixa e devolve em base64. Só aceita PDF das mídias públicas do portal (site 24 = SEMADES/SEMADUR).
const PDF_OK = /^https:\/\/cdn\.campogrande\.ms\.gov\.br\/portal\/prod\/uploads\/sites\/24\/[^?#]+\.pdf$/i;
const PDF_MAX = 30 * 1024 * 1024;
const MAX_PEDIDOS = 25;
const CACHE_SEG = 6 * 60 * 60; // 6 horas: o mesmo lote consultado de novo sai na hora

// GET: ?u=/sisgran/api/v1/...&g=1   (u pode repetir; g=0 tira a geometria da resposta)
function doGet(e) {
  const p = (e && e.parameters) || {};
  const us = [].concat(p.u || []);
  const gs = [].concat(p.g || []);
  if (p.pdf) return saida(baixarPdf(String([].concat(p.pdf)[0] || '')));
  if (!us.length) return saida({ ok: true, servico: 'ponte-sisgran', versao: 2, pdf: true });
  return saida(executar(us.map(function (u, i) { return { u: u, g: gs[i] !== '0' }; })));
}

// POST (o Chaos usa este): corpo JSON {"p":[{"u":"/sisgran/api/v1/...","g":true}, ...]}
function doPost(e) {
  let corpo = {};
  try { corpo = JSON.parse((e && e.postData && e.postData.contents) || '{}'); }
  catch (err) { return saida({ erro: 'corpo inválido' }); }
  const pedidos = Array.isArray(corpo.p) ? corpo.p : [];
  return saida(executar(pedidos.map(function (x) { return { u: String(x && x.u || ''), g: !(x && x.g === false) }; })));
}

function executar(pedidos) {
  if (!pedidos.length) return { r: [] };
  if (pedidos.length > MAX_PEDIDOS) return { erro: 'máximo de ' + MAX_PEDIDOS + ' consultas por chamada' };
  const cache = CacheService.getScriptCache();
  const r = new Array(pedidos.length);
  const buscar = [], posicao = [];
  pedidos.forEach(function (x, i) {
    if (!PERMITIDOS.some(function (rx) { return rx.test(x.u); })) { r[i] = { s: 400, erro: 'caminho não permitido' }; return; }
    const chave = chaveCache(x.u, x.g);
    const guardado = cache.get(chave);
    if (guardado) { r[i] = { s: 200, j: JSON.parse(guardado), c: 1 }; return; }
    buscar.push({ url: BASE + x.u, method: 'get', muteHttpExceptions: true, followRedirects: true, headers: { Accept: 'application/json' } });
    posicao.push(i);
  });
  if (buscar.length) {
    let respostas = [];
    try { respostas = UrlFetchApp.fetchAll(buscar); }
    catch (err) { posicao.forEach(function (i) { r[i] = { s: 502, erro: String(err && err.message || err) }; }); return { r: r }; }
    respostas.forEach(function (resp, k) {
      const i = posicao[k], x = pedidos[i];
      const s = resp.getResponseCode();
      const txt = resp.getContentText('UTF-8');
      let j = null;
      try { j = JSON.parse(txt); } catch (err) { j = null; }
      if (s !== 200 || j === null) { r[i] = { s: s, erro: 'resposta não-JSON', t: txt.slice(0, 300) }; return; }
      if (!x.g) j = semGeometria(j);
      r[i] = { s: 200, j: j };
      try {
        const str = JSON.stringify(j);
        if (str.length < 95000) cache.put(chaveCache(x.u, x.g), str, CACHE_SEG);
      } catch (err) {}
    });
  }
  return { r: r };
}

// GET ?pdf=<url da prancha> → {ok:true, n:<bytes>, b64:"..."}
function baixarPdf(url) {
  if (!PDF_OK.test(url)) return { ok: false, erro: 'endereço não permitido' };
  try {
    const resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
    const s = resp.getResponseCode();
    if (s !== 200) return { ok: false, s: s, erro: 'a prefeitura respondeu HTTP ' + s };
    const bytes = resp.getContent();
    if (bytes.length > PDF_MAX) return { ok: false, erro: 'arquivo grande demais (' + Math.round(bytes.length / 1048576) + ' MB)' };
    return { ok: true, n: bytes.length, b64: Utilities.base64Encode(bytes) };
  } catch (err) {
    return { ok: false, erro: String(err && err.message || err) };
  }
}

function semGeometria(j) {
  if (j && Array.isArray(j.features)) j.features.forEach(function (f) { if (f) f.geometry = null; });
  return j;
}

function chaveCache(u, g) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, (g ? 'g1|' : 'g0|') + u);
  return 'sg' + Utilities.base64EncodeWebSafe(bytes);
}

function saida(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// Para testar dentro do editor do Apps Script (botão Executar): deve mostrar a zona ambiental do Centro.
function testar() {
  const u = '/sisgran/api/v1/maps/wms?service=WMS&version=1.1.1&request=GetFeatureInfo'
    + '&layers=sisgran:vw_pd_zonas_ambientais&query_layers=sisgran:vw_pd_zonas_ambientais&styles='
    + '&srs=EPSG:4326&bbox=-54.6155,-20.4635,-54.6145,-20.4625&width=101&height=101&x=50&y=50'
    + '&info_format=application/json&feature_count=5';
  Logger.log(JSON.stringify(executar([{ u: u, g: false }])));
}

// Teste da prancha (botão Executar com "testarPdf"): deve mostrar ok:true e o tamanho do PDF.
function testarPdf() {
  const r = baixarPdf('https://cdn.campogrande.ms.gov.br/portal/prod/uploads/sites/24/2026/04/CEL.-ANTONINO-Folha-01.pdf');
  Logger.log(JSON.stringify({ ok: r.ok, n: r.n, erro: r.erro }));
}
