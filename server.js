// Painel de Anúncios — servidor local
// Roda no computador, serve o player para a TV e recebe arquivos do celular na mesma rede.
const express = require('express');
const multer = require('multer');
const QRCode = require('qrcode');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PORT = Number(process.env.PORT) || 8080;
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const MEDIA_DIR = path.join(DATA_DIR, 'media');
const DB_FILE = path.join(DATA_DIR, 'anuncios.json');
const CONFIG_FILE = path.join(DATA_DIR, 'config.json');

fs.mkdirSync(MEDIA_DIR, { recursive: true });

// ---------- persistência simples em JSON ----------
function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}
function writeJson(file, data) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, file);
}

const config = readJson(CONFIG_FILE, {});
if (!config.token) {
  config.token = crypto.randomBytes(6).toString('hex');
  config.defaultImageSeconds = 10;
  writeJson(CONFIG_FILE, config);
}

let ads = readJson(DB_FILE, []);
const saveAds = () => writeJson(DB_FILE, ads);

// ---------- utilidades ----------
function lanIp() {
  const nets = os.networkInterfaces();
  const candidates = [];
  for (const name of Object.keys(nets)) {
    for (const n of nets[name] || []) {
      if (n.family === 'IPv4' && !n.internal) candidates.push(n.address);
    }
  }
  // prefere faixas de rede doméstica comuns
  return candidates.find(a => /^192\.168\./.test(a)) ||
         candidates.find(a => /^10\./.test(a)) ||
         candidates.find(a => /^172\.(1[6-9]|2\d|3[01])\./.test(a)) ||
         candidates[0] || 'localhost';
}
const baseUrl = () => `http://${lanIp()}:${PORT}`;

// Requisição que chegou pelo túnel público (cloudflared, ngrok…): nunca é tratada como local
const viaTunel = req => !!(req.headers['cf-connecting-ip'] || req.headers['x-forwarded-for'] || req.headers.forwarded);
function isLocal(req) {
  if (viaTunel(req)) return false;
  const ip = req.socket.remoteAddress || '';
  return ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1';
}
function requireToken(req, res, next) {
  const t = req.get('x-token') || req.query.k || (req.body && req.body.k);
  if (isLocal(req) || t === config.token) return next();
  res.status(401).json({ erro: 'Código de acesso inválido. Escaneie o QR code novamente.' });
}

// Ativo agora? (respeita data/hora de início e fim, e o liga/desliga)
function isActive(ad, now = Date.now()) {
  if (!ad.ativo) return false;
  if (ad.inicio && new Date(ad.inicio).getTime() > now) return false;
  if (ad.fim && new Date(ad.fim).getTime() < now) return false;
  return true;
}

const COR = /^#[0-9a-fA-F]{6}$/;
const ORIENTACOES = ['horizontal', 'vertical', 'vertical-esq'];
if (!ORIENTACOES.includes(config.orientacao)) config.orientacao = 'horizontal';

function sanitizeFields(body, ad) {
  if (body.titulo !== undefined) ad.titulo = String(body.titulo).slice(0, 120);
  if (body.inicio !== undefined) ad.inicio = body.inicio || null;
  if (body.fim !== undefined) ad.fim = body.fim || null;
  if (body.duracao !== undefined) {
    const d = Number(body.duracao);
    ad.duracao = Number.isFinite(d) && d > 0 ? Math.min(d, 3600) : 0; // 0 = vídeo inteiro / padrão
  }
  if (body.ativo !== undefined) ad.ativo = body.ativo === true || body.ativo === 'true' || body.ativo === 'on';
  // anúncios de texto
  if (body.texto !== undefined) ad.texto = String(body.texto).slice(0, 1000);
  if (body.corFundo !== undefined && COR.test(body.corFundo)) ad.corFundo = body.corFundo;
  if (body.corTexto !== undefined && COR.test(body.corTexto)) ad.corTexto = body.corTexto;
  if (body.tamanho !== undefined && ['P', 'M', 'G'].includes(body.tamanho)) ad.tamanho = body.tamanho;
  // legenda exibida sobre a imagem/vídeo (posts de redes sociais)
  if (body.legenda !== undefined) ad.legenda = String(body.legenda).slice(0, 300);
  return ad;
}

// ---------- upload ----------
const ALLOWED = /^(image\/(jpeg|png|gif|webp|bmp)|video\/(mp4|webm|ogg|quicktime|x-m4v))$/;
const IMG_EXT = /\.(jpe?g|png|gif|webp|bmp)$/i;
const VID_EXT = /\.(mp4|webm|ogv|mov|m4v)$/i;
const isVideo = f => f.mimetype.startsWith('video/') || VID_EXT.test(f.originalname);
const upload = multer({
  storage: multer.diskStorage({
    destination: MEDIA_DIR,
    filename: (req, file, cb) => {
      const ext = (path.extname(file.originalname) || '').toLowerCase().replace(/[^.a-z0-9]/g, '');
      cb(null, Date.now() + '-' + crypto.randomBytes(4).toString('hex') + ext);
    },
  }),
  limits: { fileSize: 1024 * 1024 * 1024 }, // 1 GB por arquivo
  fileFilter: (req, file, cb) => cb(null, ALLOWED.test(file.mimetype) || IMG_EXT.test(file.originalname) || VID_EXT.test(file.originalname)),
});

// ---------- app ----------
const app = express();
// Pelo túnel público só ficam acessíveis as mídias (para o Instagram baixar) e o webhook do WhatsApp
app.use((req, res, next) => {
  if (!viaTunel(req) || req.path.startsWith('/media/') || req.path === '/webhook/whatsapp') return next();
  res.status(404).send('Não encontrado');
});
app.use(express.json({ limit: '8mb', verify: (req, res, buf) => { req.rawBody = buf; } }));
app.use(express.static(path.join(__dirname, 'public')));
app.use('/media', express.static(MEDIA_DIR, { maxAge: '7d' }));

app.get('/', (req, res) => res.redirect(isLocal(req) ? '/admin.html' : '/tv.html'));
app.get('/tv', (req, res) => res.redirect('/tv.html'));
app.get('/admin', (req, res) => res.redirect('/admin.html'));
app.get('/enviar', (req, res) => res.redirect('/upload.html?k=' + encodeURIComponent(req.query.k || '')));

// Informações e QR codes para o painel (só no próprio computador ou com token)
app.get('/api/info', requireToken, async (req, res) => {
  const uploadUrl = `${baseUrl()}/upload.html?k=${config.token}`;
  const tvUrl = `${baseUrl()}/tv`;
  const adminUrl = `${baseUrl()}/admin.html?k=${config.token}`;
  res.json({
    uploadUrl, tvUrl, adminUrl,
    token: config.token,
    defaultImageSeconds: config.defaultImageSeconds,
    orientacao: config.orientacao,
    qrUpload: await QRCode.toDataURL(uploadUrl, { margin: 1, width: 320 }),
    qrAdmin: await QRCode.toDataURL(adminUrl, { margin: 1, width: 320 }),
  });
});

app.put('/api/config', requireToken, (req, res) => {
  const d = Number(req.body.defaultImageSeconds);
  if (Number.isFinite(d) && d > 0) config.defaultImageSeconds = Math.min(d, 3600);
  if (ORIENTACOES.includes(req.body.orientacao)) config.orientacao = req.body.orientacao;
  writeJson(CONFIG_FILE, config);
  res.json({ ok: true, defaultImageSeconds: config.defaultImageSeconds, orientacao: config.orientacao });
});

// Lista completa (painel)
app.get('/api/anuncios', requireToken, (req, res) => {
  const now = Date.now();
  res.json(ads.map(a => ({ ...a, ativoAgora: isActive(a, now) })));
});

// Playlist pública para a TV: só o que está ativo agora
app.get('/api/playlist', (req, res) => {
  const now = Date.now();
  res.set('Cache-Control', 'no-store');
  res.json({
    geradoEm: now,
    orientacao: config.orientacao,
    itens: ads.filter(a => isActive(a, now)).map(a => ({
      id: a.id,
      tipo: a.tipo,
      url: a.arquivo ? '/media/' + a.arquivo : null,
      duracao: a.duracao || (a.tipo === 'video' ? 0 : config.defaultImageSeconds),
      texto: a.texto, corFundo: a.corFundo, corTexto: a.corTexto, tamanho: a.tamanho,
      legenda: social.config().mostrarLegenda !== false ? a.legenda : '',
      origem: a.origem,
      v: a.atualizadoEm,
    })),
  });
});

// redes escolhidas no formulário ("facebook,instagram")
function redesPedidas(body) {
  const v = Array.isArray(body.publicar) ? body.publicar.join(',') : String(body.publicar || '');
  return v.split(',').map(x => x.trim()).filter(x => x === 'facebook' || x === 'instagram');
}

app.post('/api/anuncios', requireToken, upload.array('arquivos', 50), async (req, res) => {
  if (!req.files || !req.files.length) {
    return res.status(400).json({ erro: 'Nenhum arquivo de imagem ou vídeo válido foi recebido.' });
  }
  const created = req.files.map(f => {
    const ad = sanitizeFields(req.body, {
      id: crypto.randomUUID(),
      titulo: '',
      arquivo: f.filename,
      nomeOriginal: f.originalname,
      tipo: isVideo(f) ? 'video' : 'imagem',
      tamanho: f.size,
      inicio: null,
      fim: null,
      duracao: 0,
      ativo: true,
      criadoEm: Date.now(),
    });
    if (!ad.titulo) ad.titulo = (ad.legenda || '').split('\n')[0].slice(0, 60) || f.originalname;
    if (req.body.ativo === undefined) ad.ativo = true;
    ad.atualizadoEm = Date.now();
    ads.push(ad);
    return ad;
  });
  saveAds();
  // publica nas redes só o primeiro arquivo (um post por envio)
  const redes = redesPedidas(req.body);
  let publicado = {};
  if (redes.length) {
    const ad = created[0];
    publicado = await social.publicar({
      redes, tipo: ad.tipo, texto: ad.legenda || '', arquivo: path.join(MEDIA_DIR, ad.arquivo), nomeMidia: ad.arquivo,
    });
    marcarPostado(ad, publicado);
  }
  res.json({ ok: true, criados: created.length, publicado });
});

function marcarPostado(ad, publicado) {
  for (const [rede, r] of Object.entries(publicado)) {
    if (r.ok) (ad.postado = ad.postado || {})[rede] = { id: r.id, link: r.link, em: Date.now() };
  }
  saveAds();
}

// Imagem gerada no navegador (texto -> JPG) para o Instagram, que não aceita post só de texto
function salvarImagemBase64(dataUrl) {
  const m = /^data:image\/jpeg;base64,([A-Za-z0-9+/=]+)$/.exec(String(dataUrl || ''));
  if (!m) return null;
  const nome = Date.now() + '-' + crypto.randomBytes(4).toString('hex') + '-post.jpg';
  fs.writeFileSync(path.join(MEDIA_DIR, nome), Buffer.from(m[1], 'base64'));
  return nome;
}

// Anúncio só de texto
app.post('/api/anuncios/texto', requireToken, async (req, res) => {
  const ad = sanitizeFields(req.body, {
    id: crypto.randomUUID(), titulo: '', tipo: 'texto', texto: '',
    corFundo: '#1e3a8a', corTexto: '#ffffff', tamanho: 'M',
    inicio: null, fim: null, duracao: 0, ativo: true, criadoEm: Date.now(),
  });
  if (!ad.texto.trim()) return res.status(400).json({ erro: 'Escreva o texto do anúncio.' });
  if (!ad.titulo) ad.titulo = ad.texto.trim().split('\n')[0].slice(0, 60);
  ad.ativo = req.body.ativo === undefined ? true : ad.ativo;
  ad.atualizadoEm = Date.now();
  ads.push(ad);
  saveAds();
  const redes = redesPedidas(req.body);
  let publicado = {};
  if (redes.length) {
    const img = redes.includes('instagram') ? salvarImagemBase64(req.body.imagemPost) : null;
    if (img) ad.imagemPost = img;
    publicado = Object.assign(
      redes.includes('facebook') ? await social.publicar({ redes: ['facebook'], tipo: 'texto', texto: ad.texto }) : {},
      redes.includes('instagram') ? await social.publicar({
        redes: ['instagram'], tipo: 'imagem', texto: ad.texto,
        nomeMidia: img, arquivo: img && path.join(MEDIA_DIR, img),
      }) : {},
    );
    marcarPostado(ad, publicado);
  }
  res.json({ ok: true, criados: 1, publicado });
});

app.put('/api/anuncios/:id', requireToken, (req, res) => {
  const ad = ads.find(a => a.id === req.params.id);
  if (!ad) return res.status(404).json({ erro: 'Anúncio não encontrado' });
  sanitizeFields(req.body, ad);
  ad.atualizadoEm = Date.now();
  saveAds();
  res.json(ad);
});

// Reordenar: recebe a lista de ids na nova ordem
app.post('/api/ordem', requireToken, (req, res) => {
  const ids = Array.isArray(req.body.ids) ? req.body.ids : [];
  const pos = new Map(ids.map((id, i) => [id, i]));
  ads.sort((a, b) => (pos.get(a.id) ?? 1e9) - (pos.get(b.id) ?? 1e9));
  saveAds();
  res.json({ ok: true });
});

app.delete('/api/anuncios/:id', requireToken, (req, res) => {
  const i = ads.findIndex(a => a.id === req.params.id);
  if (i < 0) return res.status(404).json({ erro: 'Anúncio não encontrado' });
  const [ad] = ads.splice(i, 1);
  saveAds();
  apagarArquivos(ad);
  res.json({ ok: true });
});

function apagarArquivos(ad) {
  for (const f of [ad.arquivo, ad.imagemPost]) if (f) fs.unlink(path.join(MEDIA_DIR, f), () => {});
}

// ---------- redes sociais ----------
const social = require('./social')({
  config, saveConfig: () => writeJson(CONFIG_FILE, config), MEDIA_DIR,
  hasExternal: id => ads.some(a => a.externalId === id),
  // post importado que foi publicado por este painel (mesmo texto): não duplica na TV
  jaPostado: (rede, texto) => {
    const t = String(texto || '').trim();
    return !!t && ads.some(a => a.postado && a.postado[rede] && String(a.texto || a.legenda || '').trim() === t);
  },
  addAd: ad => { ads.push(ad); saveAds(); },
});

app.get('/api/social', requireToken, (req, res) => res.json(social.publicState()));
app.put('/api/social', requireToken, (req, res) => { social.update(req.body || {}); res.json(social.publicState()); });
app.post('/api/social/sincronizar', requireToken, async (req, res) => {
  await social.sync();
  res.json(social.publicState());
});
// Opções do formulário: em quais redes dá para publicar, e a orientação da TV (para a prévia)
app.get('/api/publicar/opcoes', requireToken, (req, res) => res.json({ ...social.podePublicar(), orientacao: config.orientacao }));

// ---------- WhatsApp ----------
const whatsapp = require('./whatsapp')({
  config, saveConfig: () => writeJson(CONFIG_FILE, config), MEDIA_DIR,
  socialCfg: social.config, ads: () => ads,
  addAd: ad => { ads.push(ad); saveAds(); },
  removeAd: id => {
    const i = ads.findIndex(a => a.id === id);
    if (i >= 0) { apagarArquivos(ads[i]); ads.splice(i, 1); saveAds(); }
  },
});
app.get('/webhook/whatsapp', whatsapp.verificar);
app.post('/webhook/whatsapp', whatsapp.receber);
app.get('/api/whatsapp', requireToken, (req, res) => res.json(whatsapp.publicState()));
app.put('/api/whatsapp', requireToken, (req, res) => { whatsapp.update(req.body || {}); res.json(whatsapp.publicState()); });

app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).json({ erro: err.message || 'Erro interno' });
});

app.listen(PORT, '0.0.0.0', () => {
  const b = baseUrl();
  console.log('\n=== Painel de Anúncios rodando ===');
  console.log(`Painel (neste computador):  http://localhost:${PORT}/admin`);
  console.log(`Abra na Smart TV / TV Box:  ${b}/tv`);
  console.log(`Enviar pelo celular:        ${b}/upload.html?k=${config.token}`);
  console.log('Deixe esta janela aberta enquanto usar o painel.\n');
  QRCode.toString(`${b}/upload.html?k=${config.token}`, { type: 'terminal', small: true })
    .then(q => console.log('QR code para enviar pelo celular:\n' + q))
    .catch(() => {});
  social.start();
});
