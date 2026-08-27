import * as api from './api.js';
import { toast, isModalOpen } from './ui.js';

const REMEMBER_KEY = 'vertex_remember';
const RESEND_SECONDS = 60;
const CODE_LENGTH = 6;

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

let onAuthenticated = null;
let wired = false;

let view = 'login';

let verifyOrigin = 'register';

let pendingEmail = '';
let pendingRemember = true;
let verifying = false;
let resending = false;
let resendTimer = null;
let resendLeft = 0;

let defaultHead = null;

let bootMessage = '';

const AUTH_MESSAGES = {
  email_not_verified: 'Sua conta ainda não foi confirmada.',
  unverified_email: 'Sua conta ainda não foi confirmada.',
  invalid_code: 'Código incorreto. Confira os 6 dígitos e tente de novo.',
  code_invalid: 'Código incorreto. Confira os 6 dígitos e tente de novo.',
  wrong_code: 'Código incorreto. Confira os 6 dígitos e tente de novo.',
  invalid_or_expired_code: 'Código inválido ou expirado. Peça um novo abaixo.',
  code_expired: 'Esse código expirou.',
  expired_code: 'Esse código expirou.',
  too_many_attempts: 'Você errou o código vezes demais. Peça um novo para continuar.',
  email_already_verified: 'Esta conta já está confirmada. É só entrar.',
  already_verified: 'Esta conta já está confirmada. É só entrar.',
  email_taken: 'Este e-mail já está cadastrado.',
  email_in_use: 'Este e-mail já está cadastrado.',
  user_not_found: 'Não encontramos uma conta com esse e-mail.',
  unknown_email: 'Não encontramos uma conta com esse e-mail.',
  rate_limited: 'Muitos pedidos seguidos. Aguarde alguns minutos.',
  invalid_credentials: 'E-mail ou senha incorretos.',
};

const LOOKS_LIKE_CODE = /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/;

function showError(id, message) {
  const box = document.getElementById(id);
  if (!box) return;
  box.textContent = message;
  box.hidden = false;
}

function clearError(id) {
  const box = document.getElementById(id);
  if (!box) return;
  box.textContent = '';
  box.hidden = true;
}

function clearAllErrors() {
  clearError('login-error');
  clearError('register-error');
  clearError('verify-error');
}

function setNote(message) {
  const box = document.getElementById('verify-note');
  if (!box) return;
  if (!message) {
    box.textContent = '';
    box.hidden = true;
    return;
  }
  box.textContent = message;
  box.hidden = false;
}

function errorBag(err) {
  const payload = err?.payload;
  if (!payload || typeof payload !== 'object') return null;
  const detail = payload.detail;
  if (detail && typeof detail === 'object') return { ...payload, ...detail };
  return payload;
}

function authCode(err) {
  const bag = errorBag(err);
  if (!bag) return '';
  for (const key of ['detail', 'code', 'error', 'status']) {
    const v = bag[key];
    if (typeof v === 'string' && LOOKS_LIKE_CODE.test(v.trim())) return v.trim();
  }
  return '';
}

function authEmail(err) {
  const bag = errorBag(err);
  const value = bag && bag.email;
  return typeof value === 'string' ? value.trim() : '';
}

function attemptsLeft(err) {
  const bag = errorBag(err);
  if (!bag) return null;
  for (const key of ['attempts_left', 'attempts_remaining', 'remaining_attempts', 'tentativas_restantes']) {
    const raw = bag[key];
    if (raw === null || raw === undefined || raw === '') continue;
    const n = Number(raw);
    if (Number.isFinite(n)) return Math.max(0, Math.trunc(n));
  }
  return null;
}

function humanize(err, fallback) {
  if (!err) return fallback;
  if (err.status === 429) return 'Muitos pedidos seguidos. Aguarde alguns minutos e tente de novo.';
  if (err.status === 0) return 'Não foi possível falar com o servidor. Verifique sua conexão.';

  const code = authCode(err);
  if (code && AUTH_MESSAGES[code]) return AUTH_MESSAGES[code];

  const msg = String(err.message || '').trim();
  if (msg && !LOOKS_LIKE_CODE.test(msg)) return msg;
  return fallback;
}

function isExpired(err) {
  if (err?.status === 410) return true;
  const code = authCode(err);
  if (code.includes('expired')) return true;
  const msg = String(err?.message || '');
  return /expirad/i.test(msg) && !/inv[áa]lid/i.test(msg);
}

function isExhausted(err) {
  const code = authCode(err);
  if (code === 'too_many_attempts' || code === 'code_exhausted') return true;
  return /tentativas incorretas|solicite um novo|pe[çc]a um novo/i.test(String(err?.message || ''));
}

function shake(node) {
  if (!node) return;
  node.classList.remove('is-shake');
  void node.offsetWidth;
  node.classList.add('is-shake');
  node.addEventListener('animationend', () => node.classList.remove('is-shake'), { once: true });
}

let emailEmRecuperacao = '';

function mostrarRecuperacao(ligado) {
  const entry = document.getElementById('auth-entry');
  const rec = document.getElementById('recuperar-entry');
  const verify = document.getElementById('panel-verify');
  if (!rec) return;
  if (entry) entry.hidden = ligado;
  if (verify && ligado) verify.hidden = true;
  rec.hidden = !ligado;

  const title = document.getElementById('auth-title');
  const sub = document.getElementById('auth-sub');
  if (ligado) {
    if (title) title.textContent = 'Recuperar acesso.';
    if (sub) sub.textContent = '';
  } else if (defaultHead) {
    if (title) title.textContent = defaultHead.title;
    if (sub) sub.textContent = defaultHead.sub;
  }

  const f1 = document.getElementById('recuperar-form');
  const f2 = document.getElementById('redefinir-form');
  if (f1) f1.hidden = false;
  if (f2) f2.hidden = true;
  ['recuperar-erro', 'redefinir-erro'].forEach((id) => {
    const n = document.getElementById(id); if (n) n.hidden = true;
  });
}

async function pedirCodigoDeSenha(e) {
  e.preventDefault();
  const erro = document.getElementById('recuperar-erro');
  if (erro) erro.hidden = true;
  const email = (document.getElementById('recuperar-email')?.value || '').trim();
  if (!email) return;
  try {
    await api.esqueciSenha(email);
  } catch (err) {

    if (err?.status === 429) {
      if (erro) { erro.textContent = err.message; erro.hidden = false; }
      return;
    }
    if (err?.status === 0) {
      if (erro) { erro.textContent = err.message; erro.hidden = false; }
      return;
    }
  }
  emailEmRecuperacao = email;
  const destino = document.getElementById('redefinir-destino');
  if (destino) destino.textContent = email;
  document.getElementById('recuperar-form').hidden = true;
  document.getElementById('redefinir-form').hidden = false;
  document.getElementById('redefinir-codigo')?.focus();
}

async function redefinirComCodigo(e) {
  e.preventDefault();
  const erro = document.getElementById('redefinir-erro');
  if (erro) erro.hidden = true;
  const codigo = (document.getElementById('redefinir-codigo')?.value || '').trim();
  const senha = document.getElementById('redefinir-senha')?.value || '';
  if (senha.length < 8) {
    if (erro) { erro.textContent = 'A senha precisa ter pelo menos 8 caracteres.'; erro.hidden = false; }
    return;
  }
  try {
    await api.redefinirSenha(emailEmRecuperacao, codigo, senha);
  } catch (err) {
    if (erro) { erro.textContent = err?.message || 'Não foi possível redefinir a senha.'; erro.hidden = false; }
    return;
  }
  mostrarRecuperacao(false);
  const campo = document.getElementById('login-email');
  if (campo) campo.value = emailEmRecuperacao;
  emailEmRecuperacao = '';
  toast('Senha redefinida. Entre com a nova senha.', 'success');
}

function wireRecuperacao() {
  document.getElementById('esqueci-btn')?.addEventListener('click', () => {
    const digitado = (document.getElementById('login-email')?.value || '').trim();
    mostrarRecuperacao(true);
    const campo = document.getElementById('recuperar-email');
    if (campo) { campo.value = digitado; campo.focus(); }
  });
  document.getElementById('recuperar-form')?.addEventListener('submit', pedirCodigoDeSenha);
  document.getElementById('redefinir-form')?.addEventListener('submit', redefinirComCodigo);
  document.querySelectorAll('[data-voltar-login]').forEach((b) => {
    b.addEventListener('click', () => mostrarRecuperacao(false));
  });
}

function captureHead() {
  if (defaultHead) return;
  defaultHead = {
    title: document.getElementById('auth-title')?.textContent || '',
    sub: document.getElementById('auth-sub')?.textContent || '',
  };
}

function showView(next) {
  const entry = document.getElementById('auth-entry');
  const verify = document.getElementById('panel-verify');
  const tabs = document.querySelector('.tabs');
  const tabLogin = document.getElementById('tab-login');
  const tabReg = document.getElementById('tab-register');
  const panelLogin = document.getElementById('panel-login');
  const panelReg = document.getElementById('panel-register');
  if (!entry || !verify || !tabs || !tabLogin || !tabReg || !panelLogin || !panelReg) return;

  captureHead();
  view = next;

  const isVerify = next === 'verify';
  entry.hidden = isVerify;
  verify.hidden = !isVerify;

  const title = document.getElementById('auth-title');
  const sub = document.getElementById('auth-sub');
  if (isVerify) {
    if (title) title.textContent = 'Falta um passo.';
    if (sub) sub.textContent = 'Confirme o e-mail para liberar o acesso ao painel.';
  } else {
    if (title) title.textContent = defaultHead.title;
    if (sub) sub.textContent = defaultHead.sub;
    stopResendCountdown();
  }

  const isLogin = next !== 'register';
  tabs.dataset.active = isLogin ? 'login' : 'register';
  tabLogin.classList.toggle('is-active', isLogin);
  tabReg.classList.toggle('is-active', !isLogin);
  tabLogin.setAttribute('aria-selected', String(isLogin));
  tabReg.setAttribute('aria-selected', String(!isLogin));
  panelLogin.hidden = isVerify || !isLogin;
  panelReg.hidden = isVerify || isLogin;
}

function selectTab(which) {
  showView(which === 'register' ? 'register' : 'login');
  clearAllErrors();
  setNote('');
}

function backToEntry() {
  stopResendCountdown();
  clearError('verify-error');
  setNote('');
  const code = document.getElementById('verify-code');
  if (code) code.value = '';
  pendingEmail = '';

  const target = verifyOrigin === 'login' ? 'login' : 'register';
  showView(target);
  const emailField = document.getElementById(target === 'login' ? 'login-email' : 'reg-email');
  if (emailField) {
    emailField.value = '';
    emailField.focus();
  }
}

function readRemember() {
  try {
    if (localStorage.getItem(REMEMBER_KEY) === '0') return false;
  } catch {  }
  return true;
}

function saveRemember(value) {
  try { localStorage.setItem(REMEMBER_KEY, value ? '1' : '0'); } catch {  }
}

function rememberChecked() {
  const box = document.getElementById('login-remember');
  return box ? box.checked : readRemember();
}

function setPending(button, pending, idleLabel) {
  if (!button) return;
  button.disabled = pending;
  const label = button.querySelector('span:not(.btn__orb)');
  if (label) label.textContent = pending ? 'Aguarde…' : idleLabel;
}

function paintResend() {
  const btn = document.getElementById('verify-resend');
  if (!btn) return;
  const waiting = resendLeft > 0;
  btn.disabled = waiting || resending;
  if (resending) btn.textContent = 'Enviando…';
  else if (waiting) btn.textContent = `Reenviar código em ${resendLeft}s`;
  else btn.textContent = 'Reenviar código';
}

function stopResendCountdown() {
  if (resendTimer) clearInterval(resendTimer);
  resendTimer = null;
  resendLeft = 0;
  paintResend();
}

function startResendCountdown(seconds = RESEND_SECONDS) {
  if (resendTimer) clearInterval(resendTimer);
  resendLeft = Math.max(0, Math.trunc(seconds));
  paintResend();
  if (resendLeft === 0) return;
  resendTimer = setInterval(() => {
    resendLeft -= 1;
    if (resendLeft <= 0) stopResendCountdown();
    else paintResend();
  }, 1000);
}

async function doResend({ force = false } = {}) {
  if (resending) return;
  if (!force && resendLeft > 0) return;
  if (verifyOrigin === 'device') {
    setNote('Para receber um novo código, volte e entre de novo com sua senha.');
    return;
  }
  if (!pendingEmail) {
    showError('verify-error', 'Recomece o cadastro para receber um novo código.');
    return;
  }

  resending = true;
  paintResend();
  clearError('verify-error');
  try {
    await api.resend(pendingEmail);
    setNote('Enviamos um novo código. Confira sua caixa de entrada e o spam.');
    startResendCountdown();
    document.getElementById('verify-code')?.focus();
  } catch (err) {
    setNote('');
    if (err?.status === 429) {
      showError('verify-error', 'Muitos reenvios seguidos. Aguarde alguns minutos e tente de novo.');
      startResendCountdown();
    } else {
      showError('verify-error', humanize(err, 'Não foi possível reenviar o código agora.'));
      stopResendCountdown();
    }
  } finally {
    resending = false;
    paintResend();
  }
}

async function openVerify(email, { origin = 'register', autoResend = false, note = '' } = {}) {
  pendingEmail = String(email || '').trim();
  verifyOrigin = origin;

  const mail = document.getElementById('verify-email');
  if (mail) mail.textContent = pendingEmail;

  const code = document.getElementById('verify-code');
  if (code) code.value = '';
  clearError('verify-error');
  setNote(note);

  showView('verify');
  requestAnimationFrame(() => code?.focus());

  if (autoResend) await doResend({ force: true });
  else startResendCountdown();
}

function normalizeCode(value) {
  return String(value || '').replace(/\D+/g, '').slice(0, CODE_LENGTH);
}

function onCodeInput() {
  const input = document.getElementById('verify-code');
  if (!input) return;
  const digits = normalizeCode(input.value);
  if (digits !== input.value) input.value = digits;
  if (digits.length > 0) clearError('verify-error');

  if (digits.length === CODE_LENGTH && !verifying) {
    const form = document.getElementById('verify-form');
    if (form?.requestSubmit) form.requestSubmit();
    else submitVerify();
  }
}

function onCodePaste(e) {
  const input = document.getElementById('verify-code');
  const text = (e.clipboardData || window.clipboardData)?.getData('text') || '';
  const match = text.replace(/[\s-]+/g, '').match(/\d{4,8}/);
  if (!input || !match) return;
  e.preventDefault();
  input.value = match[0].slice(0, CODE_LENGTH);
  onCodeInput();
}

async function submitVerify(e) {
  if (e && typeof e.preventDefault === 'function') e.preventDefault();
  if (verifying) return;

  const input = document.getElementById('verify-code');
  const button = document.getElementById('verify-submit');
  const code = normalizeCode(input?.value);

  clearError('verify-error');

  if (!pendingEmail) {
    showError('verify-error', 'Recomece o cadastro para receber um novo código.');
    return;
  }
  if (code.length !== CODE_LENGTH) {
    showError('verify-error', 'Digite os 6 dígitos do código que enviamos.');
    shake(input);
    input?.focus();
    return;
  }

  verifying = true;
  setPending(button, true, 'Confirmar e entrar');
  try {
    const res = verifyOrigin === 'device'
      ? await api.verifyDevice(pendingEmail, code, pendingRemember)
      : await api.verify(pendingEmail, code, pendingRemember);
    const user = (res && typeof res === 'object' && res.user) ? res.user : res;

    stopResendCountdown();
    setNote('');
    if (input) input.value = '';
    saveRemember(pendingRemember);
    pendingEmail = '';

    const first = String(user?.name || '').split(' ')[0] || 'você';
    toast(`E-mail confirmado. Boas-vindas, ${first}.`, 'success');
    showView('login');
    if (onAuthenticated) onAuthenticated(user);
  } catch (err) {
    shake(input);
    if (input) {
      input.value = '';
      input.focus();
    }

    if (err?.status === 429) {
      showError('verify-error', 'Muitas tentativas seguidas. Aguarde alguns minutos e tente de novo.');
    } else if (isExpired(err)) {
      stopResendCountdown();
      showError('verify-error', 'Esse código expirou. Toque em "Reenviar código" para receber um novo.');
    } else {
      let msg = humanize(err, 'Código incorreto. Confira os 6 dígitos e tente de novo.');
      const left = attemptsLeft(err);
      if (left !== null && left > 0) msg += ` Você ainda tem ${left} tentativa${left === 1 ? '' : 's'}.`;
      if (left === 0) msg += ' Peça um novo código para continuar.';
      if (isExhausted(err)) stopResendCountdown();
      showError('verify-error', msg);
    }
  } finally {
    verifying = false;
    setPending(button, false, 'Confirmar e entrar');
  }
}

async function submitLogin(e) {
  e.preventDefault();
  clearError('login-error');

  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const remember = document.getElementById('login-remember').checked;
  const button = document.getElementById('login-submit');

  if (!email || !password) {
    showError('login-error', 'Informe e-mail e senha para entrar.');
    return;
  }

  setPending(button, true, 'Entrar');
  try {
    const user = await api.login({ email, password, remember });
    saveRemember(remember);
    document.getElementById('login-password').value = '';
    toast(`Bem-vindo de volta, ${String(user?.name || '').split(' ')[0] || 'você'}.`, 'success');
    if (onAuthenticated) onAuthenticated(user);
  } catch (err) {
    const code = authCode(err);

    if (err?.status === 403 && (code === 'email_not_verified' || code === 'unverified_email')) {
      saveRemember(remember);
      pendingRemember = remember;
      document.getElementById('login-password').value = '';
      await openVerify(authEmail(err) || email, {
        origin: 'login',
        autoResend: true,
        note: 'Sua conta ainda não foi confirmada. Enviamos um novo código para o seu e-mail.',
      });
      return;
    }

    if (err?.status === 403 && code === 'device_verification') {
      saveRemember(remember);
      pendingRemember = remember;
      document.getElementById('login-password').value = '';
      await openVerify(authEmail(err) || email, {
        origin: 'device',
        note: 'Novo dispositivo detectado. Enviamos um código para o seu e-mail para confirmar que é você.',
      });
      return;
    }
    showError('login-error', humanize(err, 'Não foi possível entrar. Tente de novo.'));
    shake(document.getElementById('login-password'));
  } finally {
    setPending(button, false, 'Entrar');
  }
}

async function submitRegister(e) {
  e.preventDefault();
  clearError('register-error');

  const name = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-password').value;
  const confirm = document.getElementById('reg-password2').value;
  const remember = document.getElementById('reg-remember').checked;
  const button = document.getElementById('register-submit');

  if (!name || !email || !password) {
    showError('register-error', 'Preencha nome, e-mail e senha.');
    return;
  }
  if (password.length < 8) {
    showError('register-error', 'A senha precisa ter pelo menos 8 caracteres.');
    return;
  }
  if (password !== confirm) {
    showError('register-error', 'As senhas não coincidem.');
    document.getElementById('reg-password2').focus();
    return;
  }

  setPending(button, true, 'Criar conta');
  try {
    const res = await api.register({ name, email, password, remember });
    saveRemember(remember);
    pendingRemember = remember;
    document.getElementById('reg-password').value = '';
    document.getElementById('reg-password2').value = '';

    if (res && res.status === 'verification_sent') {
      await openVerify(res.email || email, { origin: 'register' });
      return;
    }

    const user = (res && typeof res === 'object' && res.user) ? res.user : res;
    if (user && user.id != null) {
      toast(`Conta criada. Boas-vindas, ${String(user.name || name).split(' ')[0]}.`, 'success');
      if (onAuthenticated) onAuthenticated(user);
      return;
    }

    await openVerify(email, { origin: 'register' });
  } catch (err) {
    showError('register-error', humanize(err, 'Não foi possível criar a conta.'));
  } finally {
    setPending(button, false, 'Criar conta');
  }
}

async function loadConfig() {
  const block = document.getElementById('oauth-block');
  let cfg = null;
  try {
    cfg = await api.config();
  } catch {
    cfg = null;
  }
  if (block) block.hidden = !(cfg && cfg.google_enabled === true);
  applyDeliveryNotice(cfg && cfg.email_delivery);
}

function applyDeliveryNotice(delivery) {
  const lead = document.querySelector('.verify__lead');
  const hint = document.getElementById('verify-code-hint');
  if (!lead) return;

  if (delivery !== 'console') {
    lead.dataset.delivery = 'smtp';
    return;
  }
  lead.dataset.delivery = 'console';

  const alvo = document.getElementById('verify-email');
  const email = alvo ? alvo.textContent : '';
  clearNode(lead);
  lead.append(
    document.createTextNode('O envio por e-mail ainda não foi configurado, então o código de 6 dígitos de '),
  );
  const forte = document.createElement('strong');
  forte.className = 'verify__mail';
  forte.id = 'verify-email';
  forte.textContent = email;
  lead.append(forte);
  lead.append(document.createTextNode(
    ' aparece na janela preta do Vertex CRM e no arquivo '
    + 'CODIGO-DE-VERIFICACAO.txt, na mesma pasta do programa.',
  ));

  if (hint) {
    hint.textContent = 'Pode colar o código inteiro. Para receber por e-mail de verdade, veja CONFIGURACAO.md.';
  }
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function goToGoogle() {
  const btn = document.getElementById('google-btn');
  const remember = rememberChecked();
  saveRemember(remember);
  if (btn) {
    btn.disabled = true;
    const label = btn.querySelector('span');
    if (label) label.textContent = 'Abrindo o Google…';
  }
  window.location.href = `/api/auth/google/start?remember=${remember ? 1 : 0}`;
}

function resetGoogleButton() {
  const btn = document.getElementById('google-btn');
  if (!btn) return;
  btn.disabled = false;
  const label = btn.querySelector('span');
  if (label) label.textContent = 'Entrar com Google';
}

function consumeUrlError() {
  const found = [];
  try { found.push(new URLSearchParams(location.search)); } catch {  }
  const hash = location.hash || '';
  const qi = hash.indexOf('?');
  if (qi >= 0) {
    try { found.push(new URLSearchParams(hash.slice(qi + 1))); } catch {  }
  }

  let value = '';
  for (const params of found) {
    const v = params.get('erro') || params.get('error');
    if (v) { value = v; break; }
  }
  if (!value) return '';

  try {
    const url = new URL(location.href);
    url.searchParams.delete('erro');
    url.searchParams.delete('error');
    let newHash = url.hash;
    const hi = newHash.indexOf('?');
    if (hi >= 0) {
      const hp = new URLSearchParams(newHash.slice(hi + 1));
      hp.delete('erro');
      hp.delete('error');
      const rest = hp.toString();
      newHash = newHash.slice(0, hi) + (rest ? `?${rest}` : '');
    }
    history.replaceState(null, '', url.pathname + url.search + newHash);
  } catch {  }

  return value;
}

function authVisible() {
  const screen = document.getElementById('auth-screen');
  return Boolean(screen) && !screen.classList.contains('is-hidden');
}

function onAuthKeydown(e) {
  if (!authVisible() || isModalOpen()) return;

  if (e.key === 'Escape') {

    if (view === 'verify') {
      e.preventDefault();
      backToEntry();
    }
    return;
  }
  if (e.key !== 'Tab') return;

  const card = document.querySelector('.auth__card');
  if (!card) return;
  const items = Array.from(card.querySelectorAll(FOCUSABLE))
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
  if (!items.length) return;

  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = card.contains(active);

  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault();
    first.focus();
  }
}

function wire() {
  if (wired) return;
  wired = true;
  captureHead();

  document.getElementById('tab-login')?.addEventListener('click', () => selectTab('login'));
  document.getElementById('tab-register')?.addEventListener('click', () => selectTab('register'));

  document.getElementById('panel-login')?.addEventListener('submit', submitLogin);
  document.getElementById('panel-register')?.addEventListener('submit', submitRegister);
  document.getElementById('verify-form')?.addEventListener('submit', submitVerify);

  const code = document.getElementById('verify-code');
  code?.addEventListener('input', onCodeInput);
  code?.addEventListener('paste', onCodePaste);

  document.getElementById('verify-resend')?.addEventListener('click', () => doResend());
  document.getElementById('verify-back')?.addEventListener('click', backToEntry);

  document.getElementById('google-btn')?.addEventListener('click', goToGoogle);

  window.addEventListener('pageshow', resetGoogleButton);

  document.getElementById('auth-screen')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-pw-toggle]');
    if (!btn) return;
    const input = document.getElementById(btn.dataset.pwToggle);
    if (!input) return;
    const reveal = input.type === 'password';
    input.type = reveal ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(reveal));
    btn.setAttribute('aria-label', reveal ? 'Ocultar senha' : 'Mostrar senha');
    const use = btn.querySelector('use');
    if (use) use.setAttribute('href', reveal ? '#i-eye-off' : '#i-eye');
    input.focus();
  });

  const remembered = readRemember();
  pendingRemember = remembered;
  ['login-remember', 'reg-remember'].forEach((id) => {
    const box = document.getElementById(id);
    if (!box) return;
    box.checked = remembered;
    box.addEventListener('change', () => {
      saveRemember(box.checked);
      pendingRemember = box.checked;
      const other = document.getElementById(id === 'login-remember' ? 'reg-remember' : 'login-remember');
      if (other) other.checked = box.checked;
    });
  });

  document.addEventListener('keydown', onAuthKeydown);
}

function tabFromHash() {
  const hash = (location.hash || '').toLowerCase();
  return /^#\/(registrar|cadastro|criar-conta)\b/.test(hash) ? 'register' : 'login';
}

export function initAuth(options = {}) {
  wireRecuperacao();
  onAuthenticated = options.onAuthenticated || null;
  wire();
  showView(tabFromHash());

  if (consumeUrlError() === 'google') {
    bootMessage = 'Não foi possível entrar com o Google. Tente novamente.';
  }

  loadConfig();
}

export function showAuth() {
  const auth = document.getElementById('auth-screen');
  const app = document.getElementById('app-shell');
  if (auth) auth.classList.remove('is-hidden');
  if (app) app.classList.add('is-hidden');
  document.getElementById('boot-splash')?.classList.add('is-hidden');
  document.body.style.overflow = '';

  stopResendCountdown();
  pendingEmail = '';
  verifying = false;
  resending = false;
  clearAllErrors();
  setNote('');
  resetGoogleButton();

  ['login-password', 'reg-password', 'reg-password2', 'verify-code'].forEach((id) => {
    const input = document.getElementById(id);
    if (input) input.value = '';
  });

  const tab = tabFromHash();
  showView(tab);

  if (bootMessage) {
    showError('login-error', bootMessage);
    bootMessage = '';
  }
  document.getElementById(tab === 'register' ? 'reg-name' : 'login-email')?.focus();
}

export function hideAuth() {
  stopResendCountdown();
  document.getElementById('boot-splash')?.classList.add('is-hidden');
  document.getElementById('auth-screen')?.classList.add('is-hidden');
  document.getElementById('app-shell')?.classList.remove('is-hidden');
}
