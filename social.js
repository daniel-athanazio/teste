// Importa automaticamente os posts mais recentes do Instagram e de uma Página do Facebook
// e os transforma em anúncios. Usa as APIs oficiais da Meta (exige internet e um token de acesso).
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { Readable } = require('stream');
const { pipeline } = require('stream/promises');

// Podem ser trocados por variáveis de ambiente (útil para testes)
const IG_API = process.env.IG_API || 'https://graph.instagram.com';
const FB_API = process.env.FB_API || 'https://graph.facebook.com/v21.0';
const INTERVALO_MIN = Number(process.env.SOCIAL_INTERVAL_MIN) || 10;
const DIA = 24 * 3600 * 1000;
// mesmo formato do campo datetime-local do painel (horário do computador)
const dataLocal = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

module.exports = function createSocial({ config, saveConfig, MEDIA_DIR, hasExternal, addAd }) {
  const defaults = {
    instagram: { ativo: false, token: '' },
    facebook: { ativo: false, pageId: '', token: '' },
    quantidade: 5,       // quantos posts recentes verificar
    diasExibir: 7,       // por quantos dias o post fica no ar (0 = sem fim)
    duracao: 10,         // segundos na tela
    mostrarLegenda: true,
    importados: [],      // ids já importados (não importa de novo, mesmo se excluído)
  };
  config.social = Object.assign({}, defaults, config.social);
  config.social.instagram = Object.assign({}, defaults.instagram, config.social.instagram);
  config.social.facebook = Object.assign({}, defaults.facebook, config.social.facebook);
  const cfg = () => config.social;

  const estado = { sincronizando: false, ultima: null, erros: {}, novos: 0 };

  function publicState() {
    const c = cfg();
    return {
      instagram: { ativo: c.instagram.ativo, temToken: !!c.instagram.token },
      facebook: { ativo: c.facebook.ativo, pageId: c.facebook.pageId, temToken: !!c.facebook.token },
      quantidade: c.quantidade, diasExibir: c.diasExibir, duracao: c.duracao, mostrarLegenda: c.mostrarLegenda,
      intervaloMin: INTERVALO_MIN,
      ...estado,
    };
  }

  function update(b) {
    const c = cfg();
    const num = (v, min, max, def) => { const n = Number(v); return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : def; };
    if (b.instagram) {
      if (b.instagram.ativo !== undefined) c.instagram.ativo = !!b.instagram.ativo;
      if (b.instagram.token) c.instagram.token = String(b.instagram.token).trim();
      if (b.instagram.removerToken) c.instagram.token = '';
    }
    if (b.facebook) {
      if (b.facebook.ativo !== undefined) c.facebook.ativo = !!b.facebook.ativo;
      if (b.facebook.pageId !== undefined) c.facebook.pageId = String(b.facebook.pageId).trim();
      if (b.facebook.token) c.facebook.token = String(b.facebook.token).trim();
      if (b.facebook.removerToken) c.facebook.token = '';
    }
    if (b.quantidade !== undefined) c.quantidade = num(b.quantidade, 1, 25, 5);
    if (b.diasExibir !== undefined) c.diasExibir = num(b.diasExibir, 0, 365, 7);
    if (b.duracao !== undefined) c.duracao = num(b.duracao, 3, 600, 10);
    if (b.mostrarLegenda !== undefined) c.mostrarLegenda = !!b.mostrarLegenda;
    saveConfig();
  }

  async function getJson(url) {
    const r = await fetch(url);
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error((j.error && j.error.message) || `HTTP ${r.status}`);
    return j;
  }

  async function baixar(url, tipo) {
    const ext = tipo === 'video' ? '.mp4' : '.jpg';
    const nome = Date.now() + '-' + crypto.randomBytes(4).toString('hex') + ext;
    const r = await fetch(url);
    if (!r.ok || !r.body) throw new Error('Falha ao baixar mídia: HTTP ' + r.status);
    const destino = path.join(MEDIA_DIR, nome);
    await pipeline(Readable.fromWeb(r.body), fs.createWriteStream(destino));
    return { nome, tamanho: fs.statSync(destino).size };
  }

  // Converte um post normalizado em anúncio
  async function importar(post) {
    const c = cfg();
    if (c.importados.includes(post.externalId) || hasExternal(post.externalId)) return false;
    const base = new Date(post.data || Date.now());
    const ad = {
      id: crypto.randomUUID(),
      titulo: `${post.origem === 'instagram' ? 'Instagram' : 'Facebook'}: ${(post.legenda || 'post').split('\n')[0].slice(0, 60)}`,
      tipo: post.tipo,
      origem: post.origem,
      externalId: post.externalId,
      link: post.link || null,
      legenda: (post.legenda || '').slice(0, 300),
      inicio: null,
      fim: c.diasExibir > 0 ? dataLocal(new Date(base.getTime() + c.diasExibir * DIA)) : null,
      duracao: post.tipo === 'video' ? 0 : c.duracao,
      ativo: true,
      criadoEm: Date.now(),
      atualizadoEm: Date.now(),
    };
    if (ad.fim && new Date(ad.fim).getTime() < Date.now()) { marcar(post.externalId); return false; } // já venceu
    if (post.tipo === 'texto') {
      Object.assign(ad, { texto: ad.legenda, legenda: '', corFundo: post.origem === 'instagram' ? '#833ab4' : '#1877f2', corTexto: '#ffffff', tamanho: 'M' });
    } else {
      const f = await baixar(post.mediaUrl, post.tipo);
      ad.arquivo = f.nome; ad.tamanho = f.tamanho; ad.nomeOriginal = post.link || post.externalId;
    }
    addAd(ad);
    marcar(post.externalId);
    return true;
  }

  function marcar(id) {
    const c = cfg();
    c.importados.push(id);
    if (c.importados.length > 1000) c.importados = c.importados.slice(-1000);
    saveConfig();
  }

  // ----- Instagram (API do Instagram com login do Instagram; conta Profissional/Criador) -----
  async function postsInstagram() {
    const c = cfg().instagram;
    // renova o token de longa duração (válido por 60 dias) a cada ~7 dias
    if (!c.renovadoEm || Date.now() - c.renovadoEm > 7 * DIA) {
      try {
        const r = await getJson(`${IG_API}/refresh_access_token?grant_type=ig_refresh_token&access_token=${encodeURIComponent(c.token)}`);
        if (r.access_token) c.token = r.access_token;
        c.renovadoEm = Date.now(); saveConfig();
      } catch { /* token novo (menos de 24h) não pode ser renovado ainda; tenta depois */ }
    }
    const fields = 'id,caption,media_type,media_url,thumbnail_url,timestamp,permalink';
    const j = await getJson(`${IG_API}/me/media?fields=${fields}&limit=${cfg().quantidade}&access_token=${encodeURIComponent(c.token)}`);
    return (j.data || []).filter(m => m.media_url).map(m => ({
      origem: 'instagram',
      externalId: 'ig:' + m.id,
      tipo: m.media_type === 'VIDEO' ? 'video' : 'imagem', // carrossel: usa a primeira imagem
      mediaUrl: m.media_url,
      legenda: m.caption || '',
      data: m.timestamp,
      link: m.permalink,
    }));
  }

  // ----- Facebook (posts de uma Página, com token de acesso da Página) -----
  async function postsFacebook() {
    const c = cfg().facebook;
    const fields = 'id,message,full_picture,created_time,permalink_url,attachments{media_type,media}';
    const j = await getJson(`${FB_API}/${encodeURIComponent(c.pageId)}/posts?fields=${encodeURIComponent(fields)}&limit=${cfg().quantidade}&access_token=${encodeURIComponent(c.token)}`);
    return (j.data || []).map(p => {
      const att = p.attachments && p.attachments.data && p.attachments.data[0];
      const videoSrc = att && /video/i.test(att.media_type || '') && att.media && att.media.source;
      const tipo = videoSrc ? 'video' : p.full_picture ? 'imagem' : p.message ? 'texto' : null;
      return tipo && {
        origem: 'facebook',
        externalId: 'fb:' + p.id,
        tipo,
        mediaUrl: videoSrc || p.full_picture,
        legenda: p.message || '',
        data: p.created_time,
        link: p.permalink_url,
      };
    }).filter(Boolean);
  }

  async function sync() {
    if (estado.sincronizando) return;
    estado.sincronizando = true; estado.novos = 0;
    const c = cfg();
    const fontes = [];
    if (c.instagram.ativo && c.instagram.token) fontes.push(['instagram', postsInstagram]);
    if (c.facebook.ativo && c.facebook.token && c.facebook.pageId) fontes.push(['facebook', postsFacebook]);
    for (const [nome, buscar] of fontes) {
      try {
        const posts = await buscar();
        // do mais antigo para o mais novo, para o mais recente ficar por último na fila
        for (const p of posts.reverse()) if (await importar(p)) estado.novos++;
        delete estado.erros[nome];
      } catch (e) {
        estado.erros[nome] = e.message;
        console.error(`[${nome}]`, e.message);
      }
    }
    estado.ultima = Date.now();
    estado.sincronizando = false;
  }

  function start() {
    sync();
    setInterval(sync, INTERVALO_MIN * 60 * 1000);
  }

  return { config: cfg, publicState, update, sync, start };
};
