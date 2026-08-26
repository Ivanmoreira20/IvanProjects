const $  = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

if ('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches){
  document.documentElement.classList.add('js');
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    const sibs = [...e.target.parentElement.children].filter(c => c.classList.contains('rv'));
    e.target.style.transitionDelay = (sibs.indexOf(e.target) % 4) * 70 + 'ms';
    e.target.classList.add('is-in');
    io.unobserve(e.target);
  }), {threshold:.12});
  $$('.rv').forEach(el => io.observe(el));
}

(() => {
  const hd = $('#hd');
  if (!hd) return;
  const sent = document.createElement('div');
  sent.setAttribute('aria-hidden', 'true');
  sent.style.cssText = 'position:absolute;top:0;left:0;width:1px;height:1px;pointer-events:none';
  document.body.prepend(sent);
  new IntersectionObserver(([e]) => hd.classList.toggle('stuck', !e.isIntersecting), {threshold:0}).observe(sent);
})();

(() => {
  const burger = $('#burger'), mnav = $('#mnav');
  if (!burger || !mnav) return;
  const close = () => { mnav.classList.remove('open'); burger.setAttribute('aria-expanded', 'false'); };
  burger.addEventListener('click', () => {
    const o = mnav.classList.toggle('open');
    burger.setAttribute('aria-expanded', String(o));
  });
  mnav.addEventListener('click', e => { if (e.target.closest('a')) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && mnav.classList.contains('open')) { close(); burger.focus(); }
  });
  addEventListener('resize', () => { if (innerWidth > 1000) close(); });
})();

(() => {
  const tabs = $$('#vtabs .vtab');
  if (!tabs.length) return;
  const sel = t => tabs.forEach(x => {
    const on = x === t;
    x.setAttribute('aria-selected', String(on));
    x.tabIndex = on ? 0 : -1;
    const pane = document.getElementById(x.getAttribute('aria-controls'));
    if (pane) pane.hidden = !on;
  });
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => sel(t));
    t.addEventListener('keydown', e => {
      const step = {ArrowDown:1, ArrowRight:1, ArrowUp:-1, ArrowLeft:-1}[e.key];
      if (step) { e.preventDefault(); const n = tabs[(i + step + tabs.length) % tabs.length]; sel(n); n.focus(); }
      if (e.key === 'Home') { e.preventDefault(); sel(tabs[0]); tabs[0].focus(); }
      if (e.key === 'End') { e.preventDefault(); const l = tabs[tabs.length - 1]; sel(l); l.focus(); }
    });
  });
  sel(tabs.find(t => t.getAttribute('aria-selected') === 'true') || tabs[0]);
})();

$$('img').forEach(im => im.addEventListener('error', () => {
  const f = im.closest('figure');
  if (f) f.innerHTML = '<div class="missing">Captura indisponível no momento.</div>';
}, {once:true}));

(() => {
  const inp = $('#roiIn'), res = $('#roiRes'), det = $('#roiDet');
  if (!inp || !res || !det) return;

  const planos = $$('[data-plano][data-centavos]').map(el => ({
    codigo: el.dataset.plano,
    nome: (el.querySelector('h3')?.childNodes[0]?.textContent || el.dataset.plano).trim(),
    v: Number(el.dataset.centavos) / 100,
  })).filter(p => p.v > 0);
  const inicial = planos.find(p => p.codigo === 'inicial');
  const pro = planos.find(p => p.codigo === 'pro');

  const BRL = n => n.toLocaleString('pt-BR', {style:'currency', currency:'BRL', maximumFractionDigits:2});
  const meses = m => m === 1 ? '1 mês' : m + ' meses';
  const ler = t => {
    const n = parseFloat(String(t).replace(/[^\d,.]/g, '').replace(/\./g, '').replace(',', '.'));
    return Number.isFinite(n) && n > 0 ? n : 0;
  };

  const conta = () => {
    const v = ler(inp.value);
    if (!v || !inicial) {
      res.textContent = '—';
      det.textContent = 'Digite o valor médio de uma venda para ver a conta.';
      return;
    }
    const mi = Math.floor(v / inicial.v);
    if (mi < 1) {
      res.textContent = 'menos de 1 mês';
      det.textContent = `Uma venda de ${BRL(v)} não chega a cobrir um mês do ${inicial.nome} (${BRL(inicial.v)}). `
        + 'Com um ticket desse tamanho, o ganho do Vertex está no volume de vendas que passam pela sua operação.';
      return;
    }
    res.textContent = `${meses(mi)} do ${inicial.nome}`;
    const mp = pro ? Math.floor(v / pro.v) : 0;
    det.textContent = (mp >= 1 ? `Ou ${meses(mp)} do ${pro.nome}. ` : '')
      + `Valor considerado: ${BRL(v)}. É valor ÷ mensalidade, sem taxa inventada.`;
  };

  inp.addEventListener('input', conta);
  conta();
})();

$$('form.roi-form, #roi form').forEach(f => f.addEventListener('submit', e => e.preventDefault()));
