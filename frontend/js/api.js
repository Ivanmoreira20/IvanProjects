const BASE = '/api';
const CSRF_COOKIE = 'vertex_csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const MUTATING = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

let unauthorizedHandler = null;

export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = typeof fn === 'function' ? fn : null;
}

let paywallHandler = null;

export function setPaywallHandler(fn) {
  paywallHandler = typeof fn === 'function' ? fn : null;
}

function readCookie(name) {
  const target = name + '=';
  for (const raw of document.cookie ? document.cookie.split(';') : []) {
    const c = raw.trim();
    if (c.startsWith(target)) {
      try {
        return decodeURIComponent(c.slice(target.length));
      } catch {
        return c.slice(target.length);
      }
    }
  }
  return '';
}

async function readBody(res) {
  const type = res.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    const text = await res.text().catch(() => '');
    return text ? { message: text } : null;
  }
  return res.json().catch(() => null);
}

function serverMessage(payload) {
  if (!payload || typeof payload !== 'object') return '';
  for (const key of ['error', 'message', 'detail', 'msg']) {
    const v = payload[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }

  const errs = payload.errors;
  if (Array.isArray(errs) && errs.length) return String(errs[0]);
  if (errs && typeof errs === 'object') {
    const first = Object.values(errs)[0];
    if (Array.isArray(first) && first.length) return String(first[0]);
    if (typeof first === 'string') return first;
  }
  return '';
}

function fallbackMessage(status) {
  if (status === 400) return 'Dados inválidos. Revise os campos e tente de novo.';
  if (status === 403) return 'Sua sessão não pôde ser validada. Recarregue a página e tente de novo.';
  if (status === 404) return 'Registro não encontrado. Ele pode ter sido removido.';
  if (status === 409) return 'Este e-mail já está cadastrado.';
  if (status === 422) return 'Dados inválidos. Revise os campos e tente de novo.';
  if (status >= 500) return 'O servidor falhou ao responder. Tente novamente em instantes.';
  return 'Não foi possível completar a operação.';
}

async function request(path, options = {}) {
  const { method = 'GET', body, silent401 = false, publicRoute = false } = options;
  const headers = { Accept: 'application/json' };

  if (body !== undefined) headers['Content-Type'] = 'application/json';

  if (MUTATING.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers[CSRF_HEADER] = token;
  }

  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError('Não foi possível falar com o servidor. Verifique sua conexão.', 0);
  }

  if (res.status === 401) {

    const payload = await readBody(res);
    if (!silent401 && !publicRoute && unauthorizedHandler) {
      try { unauthorizedHandler(); } catch {  }
    }

    const message = publicRoute
      ? (serverMessage(payload) || 'Não foi possível confirmar seus dados. Tente de novo.')
      : 'Sua sessão expirou. Entre novamente.';
    throw new ApiError(message, 401, payload);
  }

  if (res.status === 402) {
    const payload = await readBody(res);

    if (payload && payload.erro === 'assinatura_necessaria' && paywallHandler) {
      try { paywallHandler(payload); } catch {  }
    }
    throw new ApiError(serverMessage(payload) || 'Assinatura necessária.', 402, payload);
  }

  if (res.status === 429) {

    const payload = await readBody(res);
    throw new ApiError(
      serverMessage(payload) || 'Muitas tentativas, aguarde alguns minutos.',
      429, payload,
    );
  }

  if (res.status === 204 || res.status === 205) return null;

  const payload = await readBody(res);

  if (!res.ok) {
    throw new ApiError(serverMessage(payload) || fallbackMessage(res.status), res.status, payload);
  }

  return payload;
}

export function config() {
  return request('/config', { publicRoute: true, silent401: true });
}

export function register({ name, email, password, remember }) {
  return request('/auth/register', {
    method: 'POST',
    body: { name, email, password, remember: Boolean(remember) },
    publicRoute: true,
    silent401: true,
  });
}

export function verify(email, code, remember) {
  return request('/auth/verify', {
    method: 'POST',
    body: { email, code: String(code || '').trim(), remember: Boolean(remember) },
    publicRoute: true,
    silent401: true,
  });
}

export function resend(email) {
  return request('/auth/resend', {
    method: 'POST',
    body: { email },
    publicRoute: true,
    silent401: true,
  });
}

export function login({ email, password, remember }) {
  return request('/auth/login', {
    method: 'POST',
    body: { email, password, remember: Boolean(remember) },
    publicRoute: true,
    silent401: true,
  });
}

export function logout() {
  return request('/auth/logout', { method: 'POST' });
}

export function me({ silent = true } = {}) {
  return request('/auth/me', { silent401: silent });
}

export function updateProfile({ name }) {
  return request('/me', { method: 'PATCH', body: { name } });
}

export function listLeads() {
  return request('/leads');
}

export function createLead(lead) {
  return request('/leads', { method: 'POST', body: lead });
}

export function updateLead(id, patch) {
  return request(`/leads/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function deleteLead(id) {
  return request(`/leads/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function stats() {
  return request('/stats');
}

export function followups() {
  return request('/followups');
}

export function semProximaAcao() {
  return request('/intel/sem-proxima-acao');
}

export function onboarding() {
  return request('/onboarding');
}

export function dispensarOnboarding(dispensar = true) {
  return request('/onboarding/dispensar', { method: 'POST', body: { dispensar } });
}

export function negociacao(id) {
  return request(`/leads/${id}/negociacao`);
}

export function importPreview(body) {
  return request('/import/preview', { method: 'POST', body });
}

export function importConfirm(body) {
  return request('/import/confirm', { method: 'POST', body });
}

export function esqueciSenha(email) {
  return request('/auth/forgot', { method: 'POST', body: { email }, publicRoute: true, silent401: true });
}

export function redefinirSenha(email, code, password) {
  return request('/auth/reset', {
    method: 'POST', body: { email, code, password }, publicRoute: true, silent401: true,
  });
}

export function trocarSenha(senhaAtual, senhaNova) {
  return request('/me/password', { method: 'POST', body: { senha_atual: senhaAtual, senha_nova: senhaNova } });
}

export function pedirTrocaDeEmail(novoEmail, senha) {
  return request('/me/email', { method: 'POST', body: { novo_email: novoEmail, senha } });
}

export function confirmarTrocaDeEmail(code) {
  return request('/me/email/confirm', { method: 'POST', body: { code } });
}

export function sessoes() {
  return request('/me/sessions');
}

export function encerrarOutrasSessoes() {
  return request('/me/sessions', { method: 'DELETE' });
}

export function avatarUrl(userId, chave) {
  if (!userId || !chave) return '';
  return `${BASE}/avatars/${encodeURIComponent(userId)}?v=${encodeURIComponent(chave)}`;
}

export function enviarAvatar(base64) {
  return request('/me/avatar', { method: 'POST', body: { imagem: base64 } });
}

export function removerAvatar() {
  return request('/me/avatar', { method: 'DELETE' });
}

export function exportMyData() {
  return request('/me/export');
}

export function deleteAccount(body) {
  return request('/me/delete', { method: 'POST', body });
}

export function leadImpact(id) {
  return request(`/leads/${encodeURIComponent(id)}/impact`);
}

export function adminOverview() {
  return request('/admin/overview');
}

export function adminAccounts(q = '', { limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return request(`/admin/accounts?${params.toString()}`);
}

export function adminAccount(id) {
  return request(`/admin/accounts/${encodeURIComponent(id)}`);
}

export function adminPlanInterests(limit = 100) {
  return request(`/admin/plan-interests?limit=${encodeURIComponent(limit)}`);
}

export function adminRevenue(months = 12) {
  return request(`/admin/revenue?months=${encodeURIComponent(months)}`);
}

export function adminSecurityEvents(limit = 50) {
  return request(`/admin/security-events?limit=${encodeURIComponent(limit)}`);
}

export function adminSaude() {
  return request('/admin/saude');
}

export function mktContatos() {
  return request('/marketing/contatos');
}
export function mktConsent(email, status) {
  return request('/marketing/consent', { method: 'POST', body: { email, status } });
}
export function mktSuppress(email) {
  return request('/marketing/suppress', { method: 'POST', body: { email } });
}
export function mktCampaigns() {
  return request('/marketing/campaigns');
}
export function mktCreateCampaign(data) {
  return request('/marketing/campaigns', { method: 'POST', body: data });
}
export function mktCampaign(id) {
  return request(`/marketing/campaigns/${encodeURIComponent(id)}`);
}
export function mktPreview(id) {
  return request(`/marketing/campaigns/${encodeURIComponent(id)}/preview`);
}
export function mktTest(id) {
  return request(`/marketing/campaigns/${encodeURIComponent(id)}/test`, { method: 'POST', body: {} });
}
export function mktSend(id) {
  return request(`/marketing/campaigns/${encodeURIComponent(id)}/send`, { method: 'POST', body: {} });
}

export function org() {
  return request('/org');
}

export function createInvite(role, email = '') {
  return request('/org/invites', { method: 'POST', body: { role, email } });
}

export function listInvites() {
  return request('/org/invites');
}

export function revokeInvite(id) {
  return request(`/org/invites/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function acceptInvite(token) {
  return request('/org/invites/accept', { method: 'POST', body: { token } });
}

export function changeMemberRole(userId, role) {
  return request(`/org/members/${encodeURIComponent(userId)}`, { method: 'PATCH', body: { role } });
}

export function removeMember(userId) {
  return request(`/org/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
}

export function orgAudit() {
  return request('/org/audit');
}

export function assignLeadOwner(leadId, ownerUserId) {
  return request(`/leads/${encodeURIComponent(leadId)}/owner`, {
    method: 'PATCH', body: { owner_user_id: ownerUserId },
  });
}

export function leadActivities(id) {
  return request(`/leads/${encodeURIComponent(id)}/activities`);
}

export function createActivity(leadId, body) {
  return request(`/leads/${encodeURIComponent(leadId)}/activities`, { method: 'POST', body });
}

export function finishActivity(id) {
  return request(`/activities/${encodeURIComponent(id)}/done`, { method: 'POST' });
}

export function deleteActivity(id) {
  return request(`/activities/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function tasks() {
  return request('/tasks');
}

export function notifications() {
  return request('/notifications');
}

export function readNotification(id) {
  return request(`/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' });
}

export function readAllNotifications() {
  return request('/notifications/read-all', { method: 'POST' });
}

export function lossReasons() {
  return request('/loss-reasons');
}

export function createLossReason(label) {
  return request('/loss-reasons', { method: 'POST', body: { label } });
}

export function updateLossReason(id, patch) {
  return request(`/loss-reasons/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function deleteLossReason(id) {
  return request(`/loss-reasons/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function lossReport() {
  return request('/reports/losses');
}

export function customFields() {
  return request('/custom-fields');
}

export function createCustomField(body) {
  return request('/custom-fields', { method: 'POST', body });
}

export function updateCustomField(id, patch) {
  return request(`/custom-fields/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function deleteCustomField(id) {
  return request(`/custom-fields/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function customFieldUsage(id) {
  return request(`/custom-fields/${encodeURIComponent(id)}/usage`);
}

export function search(q) {
  return request(`/search?q=${encodeURIComponent(q)}`);
}

export function listProposals(leadId) {
  const qs = leadId ? `?lead_id=${encodeURIComponent(leadId)}` : '';
  return request(`/proposals${qs}`);
}

export function getProposal(id) {
  return request(`/proposals/${encodeURIComponent(id)}`);
}

export function createProposal(body) {
  return request('/proposals', { method: 'POST', body });
}

export function updateProposal(id, patch) {
  return request(`/proposals/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function sendProposal(id, body) {
  return request(`/proposals/${encodeURIComponent(id)}/send`, { method: 'POST', body });
}

export function deleteProposal(id) {
  return request(`/proposals/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function automationMeta() {
  return request('/automations/meta');
}

export function listAutomations() {
  return request('/automations');
}

export function createAutomation(body) {
  return request('/automations', { method: 'POST', body });
}

export function updateAutomation(id, patch) {
  return request(`/automations/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function deleteAutomation(id) {
  return request(`/automations/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function automationRuns() {
  return request('/automation-runs');
}

export function waConfig() {
  return request('/whatsapp/config');
}

export function saveWaConfig(body) {
  return request('/whatsapp/config', { method: 'PUT', body });
}

export function checkWa() {
  return request('/whatsapp/check', { method: 'POST' });
}

export function disconnectWa() {
  return request('/whatsapp/disconnect', { method: 'POST' });
}

export function waTemplates() {
  return request('/whatsapp/templates');
}

export function createWaTemplate(body) {
  return request('/whatsapp/templates', { method: 'POST', body });
}

export function deleteWaTemplate(id) {
  return request(`/whatsapp/templates/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function leadConversation(leadId) {
  return request(`/leads/${encodeURIComponent(leadId)}/whatsapp`);
}

export function sendWhatsapp(leadId, body) {
  return request(`/leads/${encodeURIComponent(leadId)}/whatsapp`, { method: 'POST', body });
}

export function intelResumo() {
  return request('/intel/resumo');
}

export function intelLeads(banda = '') {
  const q = banda ? `?banda=${encodeURIComponent(banda)}` : '';
  return request(`/intel/leads${q}`);
}

export function intelLead(leadId) {
  return request(`/intel/leads/${encodeURIComponent(leadId)}`);
}

export function previsao() {
  return request('/intel/previsao');
}

export function ativacao() {
  return request('/intel/ativacao');
}

export function relatorioAvancado(periodo = '90d') {
  return request(`/reports/advanced?periodo=${encodeURIComponent(periodo)}`);
}

export function iaStatus() {
  return request('/ai/status');
}

export function iaPerguntar(body) {
  return request('/ai/ask', { method: 'POST', body });
}

export function planos() {
  return request('/billing/plans');
}

export function assinatura() {
  return request('/billing/me');
}

export function faturas() {
  return request('/billing/invoices');
}

export function assinar(plano, modo) {
  return request('/billing/assinar', { method: 'POST', body: { plano, modo } });
}

export function ativarTeste() {
  return request('/billing/testar', { method: 'POST', body: {} });
}

export function cancelarAssinatura() {
  return request('/billing/cancelar', { method: 'POST', body: {} });
}
