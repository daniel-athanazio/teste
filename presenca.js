// Sensor de presença por Wi-Fi.
// Recebe avisos de movimento de qualquer fonte na rede local:
//  - placa ESP32 com o firmware de sensor-esp32/ (detecta movimento pela perturbação do sinal Wi-Fi — CSI)
//  - roteador / Home Assistant / SmartThings / IFTTT, chamando uma URL (webhook)
// e transforma isso em: presença agora, contagem de visitas por hora/dia e alarme de presença.
const fs = require('fs');
const path = require('path');

const OFFLINE_MS = 2 * 60 * 1000;    // sensor sem dar sinal por 2 min = offline
const MOV_EXPIRA_MS = 90 * 1000;     // "movimento" vale por 90 s sem novo aviso (webhooks que só avisam o início)
const ALARME_REPETE_MS = 60 * 1000;  // não dispara o alarme de novo antes de 1 min
const DIAS_HISTORICO = 120;

const pad = n => String(n).padStart(2, '0');
const chaveDia = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const chaveHora = d => `${chaveDia(d)} ${pad(d.getHours())}`;
const minutos = hhmm => { const [h, m] = String(hhmm || '0:0').split(':').map(Number); return (h || 0) * 60 + (m || 0); };
const verdadeiro = v => v === true || v === 1 || ['1', 'true', 'on', 'sim', 'movimento'].includes(String(v).toLowerCase());

module.exports = function createPresenca({ config, saveConfig, DATA_DIR, log = console.log }) {
  const FILE = path.join(DATA_DIR, 'presenca.json');
  const defaults = {
    intervaloVisita: 60, // segundos sem movimento para o próximo movimento contar como nova visita
    alarme: { armado: false, auto: false, de: '20:00', ate: '07:00', webhook: '' },
  };
  config.presenca = Object.assign({}, defaults, config.presenca);
  config.presenca.alarme = Object.assign({}, defaults.alarme, config.presenca.alarme);
  const cfg = () => config.presenca;

  let hist;
  try { hist = JSON.parse(fs.readFileSync(FILE, 'utf8')); } catch { hist = {}; }
  hist.horas = hist.horas || {};   // { "2026-11-28 14": 3 }
  hist.eventos = hist.eventos || []; // últimos eventos (visitas, alarmes)
  const salvarHist = () => {
    const limite = chaveDia(new Date(Date.now() - DIAS_HISTORICO * 864e5));
    for (const k of Object.keys(hist.horas)) if (k < limite) delete hist.horas[k];
    hist.eventos = hist.eventos.slice(-200);
    const tmp = FILE + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(hist));
    fs.renameSync(tmp, FILE);
  };

  const sensores = new Map(); // id -> { id, nome, origem, movimento, intensidade, visto, movVisto }
  let presente = false;
  let ultimaAtividade = 0;
  let alarmeDisparadoEm = null;
  let ultimoAlarme = 0;

  function alarmeAtivoAgora(now = new Date()) {
    const a = cfg().alarme;
    if (a.armado) return true;
    if (!a.auto) return false;
    const m = now.getHours() * 60 + now.getMinutes(), de = minutos(a.de), ate = minutos(a.ate);
    return de <= ate ? (m >= de && m < ate) : (m >= de || m < ate); // faixa pode passar da meia-noite
  }

  const emMovimento = (s, now) => s.movimento && now - s.movVisto < MOV_EXPIRA_MS && now - s.visto < OFFLINE_MS;

  function evento(tipo, texto, sensor) {
    hist.eventos.push({ em: Date.now(), tipo, texto, sensor });
  }

  async function avisar(msg) {
    const url = cfg().alarme.webhook;
    if (!url) return;
    try {
      // {msg} na URL: troca pela mensagem e faz GET (ex.: Telegram). Sem {msg}: POST com o texto (ex.: ntfy.sh).
      const r = url.includes('{msg}')
        ? await fetch(url.replace('{msg}', encodeURIComponent(msg)))
        : await fetch(url, { method: 'POST', body: msg, headers: { 'Content-Type': 'text/plain; charset=utf-8', Title: 'Alarme de presença', Priority: 'high', Tags: 'rotating_light' } });
      if (!r.ok) log('Alarme: o aviso respondeu ' + r.status);
    } catch (e) { log('Alarme: não consegui enviar o aviso: ' + e.message); }
  }

  // Recalcula a presença geral e trata o início/fim de cada período de movimento.
  function avaliar() {
    const now = Date.now();
    const agora = [...sensores.values()].filter(s => emMovimento(s, now));
    const antes = presente;
    presente = agora.length > 0;
    if (presente && !antes) {
      const nomes = agora.map(s => s.nome).join(', ');
      if (now - ultimaAtividade > cfg().intervaloVisita * 1000) {
        const k = chaveHora(new Date(now));
        hist.horas[k] = (hist.horas[k] || 0) + 1;
        evento('visita', 'Nova visita', nomes);
      }
      if (alarmeAtivoAgora() && now - ultimoAlarme > ALARME_REPETE_MS) {
        ultimoAlarme = alarmeDisparadoEm = now;
        const msg = `🚨 Movimento detectado (${nomes}) às ${new Date(now).toLocaleTimeString('pt-BR')}`;
        evento('alarme', msg, nomes);
        log(msg);
        avisar(msg);
      }
      salvarHist();
    }
    if (presente || antes) ultimaAtividade = now;
  }

  function receber(b) {
    const id = String(b.sensor || b.id || 'sensor').slice(0, 40);
    const now = Date.now();
    const s = sensores.get(id) || { id, nome: id, origem: '', movimento: false, intensidade: null, visto: 0, movVisto: 0 };
    if (b.nome) s.nome = String(b.nome).slice(0, 60);
    if (b.origem) s.origem = String(b.origem).slice(0, 30);
    s.movimento = verdadeiro(b.movimento);
    const i = Number(b.intensidade);
    s.intensidade = Number.isFinite(i) ? Math.round(i * 100) / 100 : null;
    s.visto = now;
    if (s.movimento) s.movVisto = now;
    sensores.set(id, s);
    avaliar();
    return { ok: true, presente, alarme: alarmeAtivoAgora() };
  }

  function update(b) {
    const c = cfg();
    const iv = Number(b.intervaloVisita);
    if (Number.isFinite(iv)) c.intervaloVisita = Math.max(5, Math.min(3600, iv));
    if (b.alarme) {
      const a = b.alarme, hora = /^\d{1,2}:\d{2}$/;
      if (a.armado !== undefined) c.alarme.armado = !!a.armado;
      if (a.auto !== undefined) c.alarme.auto = !!a.auto;
      if (hora.test(a.de)) c.alarme.de = a.de;
      if (hora.test(a.ate)) c.alarme.ate = a.ate;
      if (a.webhook !== undefined) c.alarme.webhook = /^https?:\/\//.test(a.webhook) ? String(a.webhook).trim() : '';
    }
    if (b.silenciar) alarmeDisparadoEm = null;
    if (b.esquecer) sensores.delete(String(b.esquecer));
    saveConfig();
  }

  function publicState() {
    const now = Date.now(), d = new Date(now);
    const hoje = chaveDia(d);
    const porHora = Array.from({ length: 24 }, (_, h) => hist.horas[`${hoje} ${pad(h)}`] || 0);
    const dias = [];
    for (let i = 6; i >= 0; i--) {
      const k = chaveDia(new Date(now - i * 864e5));
      let total = 0;
      for (let h = 0; h < 24; h++) total += hist.horas[`${k} ${pad(h)}`] || 0;
      dias.push({ dia: k, total });
    }
    return {
      presente,
      visitasHoje: porHora.reduce((a, b) => a + b, 0),
      porHora, dias,
      sensores: [...sensores.values()].map(s => ({
        id: s.id, nome: s.nome, origem: s.origem, intensidade: s.intensidade, visto: s.visto,
        online: now - s.visto < OFFLINE_MS, movimento: emMovimento(s, now),
      })),
      intervaloVisita: cfg().intervaloVisita,
      alarme: { ...cfg().alarme, ativoAgora: alarmeAtivoAgora(d), disparadoEm: alarmeDisparadoEm },
      eventos: hist.eventos.slice(-15).reverse(),
    };
  }

  // Planilha com as visitas por hora (abre no Excel)
  function csv() {
    const linhas = Object.keys(hist.horas).sort().map(k => `${k.slice(0, 10)};${k.slice(11)}:00;${hist.horas[k]}`);
    return '\ufeffdata;hora;visitas\n' + linhas.join('\n') + '\n';
  }

  setInterval(avaliar, 5000).unref();
  return { receber, update, publicState, csv };
};
