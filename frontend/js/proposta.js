import { el, clear, brl, toast } from './ui.js';

const BASE = '/api/public/proposal';

function tokenDaUrl() {
  const partes = location.pathname.split('/').filter(Boolean);
  return partes[partes.length - 1] || '';
}

const dataLonga = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
const dataCurta = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
});

const formata = (iso, fmt) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? fmt.format(d) : '—';
};

function texto(id, valor) {
  const no = document.getElementById(id);
  if (no) no.textContent = valor;
}

function mostrar(id, visivel) {
  const no = document.getElementById(id);
  if (no) no.hidden = !visivel;
}

function secao(wrapId, alvoId, conteudo) {
  const tem = Boolean((conteudo || '').trim());
  mostrar(wrapId, tem);
  if (tem) texto(alvoId, conteudo);
}

const TOM = {
  Rascunho: '', Enviada: 'info', Visualizada: 'info',
  Aceita: 'ok', Recusada: 'ruim', Expirada: 'warn',
};

let proposta = null;

function render(dados) {
  proposta = dados;

  texto('pub-owner', dados.owner_name || dados.company_name || '—');
  texto('pub-number', dados.number || '');
  texto('pub-date', `emitida em ${formata(dados.created_at, dataLonga)}`);
  texto('pub-title', dados.title);
  texto('pub-client', dados.client_name || '—');
  texto('pub-company', dados.client_company || '—');

  const selo = document.getElementById('pub-status');
  selo.textContent = dados.status;
  selo.className = `pubpill${TOM[dados.status] ? ` pubpill--${TOM[dados.status]}` : ''}`;

  mostrar('pub-valid-row', Boolean(dados.valid_until));
  if (dados.valid_until) texto('pub-valid', formata(dados.valid_until, dataLonga));

  const corpo = document.getElementById('pub-items');
  clear(corpo);
  for (const item of dados.items) {
    corpo.append(el('tr', {}, [
      el('td', { text: item.description }),
      el('td', { class: 'num', text: String(item.qty).replace('.', ',') }),
      el('td', { class: 'num', text: brl(item.unit_price) }),
      el('td', { class: 'num', text: brl(item.total) }),
    ]));
  }

  texto('pub-subtotal', brl(dados.subtotal));
  mostrar('pub-discount-row', dados.discount > 0);
  if (dados.discount > 0) texto('pub-discount', `− ${brl(dados.discount)}`);
  texto('pub-total', brl(dados.total));

  secao('pub-terms-wrap', 'pub-terms', dados.terms);
  secao('pub-delivery-wrap', 'pub-delivery', dados.delivery);
  secao('pub-notes-wrap', 'pub-notes', dados.notes);

  const respondida = Boolean(dados.decided_at);
  const aceita = dados.status === 'Aceita';

  mostrar('pub-decided', respondida);
  if (respondida) {
    texto('pub-decided-title', aceita ? 'Proposta aceita' : 'Proposta recusada');
    texto('pub-decided-meta', `por ${dados.decided_by} em ${formata(dados.decided_at, dataCurta)}`);
    const seloDec = document.getElementById('pub-decided-seal');
    seloDec.className = `decided__seal${aceita ? '' : ' decided__seal--no'}`;
    seloDec.querySelector('use').setAttribute('href', aceita ? '#i-trophy' : '#i-close');
    document.getElementById('pub-decided').classList.toggle('decided--no', !aceita);
  }

  mostrar('pub-expired', !respondida && dados.expired);

  mostrar('pub-decide', !respondida && !dados.expired);

  document.title = `${dados.number} — ${dados.title}`;
  mostrar('pub-loading', false);
  mostrar('pub-doc', true);
}

function falhar(mensagem) {
  mostrar('pub-loading', false);
  mostrar('pub-doc', false);
  mostrar('pub-error', true);
  if (mensagem) texto('pub-error-text', mensagem);
}

async function carregar() {
  const token = tokenDaUrl();
  if (!token || token.length < 20) { falhar(); return; }

  try {
    const resposta = await fetch(`${BASE}/${encodeURIComponent(token)}`, {
      headers: { Accept: 'application/json' },
    });
    if (!resposta.ok) {
      falhar(resposta.status === 404
        ? 'Este link não existe mais ou foi digitado incompleto. Peça um link novo para quem enviou a proposta.'
        : 'Não foi possível abrir a proposta agora. Tente novamente em alguns minutos.');
      return;
    }
    render(await resposta.json());
  } catch {
    falhar('Não foi possível falar com o servidor. Verifique sua conexão e recarregue a página.');
  }
}

let decisaoEscolhida = 'aceita';

async function responder(evento) {
  evento.preventDefault();
  const erro = document.getElementById('pub-form-error');
  const nome = document.getElementById('pub-name').value.trim();
  erro.hidden = true;

  if (nome.length < 2) {
    erro.textContent = 'Escreva seu nome completo para registrar a resposta.';
    erro.hidden = false;
    document.getElementById('pub-name').focus();
    return;
  }

  const botoes = [...document.querySelectorAll('#pub-form [data-decision]')];
  botoes.forEach((b) => { b.disabled = true; });

  try {
    const resposta = await fetch(`${BASE}/${encodeURIComponent(tokenDaUrl())}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        decision: decisaoEscolhida,
        name: nome,
        note: document.getElementById('pub-note').value.trim(),
      }),
    });
    const dados = await resposta.json().catch(() => null);

    if (!resposta.ok) {
      const detalhe = typeof dados?.detail === 'string' ? dados.detail : null;
      erro.textContent = detalhe || 'Não foi possível registrar sua resposta. Tente novamente.';
      erro.hidden = false;
      return;
    }

    render(dados);
    toast(decisaoEscolhida === 'aceita'
      ? 'Proposta aceita. Obrigado!'
      : 'Resposta registrada. Obrigado pelo retorno.', 'success', 6000);
  } catch {
    erro.textContent = 'Não foi possível falar com o servidor. Verifique sua conexão.';
    erro.hidden = false;
  } finally {
    botoes.forEach((b) => { b.disabled = false; });
  }
}

function iniciar() {

  document.querySelectorAll('#pub-form [data-decision]').forEach((botao) => {
    botao.addEventListener('click', () => { decisaoEscolhida = botao.dataset.decision; });
  });
  document.getElementById('pub-form')?.addEventListener('submit', responder);
  document.getElementById('pub-print')?.addEventListener('click', () => window.print());
  carregar();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', iniciar, { once: true });
} else {
  iniciar();
}
