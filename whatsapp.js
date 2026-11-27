// Recebe anúncios pelo WhatsApp: quem está autorizado manda texto, foto ou vídeo para o número
// do painel (API oficial do WhatsApp Business / Cloud API) e a mensagem entra na TV.
// O WhatsApp entrega as mensagens num endereço https público (webhook), por isso é preciso um túnel.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { Readable } = require('stream');
const { pipeline } = require('stream/promises');

const WA_API = process.env.WA_API || 'https://graph.facebook.com/v21.0';
const DIA = 24 * 3600 * 1000;
const dataLocal = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const soDigitos = s => String(s || '').replace(/\D/g, '');

module.exports = function createWhatsapp({ config, saveConfig, MEDIA_DIR, socialCfg, ads, addAd, removeAd }) {
  config.whatsapp = Object.assign({
    ativo: false,
    token: '',          // token de acesso (usuário do sistema) com whatsapp_business_messaging
    appSecret: '',      // opcional: confere a assinatura das mensagens recebidas
    verifyToken: crypto.randomBytes(8).toString('hex'),
    autorizados: [],    // números que podem enviar anúncios (com DDI e DDD, só dígitos)
  }, config.whatsapp);
  const cfg = () => config.whatsapp;
  saveConfig();
  const estado = { ultima: null, erro: null };

  function publicState() {
    const c = cfg();
    return {
      ativo: c.ativo, temToken: !!c.token, temSecret: !!c.appSecret,
      verifyToken: c.verifyToken, autorizados: c.autorizados.join('\n'),
      webhook: socialCfg().urlPublica ? socialCfg().urlPublica + '/webhook/whatsapp' : '',
      ...estado,
    };
  }

  function update(b) {
    const c = cfg();
    if (b.ativo !== undefined) c.ativo = !!b.ativo;
    if (b.token) c.token = String(b.token).trim();
    if (b.removerToken) c.token = '';
    if (b.appSecret) c.appSecret = String(b.appSecret).trim();
    if (b.autorizados !== undefined) {
      c.autorizados = String(b.autorizados).split(/[\n,;]+/).map(soDigitos).filter(n => n.length >= 8).slice(0, 50);
    }
    saveConfig();
  }

  // Números do Brasil: aceita com ou sem o 9 extra do celular. Devolve o número cadastrado (ou undefined)
  function autorizado(numero) {
    const n = soDigitos(numero);
    const variantes = [n];
    if (/^55\d{10}$/.test(n)) variantes.push(n.slice(0, 4) + '9' + n.slice(4));
    if (/^55\d{11}$/.test(n)) variantes.push(n.slice(0, 4) + n.slice(5));
    return cfg().autorizados.find(a => variantes.includes(a));
  }

  // GET de verificação feito pela Meta ao cadastrar o webhook
  function verificar(req, res) {
    if (req.query['hub.mode'] === 'subscribe' && req.query['hub.verify_token'] === cfg().verifyToken) {
      return res.send(String(req.query['hub.challenge'] || ''));
    }
    res.sendStatus(403);
  }

  function assinaturaOk(req) {
    const secret = cfg().appSecret;
    if (!secret) return true;
    const sig = req.get('x-hub-signature-256') || '';
    const esperado = 'sha256=' + crypto.createHmac('sha256', secret).update(req.rawBody || '').digest('hex');
    return sig.length === esperado.length && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(esperado));
  }

  async function api(url, opts = {}) {
    const r = await fetch(url, { ...opts, headers: { Authorization: 'Bearer ' + cfg().token, ...(opts.headers || {}) } });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error((j.error && j.error.message) || 'HTTP ' + r.status);
    }
    return r;
  }

  async function responder(phoneId, para, texto) {
    if (!phoneId || !cfg().token) return;
    try {
      await api(`${WA_API}/${phoneId}/messages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messaging_product: 'whatsapp', to: para, type: 'text', text: { body: texto } }),
      });
    } catch (e) { console.error('[whatsapp] resposta:', e.message); }
  }

  async function baixarMidia(mediaId, tipo) {
    const info = await (await api(`${WA_API}/${mediaId}`)).json();
    const r = await api(info.url);
    const ext = tipo === 'video' ? '.mp4' : (/png/.test(info.mime_type) ? '.png' : /webp/.test(info.mime_type) ? '.webp' : '.jpg');
    const nome = Date.now() + '-' + crypto.randomBytes(4).toString('hex') + ext;
    const destino = path.join(MEDIA_DIR, nome);
    await pipeline(Readable.fromWeb(r.body), fs.createWriteStream(destino));
    return { nome, tamanho: fs.statSync(destino).size };
  }

  const emAndamento = new Set();
  async function tratarMensagem(m, phoneId) {
    if (emAndamento.has(m.id)) return;
    emAndamento.add(m.id);
    try { await tratar(m, phoneId); } finally { emAndamento.delete(m.id); }
  }

  async function tratar(m, phoneId) {
    if (ads().some(a => a.externalId === 'wa:' + m.id)) return; // reentrega da Meta
    const de = autorizado(m.from);
    if (!de) {
      return responder(phoneId, m.from, `Este número (${m.from}) não está autorizado a enviar anúncios. Peça para cadastrá-lo no painel.`);
    }
    const texto = m.type === 'text' ? (m.text && m.text.body) || '' : '';
    const cmd = texto.trim().toLowerCase();
    if (cmd === 'apagar' || cmd === 'excluir') {
      const meus = ads().filter(a => a.origem === 'whatsapp' && a.remetente === de);
      const ultimo = meus[meus.length - 1];
      if (!ultimo) return responder(phoneId, m.from, 'Você não tem anúncios enviados pelo WhatsApp.');
      removeAd(ultimo.id);
      return responder(phoneId, m.from, `🗑️ Removido da TV: ${ultimo.titulo}`);
    }
    if (cmd === 'ajuda' || cmd === 'menu') {
      return responder(phoneId, m.from, 'Envie um texto, uma foto ou um vídeo e ele aparece na TV.\nA legenda da foto/vídeo aparece junto.\nMande APAGAR para tirar da TV o último que você enviou.');
    }

    const s = socialCfg();
    const ad = {
      id: crypto.randomUUID(), origem: 'whatsapp', externalId: 'wa:' + m.id, remetente: de,
      inicio: null, fim: s.diasExibir > 0 ? dataLocal(new Date(Date.now() + s.diasExibir * DIA)) : null,
      duracao: s.duracao, ativo: true, criadoEm: Date.now(), atualizadoEm: Date.now(),
    };
    if (m.type === 'text') {
      if (!texto.trim()) return;
      Object.assign(ad, { tipo: 'texto', texto: texto.slice(0, 1000), corFundo: '#075e54', corTexto: '#ffffff', tamanho: 'M' });
    } else if (m.type === 'image' || m.type === 'video') {
      const midia = m[m.type];
      ad.tipo = m.type === 'video' ? 'video' : 'imagem';
      if (ad.tipo === 'video') ad.duracao = 0;
      ad.legenda = (midia.caption || '').slice(0, 300);
      const f = await baixarMidia(midia.id, ad.tipo);
      ad.arquivo = f.nome; ad.tamanho = f.tamanho; ad.nomeOriginal = 'WhatsApp ' + de;
    } else {
      return responder(phoneId, m.from, 'Só consigo exibir texto, foto ou vídeo.');
    }
    ad.titulo = 'WhatsApp: ' + ((ad.texto || ad.legenda || (ad.tipo === 'video' ? 'vídeo' : 'foto')).split('\n')[0].slice(0, 60));
    addAd(ad);
    estado.ultima = Date.now(); estado.erro = null;
    responder(phoneId, m.from, '✅ Pronto! Já está na TV' + (ad.fim ? ` até ${new Date(ad.fim).toLocaleDateString('pt-BR')}` : '') + '.\nMande APAGAR para tirar.');
  }

  // POST com as mensagens recebidas
  function receber(req, res) {
    if (!assinaturaOk(req)) return res.sendStatus(401);
    res.sendStatus(200); // a Meta exige resposta rápida; o processamento segue em segundo plano
    if (!cfg().ativo) return;
    const entradas = (req.body && req.body.entry) || [];
    for (const e of entradas) {
      for (const ch of e.changes || []) {
        const v = ch.value || {};
        const phoneId = v.metadata && v.metadata.phone_number_id;
        for (const m of v.messages || []) {
          tratarMensagem(m, phoneId).catch(err => {
            estado.erro = err.message;
            console.error('[whatsapp]', err.message);
            responder(phoneId, m.from, 'Não consegui colocar na TV: ' + err.message);
          });
        }
      }
    }
  }

  return { publicState, update, verificar, receber };
};
