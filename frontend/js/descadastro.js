(function () {
  'use strict';
  var btn = document.getElementById('btn');
  var titulo = document.getElementById('titulo');
  var texto = document.getElementById('texto');
  var token = new URLSearchParams(window.location.search).get('t') || '';

  function mostrar(t, msg, classe) {
    titulo.textContent = t;
    texto.textContent = msg;
    if (classe) texto.className = classe;
  }

  if (!token) {
    btn.hidden = true;
    mostrar('Link inválido', 'Este link de descadastro está incompleto ou expirou.', 'quiet');
    return;
  }

  btn.addEventListener('click', function () {
    btn.disabled = true;
    btn.textContent = 'Processando…';
    fetch('/api/marketing/unsubscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token })
    }).then(function (r) {
      if (!r.ok) throw new Error('falha');
      btn.hidden = true;
      mostrar('Pronto ✓', 'Você foi descadastrado e não receberá mais estes e-mails.', 'ok');
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = 'Tentar de novo';
      mostrar('Não deu certo', 'Não conseguimos concluir agora. Tente novamente em instantes.', 'quiet');
    });
  });
})();
