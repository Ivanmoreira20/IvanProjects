import * as api from './api.js';
import { el, icon, clear, brl, toast } from './ui.js';

const RECURSO_LABEL = {
  automacoes: 'Automações e follow-up automático',
  whatsapp: 'WhatsApp integrado',
  ia: 'Assistente de IA sobre os seus negócios',
  relatorios_avancados: 'Relatórios avançados de conversão',
  propostas: 'Propostas com link para o cliente',
  varios_usuarios: 'Equipe com papéis (Admin, Gestor, Vendedor)',
  api_publica: 'Integração por API',
};

const BASE = [
  'Leads e negócios sem limite',
  'Funil visual, dashboard e histórico',
  'Avisos do que parou de andar',
  'Exportação em CSV quando quiser',
];

let aberto = false;
let ocupado = false;

function cartao(plano, pagamentoLigado) {
  const itens = plano.codigo === 'pro'
    ? ['Tudo do Iniciante', ...plano.recursos.map((r) => RECURSO_LABEL[r]).filter(Boolean)]
    : BASE;

  const botoes = pagamentoLigado
    ? [
        el('button', {
          class: 'btn btn--primary pw__cta', attrs: { type: 'button' },
          on: { click: () => assinar(plano.codigo, 'cartao') },
        }, [el('span', { text: 'Assinar no cartão' })]),
        el('button', {
          class: 'btn btn--ghost btn--sm pw__cta', attrs: { type: 'button' },
          on: { click: () => assinar(plano.codigo, 'avulso') },
        }, [el('span', { text: 'Pagar com Pix ou boleto' })]),
      ]
    : [el('p', { class: 'pw__aviso', text: 'O pagamento está sendo configurado. Tente novamente em instantes.' })];

  return el('article', { class: `pw__plano${plano.codigo === 'pro' ? ' pw__plano--destaque' : ''}` }, [
    plano.codigo === 'pro' ? el('span', { class: 'pw__flag', text: 'Mais completo' }) : null,
    el('h3', { class: 'pw__pnome', text: plano.nome }),
    el('p', { class: 'pw__pdesc', text: plano.resumo }),
    el('p', { class: 'pw__preco' }, [
      el('strong', { text: brl(plano.preco) }),
      el('span', { class: 'pw__mes', text: '/mês' }),
    ]),
    el('ul', { class: 'pw__lista' }, itens.map((t) => el('li', {}, [icon('check'), el('span', { text: t })]))),
    ...botoes,
  ]);
}

async function assinar(plano, modo) {
  if (ocupado) return;
  ocupado = true;
  try {
    const r = await api.assinar(plano, modo);
    if (!r?.link) throw new Error('O servidor não devolveu link de pagamento.');

    window.location.assign(r.link);
  } catch (erro) {
    toast(erro?.message || 'Não foi possível iniciar o pagamento.', 'error');
  } finally {
    ocupado = false;
  }
}

async function exportar() {
  try {
    const dados = await api.exportMyData();
    const blob = new Blob([JSON.stringify(dados, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'meus-dados-vertex.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('Seus dados foram baixados.', 'success');
  } catch (erro) {
    toast(erro?.message || 'Não foi possível exportar.', 'error');
  }
}

function textoDeContexto(info) {
  if (info?.status_assinatura === 'vencida') {
    return 'Sua assinatura venceu. Renove para voltar a usar o Vertex — nada do que você cadastrou foi perdido.';
  }
  if (info?.status_assinatura === 'pendente') {
    return 'Estamos aguardando a confirmação do seu pagamento. Assim que o Mercado Pago confirmar, o acesso volta sozinho.';
  }
  return 'O Vertex deixou de ter plano gratuito. Escolha um plano para continuar de onde você parou — os seus dados continuam aqui, intactos.';
}

export async function mostrarPaywall(info = {}) {
  const tela = document.getElementById('paywall-screen');
  if (!tela || aberto) return;
  aberto = true;

  document.getElementById('app-shell')?.classList.add('is-hidden');
  document.getElementById('auth-screen')?.classList.add('is-hidden');
  tela.classList.remove('is-hidden');

  const sub = document.getElementById('paywall-sub');
  if (sub) sub.textContent = textoDeContexto(info);

  const caixa = document.getElementById('paywall-planos');
  if (!caixa) return;
  clear(caixa);
  caixa.append(el('p', { class: 'pw__aviso', text: 'Carregando os planos…' }));

  try {
    const [planos, assinatura] = await Promise.all([
      api.planos(),
      api.assinatura().catch(() => ({ pagamento_ligado: true })),
    ]);
    clear(caixa);
    for (const p of planos.filter((x) => x.assinavel)) {
      caixa.append(cartao(p, assinatura.pagamento_ligado !== false));
    }
  } catch {
    clear(caixa);
    caixa.append(el('p', { class: 'pw__aviso', text: 'Não foi possível carregar os planos. Recarregue a página.' }));
  }
}

export function wirePaywall({ onLogout } = {}) {
  document.getElementById('paywall-export')?.addEventListener('click', exportar);
  document.getElementById('paywall-logout')?.addEventListener('click', () => {
    if (onLogout) onLogout();
  });
}
