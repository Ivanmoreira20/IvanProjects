document.documentElement.classList.add('js-reveal');

const alvos = document.querySelectorAll('.reveal');
if (matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
  alvos.forEach((el) => el.classList.add('is-in'));
} else {
  const obs = new IntersectionObserver((entradas) => {
    entradas.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add('is-in');
      obs.unobserve(e.target);
    });
  }, { rootMargin: '0px 0px -10% 0px', threshold: 0.05 });
  alvos.forEach((el, i) => {
    el.style.transitionDelay = `${Math.min(i % 5, 4) * 60}ms`;
    obs.observe(el);
  });
}

const modal = document.getElementById('plan-modal');
const form = document.getElementById('plan-form');
const sucesso = document.getElementById('plan-success');
const erroBox = document.getElementById('plan-error');
const enviar = document.getElementById('plan-submit');

const TEXTOS = {
  pro: {
    tag: 'Plano Pro',
    titulo: 'Falar sobre o Pro',
    sub: 'O Pro já está no ar e você pode assinar sozinho, dentro do sistema, em Plano e cobrança. Use este formulário só se preferir falar com a gente antes.',
  },
  empresa: {
    tag: 'Plano Empresa',
    titulo: 'Falar sobre o Empresa',
    sub: 'Conte o tamanho da operação e o que precisa. Montamos uma proposta sob medida, sem compromisso.',
  },
};

let planoAtual = 'pro';
let ultimoFoco = null;
let enviando = false;

function mostrarErro(mensagem) {
  if (!erroBox) return;
  erroBox.textContent = mensagem || '';
  erroBox.hidden = !mensagem;
}

function abrir(plano) {
  planoAtual = TEXTOS[plano] ? plano : 'pro';
  const t = TEXTOS[planoAtual];

  document.getElementById('plan-modal-tag').textContent = t.tag;
  document.getElementById('plan-modal-title').textContent = t.titulo;
  document.getElementById('plan-modal-sub').textContent = t.sub;

  mostrarErro('');
  form.hidden = false;
  sucesso.hidden = true;

  ultimoFoco = document.activeElement;
  modal.hidden = false;
  document.body.style.overflow = 'hidden';
  document.addEventListener('keydown', aoTeclar, true);
  requestAnimationFrame(() => document.getElementById('plan-name')?.focus());
}

function fechar() {
  if (modal.hidden) return;
  modal.hidden = true;
  document.body.style.overflow = '';
  document.removeEventListener('keydown', aoTeclar, true);

  if (ultimoFoco && document.contains(ultimoFoco)) ultimoFoco.focus();
  ultimoFoco = null;
}

function aoTeclar(e) {
  if (e.key === 'Escape') { e.preventDefault(); fechar(); return; }
  if (e.key !== 'Tab') return;

  const focaveis = Array.from(modal.querySelectorAll(
    'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled])',
  )).filter((n) => n.offsetParent !== null);
  if (!focaveis.length) return;

  const primeiro = focaveis[0];
  const ultimo = focaveis[focaveis.length - 1];
  if (e.shiftKey && document.activeElement === primeiro) { e.preventDefault(); ultimo.focus(); }
  else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primeiro.focus(); }
}

document.addEventListener('click', (e) => {
  const botao = e.target.closest('[data-plano]');
  if (botao) { abrir(botao.dataset.plano); return; }
  if (e.target.closest('[data-fechar]')) fechar();
});

function abrirPelaUrl() {
  const alvo = (location.hash || '').replace('#', '').toLowerCase();
  if (TEXTOS[alvo]) abrir(alvo);
}
abrirPelaUrl();
window.addEventListener('hashchange', abrirPelaUrl);

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (enviando) return;

  const dados = {
    plan: planoAtual,
    name: document.getElementById('plan-name').value.trim(),
    email: document.getElementById('plan-email').value.trim(),
    company: document.getElementById('plan-company').value.trim(),
    phone: document.getElementById('plan-phone').value.trim(),
    seats: Number(document.getElementById('plan-seats').value) || 1,
    message: document.getElementById('plan-message').value.trim(),
  };

  if (!dados.name) return mostrarErro('Diga como podemos te chamar.');
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(dados.email)) {
    return mostrarErro('Confira o e-mail: é por ele que vamos responder.');
  }

  enviando = true;
  mostrarErro('');
  enviar.disabled = true;
  enviar.textContent = 'Enviando…';

  try {
    const resposta = await fetch('/api/plan-interest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dados),
    });

    if (!resposta.ok) {
      const corpo = await resposta.json().catch(() => ({}));
      if (resposta.status === 429) {
        throw new Error('Já recebemos vários pedidos deste endereço. Tente daqui a pouco.');
      }
      throw new Error(corpo.detail || 'Não foi possível enviar agora. Tente novamente.');
    }

    const corpo = await resposta.json();
    document.getElementById('plan-success-text').textContent = corpo.message
      || 'Entramos em contato pelo e-mail informado.';
    form.hidden = true;
    sucesso.hidden = false;
    sucesso.querySelector('.btn')?.focus();
  } catch (erro) {
    mostrarErro(erro?.message || 'Não foi possível enviar agora. Tente novamente.');
  } finally {
    enviando = false;
    enviar.disabled = false;
    enviar.textContent = 'Enviar pedido';
  }
});
