import * as api from './api.js';
import { el, clear, brl, icon, toast, confirmar } from './ui.js';

export const cobranca = {
  assinatura: null,
  planos: [],
  faturas: [],
  carregando: false,
};

export function planoInclui(recurso) {
  const lista = cobranca.assinatura?.recursos || [];
  return lista.includes(recurso);
}

export async function loadCobranca() {
  const [assinatura, planos] = await Promise.all([
    api.assinatura(),
    api.planos(),
  ]);
  cobranca.assinatura = assinatura;
  cobranca.planos = planos;
}

export async function loadFaturas() {
  cobranca.faturas = await api.faturas();
}

const moedaExata = new Intl.NumberFormat('pt-BR', {
  style: 'currency', currency: 'BRL',
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const moedaRedonda = new Intl.NumberFormat('pt-BR', {
  style: 'currency', currency: 'BRL',
  minimumFractionDigits: 0, maximumFractionDigits: 0,
});

function preco(centavos) {
  const n = Number(centavos) || 0;
  return (n % 100 === 0 ? moedaRedonda : moedaExata).format(n / 100);
}

const RECURSO_TEXTO = {
  automacoes: 'Automações',
  whatsapp: 'WhatsApp integrado',
  ia: 'Assistente de IA',
  relatorios_avancados: 'Relatórios avançados',
  propostas: 'Propostas com link para o cliente',
  varios_usuarios: 'Vários usuários na mesma conta',
  api_publica: 'API pública',
};

const BLOQUEIO_TITULO = {
  automacoes: 'As automações fazem parte do Pro',
  whatsapp: 'O WhatsApp integrado faz parte do Pro',
  ia: 'O assistente de IA faz parte do Pro',
  relatorios_avancados: 'Os relatórios avançados fazem parte do Pro',
  propostas: 'As propostas fazem parte do Pro',
  varios_usuarios: 'Vários usuários na mesma conta é um recurso do Pro',
  api_publica: 'A API pública faz parte do plano Empresa',
};

const STATUS_TEXTO = {
  gratuito: 'Plano gratuito',
  trial: 'Período de teste',
  ativa: 'Assinatura ativa',
  pendente: 'Aguardando o pagamento',
  vencida: 'Assinatura vencida',
  cancelada: 'Assinatura cancelada',
};

const STATUS_TOM = {
  gratuito: 'chip',
  trial: 'chip--ok',
  ativa: 'chip--ok',
  pendente: 'chip--warn',
  vencida: 'chip--warn',
  cancelada: 'chip--warn',
};

function data(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });
}

function frasePrincipal(a) {
  if (a.em_trial) {
    const n = a.dias_de_trial ?? 0;
    const quando = data(a.trial_ends_at);
    return n === 1
      ? `Último dia de teste do Pro. Termina ${quando}.`
      : `Faltam ${n} dias de teste do Pro. Termina ${quando}.`;
  }
  if (a.status === 'ativa') {
    return a.cancela_no_fim
      ? `Cancelada. O acesso Pro continua até ${data(a.current_period_end)}, e não renova depois disso.`
      : `Pro ativo. A próxima cobrança é ${data(a.current_period_end)}.`;
  }
  if (a.status === 'pendente') {
    return 'A cobrança foi criada e está esperando o pagamento. Assim que ele for confirmado, o Pro é liberado automaticamente.';
  }
  if (a.status === 'cancelada') {
    return 'A assinatura foi cancelada. A conta continua no plano Inicial, com todos os seus dados.';
  }
  if (a.pode_testar) {
    return `A conta está no plano Inicial, que é gratuito e não expira. Você ainda pode experimentar o Pro por ${a.dias_do_teste} dias, sem cartão.`;
  }
  return 'A conta está no plano Inicial, que é gratuito e não expira. Os seus leads, negócios e propostas continuam aqui.';
}

function cartaoEstado(a) {
  const corpo = [
    el('div', { class: 'cob__topo' }, [
      el('div', {}, [
        el('span', { class: 'cob__rotulo', text: 'Plano atual' }),
        el('strong', { class: 'cob__plano', text: a.plano_nome }),
      ]),
      el('span', {
        class: `chip ${STATUS_TOM[a.status] || 'chip--warn'}`,
        text: STATUS_TEXTO[a.status] || a.status,
      }),
    ]),
    el('p', { class: 'cob__frase', text: frasePrincipal(a) }),
  ];

  if (a.centavos > 0 && (a.status === 'ativa' || a.status === 'pendente')) {
    corpo.push(el('p', { class: 'cob__valor', text: `${preco(a.centavos)} por mês` }));
  }

  const acoes = [];
  if (a.status === 'ativa' && !a.cancela_no_fim) {
    acoes.push(el('button', {
      class: 'btn btn--ghost',
      attrs: { type: 'button' },
      on: { click: cancelarAssinatura },
    }, ['Cancelar assinatura']));
  }
  if (acoes.length) corpo.push(el('div', { class: 'cob__acoes' }, acoes));

  return el('div', { class: 'bezel' }, [el('div', { class: 'bezel__in cob__estado' }, corpo)]);
}

function listaRecursos(codigos, ativos) {
  return el('ul', { class: 'cob__recursos' }, codigos.map((c) => {
    const tem = ativos.includes(c);
    return el('li', { class: tem ? 'cob__rec cob__rec--sim' : 'cob__rec cob__rec--nao' }, [
      icon(tem ? 'check' : 'x-circle'),
      RECURSO_TEXTO[c] || c,
    ]);
  }));
}

function cartaoPlano(plano, atual, ligado) {
  const eOAtual = plano.codigo === atual.plano;
  const cabeca = [
    el('h3', { class: 'cob__pnome', text: plano.nome }),
    el('p', { class: 'cob__presumo', text: plano.resumo }),
    el('p', { class: 'cob__ppreco' }, [
      plano.centavos > 0 ? preco(plano.centavos) : (plano.codigo === 'inicial' ? 'Grátis' : 'Sob medida'),
      plano.centavos > 0 ? el('span', { class: 'cob__pmes', text: ' /mês' }) : null,
    ].filter(Boolean)),
  ];

  const corpo = [...cabeca, listaRecursos(Object.keys(RECURSO_TEXTO), plano.recursos)];

  if (eOAtual) {
    corpo.push(el('p', { class: 'cob__atual', text: 'É o seu plano agora.' }));
  } else if (plano.assinavel) {
    const escolha = [];

    if (atual.pode_testar) {
      escolha.push(
        el('button', {
          class: 'btn btn--primary',
          attrs: { type: 'button' },
          on: { click: ativarTeste },
        }, [`Experimentar ${atual.dias_do_teste} dias grátis`]),
        el('p', { class: 'cob__nota', text: 'Sem cartão. Uma vez por conta.' }),
      );
    }

    if (!ligado) {

      escolha.push(el('p', {
        class: 'cob__aviso',
        text: 'A assinatura paga ainda não está ligada neste servidor.',
      }));
    } else {
      if (atual.pode_testar) escolha.push(el('p', { class: 'cob__ou', text: 'ou assine agora' }));
      escolha.push(
        el('button', {
          class: atual.pode_testar ? 'btn btn--glass' : 'btn btn--primary',
          attrs: { type: 'button' },
          on: { click: () => assinar(plano.codigo, 'cartao') },
        }, ['Assinar no cartão']),
        el('button', {
          class: 'btn btn--glass',
          attrs: { type: 'button' },
          on: { click: () => assinar(plano.codigo, 'avulso') },
        }, ['Pagar com Pix ou boleto']),
        el('p', { class: 'cob__nota', text: 'No cartão, renova sozinho todo mês. No Pix ou boleto, você paga a cada mês.' }),
      );
    }
    corpo.push(el('div', { class: 'cob__escolha' }, escolha));
  } else if (plano.codigo === 'empresa') {
    corpo.push(el('a', {
      class: 'btn btn--glass', attrs: { href: '/planos#empresa' },
    }, ['Falar sobre o Empresa']));
  }

  return el('div', { class: `bezel cob__plano-card${eOAtual ? ' is-atual' : ''}` }, [
    el('div', { class: 'bezel__in' }, corpo),
  ]);
}

function painelFaturas(conteudo) {
  return el('div', { class: 'bezel' }, [
    el('div', { class: 'bezel__in panel' }, [conteudo]),
  ]);
}

function tabelaFaturas(linhas) {
  if (!linhas.length) {
    return el('p', { class: 'empty__text', text: 'Nenhuma cobrança ainda. Quando houver, ela aparece aqui com data, valor e forma de pagamento.' });
  }
  return el('div', { class: 'tablewrap' }, [
    el('table', { class: 'table' }, [
      el('thead', {}, [el('tr', {}, [
        el('th', { attrs: { scope: 'col' } }, ['Data']),
        el('th', { attrs: { scope: 'col' } }, ['Plano']),
        el('th', { attrs: { scope: 'col' } }, ['Forma']),
        el('th', { attrs: { scope: 'col' } }, ['Situação']),
        el('th', { class: 'num', attrs: { scope: 'col' } }, ['Valor']),
      ])]),
      el('tbody', {}, linhas.map((f) => el('tr', {}, [
        el('td', {}, [data(f.pago_em || f.criado_em)]),
        el('td', {}, [f.plano === 'pro' ? 'Pro' : f.plano || '—']),
        el('td', {}, [f.metodo || '—']),
        el('td', {}, [
          el('span', {
            class: `chip ${f.status === 'aprovado' ? 'chip--ok' : 'chip--warn'}`,
            text: f.status,
          }),
        ]),
        el('td', { class: 'num' }, [preco(f.centavos)]),
      ]))),
    ]),
  ]);
}

export function renderCobranca() {
  const alvo = document.getElementById('cob-conteudo');
  if (!alvo) return;
  clear(alvo);

  const a = cobranca.assinatura;
  if (!a) {
    alvo.append(el('p', { class: 'empty__text', text: 'Carregando…' }));
    return;
  }

  alvo.append(cartaoEstado(a));

  if (a.pagamento_modo === 'teste') {
    alvo.append(el('p', {
      class: 'cob__sandbox',
      text: 'Este servidor está usando credenciais de teste do Mercado Pago. Nenhuma cobrança aqui é real.',
    }));
  }

  alvo.append(el('h2', { class: 'cob__h2', text: 'Planos' }));
  alvo.append(el('div', { class: 'cob__planos' },
    cobranca.planos.map((p) => cartaoPlano(p, a, a.pagamento_ligado))));

  alvo.append(el('h2', { class: 'cob__h2', text: 'Histórico de cobrança' }));
  alvo.append(painelFaturas(tabelaFaturas(cobranca.faturas)));
}

async function assinar(plano, modo) {
  if (cobranca.carregando) return;
  cobranca.carregando = true;
  try {
    const r = await api.assinar(plano, modo);
    if (!r?.link) throw new Error('O servidor não devolveu link de pagamento.');

    window.location.assign(r.link);
  } catch (erro) {
    toast(erro?.message || 'Não foi possível iniciar o pagamento.', 'erro');
  } finally {
    cobranca.carregando = false;
  }
}

async function ativarTeste() {
  if (cobranca.carregando) return;
  cobranca.carregando = true;
  try {
    cobranca.assinatura = await api.ativarTeste();
    renderCobranca();
    toast(`Pro liberado por ${cobranca.assinatura.dias_do_teste} dias. Aproveite.`, 'ok');
  } catch (erro) {
    toast(erro?.message || 'Não foi possível ativar o teste.', 'erro');
  } finally {
    cobranca.carregando = false;
  }
}

async function cancelarAssinatura() {
  const ok = await confirmar({
    titulo: 'Cancelar a assinatura?',
    texto: 'O Pro continua funcionando até o fim do período que você já pagou. Depois disso a conta volta para o Inicial.',
    aviso: 'Nenhum dado é apagado. Os seus leads, negócios e propostas continuam na conta.',
    confirmar: 'Cancelar assinatura',
    cancelar: 'Manter assinatura',
  });
  if (!ok) return;
  try {
    cobranca.assinatura = await api.cancelarAssinatura();
    renderCobranca();
    toast('Assinatura cancelada. O acesso continua até o fim do período pago.', 'ok');
  } catch (erro) {
    toast(erro?.message || 'Não foi possível cancelar.', 'erro');
  }
}

export function bloqueio(recurso, descricao) {
  return el('div', { class: 'bezel bloq' }, [
    el('div', { class: 'bezel__in bloq__in' }, [
      icon('spark', 'ico bloq__ico'),
      el('h2', {
        class: 'bloq__h',
        text: BLOQUEIO_TITULO[recurso] || `${RECURSO_TEXTO[recurso] || recurso} faz parte do Pro`,
      }),
      el('p', { class: 'bloq__p', text: descricao }),
      el('a', { class: 'btn btn--primary', attrs: { href: '#/cobranca' } }, ['Ver os planos']),
    ]),
  ]);
}
