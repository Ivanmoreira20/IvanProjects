export const store = {
  proposals: [],
  propFilter: 'todas',
  automations: [],
  automationRuns: [],
  automationMeta: null,
  notifications: { unread: 0, items: [] },
  lossReasons: [],
  customFields: [],
  waConfig: null,
  waTemplates: [],
  tasks: [],

  leadId: null,
  leadActivities: [],
  leadProposals: [],
  leadChat: null,
  leadNegociacao: null,
  tlFilter: 'todos',

  intel: null,
  intelBanda: '',
  previsao: null,
  avancado: null,
  avancadoPeriodo: '90d',
  ativacao: null,
  iaStatus: null,
  iaConversa: [],
  iaOcupada: false,
};

export const hooks = {
  state: null,
  refreshData: () => aviso('refreshData'),
  renderCurrentPage: () => aviso('renderCurrentPage'),
  draw: () => aviso('draw'),
  go: (rota) => { location.hash = `#/${rota}`; },
};

function aviso(nome) {
  console.warn(`[vertex] hooks.${nome} chamado antes do boot.`);
}

export const OPEN_STATUSES = ['Prospecção', 'Qualificação', 'Proposta', 'Negociação'];
export const CLOSED_STATUSES = ['Ganho', 'Perdido'];
export const STATUSES = [...OPEN_STATUSES, ...CLOSED_STATUSES];

export const PROPOSAL_STATUSES = [
  'Rascunho', 'Enviada', 'Visualizada', 'Aceita', 'Recusada', 'Expirada',
];

export const ACTIVITY_META = {
  criacao:   { icone: 'i-spark',        rotulo: 'Criação',    cor: 'neutra' },
  nota:      { icone: 'i-note',         rotulo: 'Anotação',   cor: 'neutra' },
  ligacao:   { icone: 'i-phone',        rotulo: 'Ligação',    cor: 'contato' },
  reuniao:   { icone: 'i-users',        rotulo: 'Reunião',    cor: 'contato' },
  email:     { icone: 'i-mail',         rotulo: 'E-mail',     cor: 'contato' },
  whatsapp:  { icone: 'i-whats',        rotulo: 'WhatsApp',   cor: 'contato' },
  tarefa:    { icone: 'i-calendar',     rotulo: 'Tarefa',     cor: 'tarefa'  },
  etapa:     { icone: 'i-columns',      rotulo: 'Etapa',      cor: 'etapa'   },
  proposta:  { icone: 'i-doc',          rotulo: 'Proposta',   cor: 'proposta'},
  automacao: { icone: 'i-bolt',         rotulo: 'Automação',  cor: 'auto'    },
  ganho:     { icone: 'i-trophy',       rotulo: 'Ganho',      cor: 'ganho'   },
  perda:     { icone: 'i-x-circle',     rotulo: 'Perda',      cor: 'perda'   },
};

export const CONTACT_KINDS = new Set(['ligacao', 'reuniao', 'email', 'whatsapp']);

const dataHora = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
});
const soData = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });

export function quando(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? dataHora.format(d) : '—';
}

export function dia(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? soData.format(d) : '—';
}

export function relativo(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '—';
  const dias = Math.round((d.getTime() - Date.now()) / 86400000);
  if (dias === 0) return 'hoje';
  if (dias === 1) return 'amanhã';
  if (dias === -1) return 'ontem';
  return dias > 0 ? `em ${dias} dias` : `há ${Math.abs(dias)} dias`;
}

export function venceu(iso) {
  const d = iso ? new Date(iso) : null;
  return Boolean(d && !Number.isNaN(d.getTime()) && d.getTime() < Date.now());
}
