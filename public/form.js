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
    '<div class="drop" id="drop">📷 Toque para escolher fotos e vídeos<br><span class="mut">ou arraste os arquivos aqui</span>' +
    '<input type="file" id="arquivos" accept="image/*,video/*" multiple hidden></div>' +
    '<div id="sel" class="mut" style="margin-top:8px"></div>' +
    '<label>Título (opcional)</label><input type="text" id="titulo" placeholder="Ex.: Promoção de verão">' +
    '<div class="row">' +
    '<div><label>Começa em</label><input type="datetime-local" id="inicio"></div>' +
    '<div><label>Termina em</label><input type="datetime-local" id="fim"></div>' +
    '</div>' +
    '<label>Duração na tela (segundos)</label><input type="number" id="duracao" min="0" step="1" placeholder="Imagem: padrão do painel · Vídeo: inteiro">' +
    '<p class="mut">Deixe as datas vazias para exibir sempre. Vídeo com duração 0 ou vazia toca até o fim.</p>' +
    '<button class="big" id="enviar" disabled>Enviar</button>' +
    '<progress id="prog" max="100" value="0"></progress><div class="msg" id="msg"></div>';
}

function setupForm(container, onDone) {
  container.innerHTML = formHtml();
  var $ = function (id) { return document.getElementById(id); };
  var files = [];
  var drop = $('drop'), input = $('arquivos');
  function setFiles(list) {
    files = Array.prototype.slice.call(list).filter(function (f) { return /^(image|video)\//.test(f.type); });
    $('sel').textContent = files.length ? files.length + ' arquivo(s): ' + files.map(function (f) { return f.name; }).join(', ') : '';
    $('enviar').disabled = !files.length;
  }
  drop.onclick = function () { input.click(); };
  input.onchange = function () { setFiles(input.files); };
  drop.ondragover = function (e) { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = function () { drop.classList.remove('over'); };
  drop.ondrop = function (e) { e.preventDefault(); drop.classList.remove('over'); setFiles(e.dataTransfer.files); };

  $('enviar').onclick = function () {
    var fd = new FormData();
    files.forEach(function (f) { fd.append('arquivos', f); });
    ['titulo', 'inicio', 'fim', 'duracao'].forEach(function (k) { fd.append(k, $(k).value); });
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/anuncios');
    xhr.setRequestHeader('x-token', TOKEN);
    var prog = $('prog'), msg = $('msg');
    prog.style.display = 'block'; prog.value = 0; msg.textContent = ''; msg.className = 'msg';
    $('enviar').disabled = true;
    xhr.upload.onprogress = function (e) { if (e.lengthComputable) prog.value = Math.round(e.loaded / e.total * 100); };
    xhr.onload = function () {
      var j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) {}
      prog.style.display = 'none';
      if (xhr.status === 200) {
        msg.textContent = '✔ ' + j.criados + ' anúncio(s) enviado(s)!'; msg.className = 'msg ok';
        setFiles([]); input.value = ''; $('titulo').value = '';
        if (onDone) onDone();
      } else {
        msg.textContent = j.erro || 'Falha no envio'; msg.className = 'msg err'; $('enviar').disabled = false;
      }
    };
    xhr.onerror = function () { prog.style.display = 'none'; msg.textContent = 'Sem conexão com o computador. Verifique se está na mesma rede Wi-Fi.'; msg.className = 'msg err'; $('enviar').disabled = false; };
    xhr.send(fd);
  };
}
