import * as api from './api.js';
import { el, icon, clear, toast } from './ui.js';

let estado = null;
let ocupado = false;

let viuIncompleto = false;
let celebrado = false;

export function estadoOnboarding() {
  return estado;
}

export async function loadOnboarding() {
  try {
    estado = await api.onboarding();
  } catch {
    estado = null;
  }
  return estado;
}

function linhaPasso(passo, indice, ir) {
  const marca = passo.feito
    ? el('span', { class: 'pp__marca pp__marca--ok' }, [icon('check')])
    : el('span', { class: 'pp__marca', text: String(indice + 1) });

  const texto = [
    el('p', { class: 'pp__titulo', text: passo.titulo }),
    el('p', { class: 'pp__porque', text: passo.porque }),
  ];

  const filhos = [marca, el('div', { class: 'pp__txt' }, texto)];

  if (!passo.feito) {
    filhos.push(el('button', {
      class: 'btn btn--ghost pp__btn',
      attrs: { type: 'button' },
      on: { click: () => ir(passo) },
    }, [el('span', { class: 'btn__txt', text: passo.acao }), icon('arrow-ur')]));
  }

  return el('li', {
    class: `pp__item${passo.feito ? ' is-ok' : ''}`,
  }, filhos);
}

function celebrar(caixa) {
  const cabeca = el('div', { class: 'pp__head pp__head--done' }, [
    el('span', { class: 'pp__selo pp__selo--done', attrs: { 'aria-hidden': 'true' } }, [icon('check')]),
    el('div', { class: 'pp__headtxt' }, [
      el('h2', { class: 'pp__titulo-h', text: 'Você concluiu os primeiros passos! 🎉' }),
      el('p', { class: 'pp__sub', text: 'Sua operação está montada no Vertex. Este guia sai do painel — bom trabalho.' }),
    ]),
  ]);
  const ok = el('button', {
    class: 'btn btn--primary pp__btn',
    attrs: { type: 'button' },
    on: { click: () => { celebrado = true; caixa.hidden = true; clear(caixa); } },
  }, [el('span', { class: 'btn__txt', text: 'Ótimo!' })]);
  clear(caixa);
  caixa.append(el('div', { class: 'bezel__in pp__in pp__in--done' }, [cabeca, ok]));
  caixa.hidden = false;
}

export function renderOnboarding(go, novoLead) {
  const caixa = document.getElementById('dash-primeiros');
  if (!caixa) return;

  if (!estado || estado.dispensado || estado.oculto_auto) {
    caixa.hidden = true;
    clear(caixa);
    return;
  }

  if (estado.completo) {
    if (viuIncompleto && !celebrado) {
      celebrar(caixa);
    } else {
      caixa.hidden = true;
      clear(caixa);
    }
    return;
  }

  viuIncompleto = true;

  const ir = (passo) => {
    if (passo.id === 'lead') { novoLead(); return; }

    if ((passo.id === 'contato' || passo.id === 'proxima') && estado.foco_lead_id) {
      location.hash = `#/lead/${estado.foco_lead_id}`;
      return;
    }
    go(passo.rota);
  };

  const feitos = estado.concluidos;
  const total = estado.total;
  const pct = total ? Math.round((feitos / total) * 100) : 0;

  const barra = el('div', {
    class: 'pp__barra',
    attrs: {
      role: 'progressbar',
      'aria-valuenow': feitos,
      'aria-valuemin': 0,
      'aria-valuemax': total,
      'aria-label': `${feitos} de ${total} primeiros passos concluídos`,
    },
  }, [el('span', { class: 'pp__barra-in' })]);
  barra.firstChild.style.width = `${pct}%`;

  const dispensar = el('button', {
    class: 'iconbtn pp__fechar',
    attrs: { type: 'button', 'aria-label': 'Ocultar os primeiros passos' },
    on: { click: () => esconder(go, novoLead) },
  }, [icon('close')]);

  const cabeca = el('div', { class: 'pp__head' }, [
    el('span', { class: 'pp__selo', attrs: { 'aria-hidden': 'true' } }, [icon('target')]),
    el('div', { class: 'pp__headtxt' }, [
      el('h2', { class: 'pp__titulo-h', text: 'Primeiros passos' }),
      el('p', {
        class: 'pp__sub',

        text: `${total} passos para montar a sua operação no Vertex.`,
      }),
    ]),
    el('span', { class: 'chip pp__chip', text: `${feitos} de ${total}` }),
    dispensar,
  ]);

  const lista = el('ol', { class: 'pp__lista' },
    estado.passos.map((p, i) => linhaPasso(p, i, ir)));

  clear(caixa);
  caixa.append(el('div', { class: 'bezel__in pp__in' }, [cabeca, barra, lista]));
  caixa.hidden = false;
}

async function esconder(go, novoLead) {
  if (ocupado) return;
  ocupado = true;
  try {
    estado = await api.dispensarOnboarding(true);
    renderOnboarding(go, novoLead);
    toast('Primeiros passos ocultados. Você pode trazer de volta em Configurações.');
  } catch {
    toast('Não foi possível ocultar agora. Tente de novo.', 'error');
  } finally {
    ocupado = false;
  }
}

export async function reexibirOnboarding() {
  estado = await api.dispensarOnboarding(false);
  return estado;
}
