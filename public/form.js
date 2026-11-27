// Formulário de envio compartilhado entre o painel e a página do celular.
var TOKEN = new URLSearchParams(location.search).get('k') || '';

function api(method, url, body) {
  return fetch(url, {
    method: method,
    headers: Object.assign({ 'x-token': TOKEN }, body ? { 'Content-Type': 'application/json' } : {}),
    body: body ? JSON.stringify(body) : undefined,
  }).then(function (r) {
    return r.json().then(function (j) { if (!r.ok) throw new Error(j.erro || 'Erro'); return j; });
  });
}

function formHtml() {
  return '' +
    '<div class="tabs"><button type="button" class="tab on" data-modo="midia">📷 Foto / Vídeo</button>' +
    '<button type="button" class="tab" data-modo="texto">✏️ Texto</button></div>' +
    '<div id="modoMidia"><div class="drop" id="drop">📷 Toque para escolher fotos e vídeos<br><span class="mut">ou arraste os arquivos aqui</span>' +
    '<input type="file" id="arquivos" accept="image/*,video/*" multiple hidden></div>' +
    '<div id="sel" class="mut" style="margin-top:8px"></div>' +
    '<label>Legenda (opcional — aparece na TV e no post)</label><textarea id="legenda" rows="2" maxlength="300" placeholder="Ex.: Chegou a coleção nova!"></textarea></div>' +
    '<div id="modoTexto" hidden>' +
    '<label>Texto do anúncio</label><textarea id="texto" rows="4" maxlength="1000" placeholder="Ex.: Hoje: 20% de desconto em todos os produtos!"></textarea>' +
    '<div class="row">' +
    '<div><label>Cor do fundo</label><input type="color" id="corFundo" value="#1e3a8a"></div>' +
    '<div><label>Cor do texto</label><input type="color" id="corTexto" value="#ffffff"></div>' +
    '<div><label>Tamanho da letra</label><select id="tamanho"><option value="P">Pequena</option><option value="M" selected>Média</option><option value="G">Grande</option></select></div>' +
    '</div>' +
    '<label>Prévia</label><div class="previa" id="previa"><div></div></div>' +
    '</div>' +
    '<label>Título (opcional)</label><input type="text" id="titulo" placeholder="Ex.: Promoção de verão">' +
    '<div class="row">' +
    '<div><label>Começa em</label><input type="datetime-local" id="inicio"></div>' +
    '<div><label>Termina em</label><input type="datetime-local" id="fim"></div>' +
    '</div>' +
    '<label>Duração na tela (segundos)</label><input type="number" id="duracao" min="0" step="1" placeholder="Imagem: padrão do painel · Vídeo: inteiro">' +
    '<p class="mut">Deixe as datas vazias para exibir sempre. Vídeo com duração 0 ou vazia toca até o fim.</p>' +
    '<fieldset class="publicar"><legend>Postar também nas redes sociais</legend>' +
    '<label class="check"><input type="checkbox" id="pubFacebook"> Facebook <span class="mut" id="fbAviso"></span></label>' +
    '<label class="check"><input type="checkbox" id="pubInstagram"> Instagram <span class="mut" id="igAviso"></span></label>' +
    '<label class="check"><input type="checkbox" id="pubWhatsapp"> WhatsApp <span class="mut">(abre o WhatsApp para escolher contatos, grupos ou o Status)</span></label>' +
    '<p class="mut" id="pubNota" hidden>Será postado só o primeiro arquivo escolhido.</p>' +
    '</fieldset>' +
    '<button class="big" id="enviar" disabled>Enviar</button>' +
    '<progress id="prog" max="100" value="0"></progress><div class="msg" id="msg"></div><div id="compartilhar"></div>';
}

// Desenha o anúncio de texto num JPG quadrado (o Instagram não aceita post só de texto)
function textoParaJpeg(texto, fundo, cor) {
  var c = document.createElement('canvas'), L = 1080, m = 90;
  c.width = c.height = L;
  var g = c.getContext('2d');
  g.fillStyle = fundo; g.fillRect(0, 0, L, L);
  g.fillStyle = cor; g.textAlign = 'center'; g.textBaseline = 'middle';
  var linhas, tam = 120;
  function quebrar() {
    linhas = [];
    texto.split('\n').forEach(function (par) {
      var atual = '';
      par.split(' ').forEach(function (p) {
        var t = atual ? atual + ' ' + p : p;
        if (g.measureText(t).width > L - 2 * m && atual) { linhas.push(atual); atual = p; } else atual = t;
      });
      linhas.push(atual);
    });
  }
  do {
    g.font = 'bold ' + tam + 'px Arial, Helvetica, sans-serif';
    quebrar();
    var largura = Math.max.apply(null, linhas.map(function (l) { return g.measureText(l).width; }));
    var cabe = linhas.length * tam * 1.25 <= L - 2 * m && largura <= L - 2 * m;
    tam -= 4;
  } while (!cabe && tam > 20);
  tam += 4;
  var y0 = L / 2 - (linhas.length - 1) * tam * 1.25 / 2;
  linhas.forEach(function (l, i) { g.fillText(l, L / 2, y0 + i * tam * 1.25); });
  return c.toDataURL('image/jpeg', 0.9);
}

var NOMES_REDE = { facebook: 'Facebook', instagram: 'Instagram' };
function resumoPublicado(p) {
  return Object.keys(p || {}).map(function (r) {
    return p[r].ok ? ' · ' + NOMES_REDE[r] + ': postado ✔' : ' · ' + NOMES_REDE[r] + ': não postou (' + p[r].erro + ')';
  }).join('');
}
function temErro(p) { return Object.keys(p || {}).some(function (r) { return !p[r].ok; }); }

function setupForm(container, onDone) {
  container.innerHTML = formHtml();
  var $ = function (id) { return document.getElementById(id); };
  var files = [], modo = 'midia';
  api('GET', '/api/publicar/opcoes').then(function (o) {
    if (!o.facebook) { $('pubFacebook').disabled = true; $('fbAviso').textContent = '(configure a Página em Redes sociais no painel)'; }
    if (!o.instagram) { $('pubInstagram').disabled = true; $('igAviso').textContent = '(configure o Instagram em Redes sociais no painel)'; }
    else if (o.instagramPrecisaUrl) { $('pubInstagram').disabled = true; $('igAviso').textContent = '(falta o endereço público em Redes sociais no painel)'; }
    $('previa').classList.toggle('vert', o.orientacao && o.orientacao !== 'horizontal');
  }).catch(function () {});
  function redes() {
    return ['facebook', 'instagram'].filter(function (r) { var c = $('pub' + r.charAt(0).toUpperCase() + r.slice(1)); return c.checked && !c.disabled; });
  }
  // Botão para mandar o mesmo conteúdo pelo WhatsApp (contatos, grupos ou Status)
  function oferecerWhatsapp(texto, arquivos) {
    var box = $('compartilhar'); box.innerHTML = '';
    if (!$('pubWhatsapp').checked) return;
    var a = document.createElement('a');
    a.className = 'btn wa'; a.target = '_blank'; a.rel = 'noopener';
    a.href = 'https://wa.me/?text=' + encodeURIComponent(texto || '');
    a.textContent = arquivos && arquivos.length ? '🟢 Compartilhar no WhatsApp' : '🟢 Postar no WhatsApp';
    if (arquivos && arquivos.length && navigator.canShare && navigator.canShare({ files: arquivos })) {
      a.onclick = function (e) { e.preventDefault(); navigator.share({ files: arquivos, text: texto || '' })['catch'](function () {}); };
    } else if (arquivos && arquivos.length) {
      var p = document.createElement('p'); p.className = 'mut';
      p.textContent = 'O WhatsApp vai abrir com a legenda; anexe a foto/vídeo da sua galeria.';
      box.appendChild(p);
    }
    box.insertBefore(a, box.firstChild);
  }
  var tabs = container.querySelectorAll('.tab');
  Array.prototype.forEach.call(tabs, function (t) {
    t.onclick = function () {
      modo = t.dataset.modo;
      Array.prototype.forEach.call(tabs, function (x) { x.classList.toggle('on', x === t); });
      $('modoMidia').hidden = modo !== 'midia'; $('modoTexto').hidden = modo !== 'texto';
      atualizaBotao();
    };
  });
  function atualizaBotao() {
    $('enviar').disabled = modo === 'midia' ? !files.length : !$('texto').value.trim();
    $('pubNota').hidden = !(modo === 'midia' && files.length > 1 && redes().length);
  }
  ['pubFacebook', 'pubInstagram'].forEach(function (id) { $(id).onchange = atualizaBotao; });
  function previa() {
    var p = $('previa'), d = p.firstChild;
    p.style.background = $('corFundo').value; d.style.color = $('corTexto').value;
    d.style.fontSize = { P: '14px', M: '20px', G: '28px' }[$('tamanho').value];
    d.textContent = $('texto').value || 'Seu texto aparece assim na TV';
    atualizaBotao();
  }
  ['texto', 'corFundo', 'corTexto', 'tamanho'].forEach(function (id) { $(id).oninput = $(id).onchange = previa; });
  previa();
  var drop = $('drop'), input = $('arquivos');
  function setFiles(list) {
    files = Array.prototype.slice.call(list).filter(function (f) { return /^(image|video)\//.test(f.type); });
    $('sel').textContent = files.length ? files.length + ' arquivo(s): ' + files.map(function (f) { return f.name; }).join(', ') : '';
    atualizaBotao();
  }
  drop.onclick = function () { input.click(); };
  input.onchange = function () { setFiles(input.files); };
  drop.ondragover = function (e) { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = function () { drop.classList.remove('over'); };
  drop.ondrop = function (e) { e.preventDefault(); drop.classList.remove('over'); setFiles(e.dataTransfer.files); };

  $('enviar').onclick = function () {
    var msg = $('msg');
    if (modo === 'texto') {
      var body = {};
      ['titulo', 'inicio', 'fim', 'duracao', 'texto', 'corFundo', 'corTexto', 'tamanho'].forEach(function (k) { body[k] = $(k).value; });
      body.publicar = redes().join(',');
      if (body.publicar.indexOf('instagram') >= 0) body.imagemPost = textoParaJpeg(body.texto, body.corFundo, body.corTexto);
      var textoEnviado = body.texto;
      $('enviar').disabled = true;
      msg.textContent = body.publicar ? 'Enviando e postando nas redes…' : ''; msg.className = 'msg';
      api('POST', '/api/anuncios/texto', body).then(function (j) {
        msg.textContent = '✔ Texto na TV!' + resumoPublicado(j.publicado); msg.className = temErro(j.publicado) ? 'msg err' : 'msg ok';
        oferecerWhatsapp(textoEnviado);
        $('texto').value = ''; $('titulo').value = ''; previa();
        if (onDone) onDone();
      }).catch(function (e) { msg.textContent = e.message; msg.className = 'msg err'; atualizaBotao(); });
      return;
    }
    var fd = new FormData();
    files.forEach(function (f) { fd.append('arquivos', f); });
    ['titulo', 'inicio', 'fim', 'duracao', 'legenda'].forEach(function (k) { fd.append(k, $(k).value); });
    fd.append('publicar', redes().join(','));
    var enviados = files.slice(0, 1), legendaEnviada = $('legenda').value;
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/anuncios');
    xhr.setRequestHeader('x-token', TOKEN);
    var prog = $('prog');
    prog.style.display = 'block'; prog.value = 0; msg.textContent = ''; msg.className = 'msg';
    $('enviar').disabled = true;
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) prog.value = Math.round(e.loaded / e.total * 100);
      if (prog.value >= 100 && redes().length) msg.textContent = 'Postando nas redes… (vídeos podem levar alguns minutos)';
    };
    xhr.onload = function () {
      var j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) {}
      prog.style.display = 'none';
      if (xhr.status === 200) {
        msg.textContent = '✔ ' + j.criados + ' anúncio(s) na TV!' + resumoPublicado(j.publicado);
        msg.className = temErro(j.publicado) ? 'msg err' : 'msg ok';
        oferecerWhatsapp(legendaEnviada, enviados);
        setFiles([]); input.value = ''; $('titulo').value = ''; $('legenda').value = '';
        if (onDone) onDone();
      } else {
        msg.textContent = j.erro || 'Falha no envio'; msg.className = 'msg err'; $('enviar').disabled = false;
      }
    };
    xhr.onerror = function () { prog.style.display = 'none'; msg.textContent = 'Sem conexão com o computador. Verifique se está na mesma rede Wi-Fi.'; msg.className = 'msg err'; $('enviar').disabled = false; };
    xhr.send(fd);
  };
}
