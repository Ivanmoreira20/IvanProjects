document.documentElement.classList.add('js-reveal');

const burger = document.getElementById('hd-burger');
const nav = document.getElementById('hd-nav');

if (burger && nav) {
  const fechar = () => {
    nav.classList.remove('is-open');
    burger.setAttribute('aria-expanded', 'false');
    burger.setAttribute('aria-label', 'Abrir menu');
  };

  burger.addEventListener('click', () => {
    const aberto = nav.classList.toggle('is-open');
    burger.setAttribute('aria-expanded', String(aberto));
    burger.setAttribute('aria-label', aberto ? 'Fechar menu' : 'Abrir menu');
  });

  nav.addEventListener('click', (e) => {
    if (e.target.closest('a')) fechar();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && nav.classList.contains('is-open')) {
      fechar();
      burger.focus();
    }
  });

  matchMedia('(min-width: 821px)').addEventListener('change', (ev) => {
    if (ev.matches) fechar();
  });
}

const hd = document.getElementById('hd');

if (hd) {

  const sentinela = document.createElement('div');
  sentinela.setAttribute('aria-hidden', 'true');
  sentinela.style.cssText = 'position:absolute;top:0;left:0;width:1px;height:1px;pointer-events:none';
  document.body.prepend(sentinela);

  new IntersectionObserver(
    ([entrada]) => hd.classList.toggle('is-stuck', !entrada.isIntersecting),
    { threshold: 0 },
  ).observe(sentinela);
}

const alvos = document.querySelectorAll('.reveal');

const parado = matchMedia('(prefers-reduced-motion: reduce)').matches;

if (parado || !('IntersectionObserver' in window)) {
  alvos.forEach((el) => el.classList.add('is-in'));
} else {
  const observador = new IntersectionObserver(
    (entradas) => {
      entradas.forEach((entrada) => {
        if (!entrada.isIntersecting) return;
        entrada.target.classList.add('is-in');
        observador.unobserve(entrada.target);
      });
    },
    { rootMargin: '0px 0px -12% 0px', threshold: 0.05 },
  );

  alvos.forEach((el, i) => {

    el.style.transitionDelay = `${Math.min(i % 6, 5) * 55}ms`;
    observador.observe(el);
  });
}

const titulo = document.querySelector('[data-escrita]');

if (titulo && !parado) {
  titulo.setAttribute('aria-label', titulo.textContent.replace(/\s+/g, ' ').trim());

  const letras = [];

  const dividir = (no) => {
    for (const filho of [...no.childNodes]) {
      if (filho.nodeType === Node.TEXT_NODE) {
        const pedaco = document.createDocumentFragment();
        for (const ch of filho.nodeValue) {
          const cel = document.createElement('span');
          cel.className = 'esc__c';
          cel.textContent = ch;
          letras.push(cel);
          pedaco.appendChild(cel);
        }
        filho.replaceWith(pedaco);
      } else if (filho.nodeType === Node.ELEMENT_NODE) {
        dividir(filho);
      }
    }
  };

  dividir(titulo);

  const cursor = document.createElement('span');
  cursor.className = 'esc__cursor';
  cursor.setAttribute('aria-hidden', 'true');

  titulo.prepend(cursor);
  titulo.classList.add('is-escrevendo');

  const escrever = () => {
    let i = 0;

    const passo = () => {
      const letra = letras[i];
      letra.classList.add('is-on');
      letra.after(cursor);
      i += 1;

      if (i < letras.length) {

        const espera = 32 + Math.random() * 42 + (letra.textContent === ' ' ? 66 : 0);
        setTimeout(passo, espera);
        return;
      }

      titulo.classList.remove('is-escrevendo');
      titulo.classList.add('is-escrito');
      setTimeout(() => cursor.classList.add('esc__cursor--fim'), 2400);
    };

    passo();
  };

  let iniciado = false;
  let reserva = 0;

  const comecar = () => {
    if (iniciado) return;
    iniciado = true;
    clearTimeout(reserva);
    setTimeout(escrever, 180);
  };

  titulo.addEventListener('transitionend', comecar, { once: true });
  reserva = setTimeout(comecar, 1500);
}

const abas = [...document.querySelectorAll('.demo__tab')];

if (abas.length) {
  const paineis = abas.map((b) => document.getElementById(b.getAttribute('aria-controls')));

  const trocar = (indice, mover = false) => {
    abas.forEach((aba, i) => {
      const ligada = i === indice;
      aba.setAttribute('aria-selected', String(ligada));
      aba.tabIndex = ligada ? 0 : -1;
      aba.classList.toggle('is-on', ligada);
      if (paineis[i]) paineis[i].hidden = !ligada;
    });
    if (mover) abas[indice].focus();
  };

  abas.forEach((aba, i) => {
    aba.addEventListener('click', () => trocar(i));
    aba.addEventListener('keydown', (e) => {
      const salto = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (salto) {
        e.preventDefault();
        trocar((i + salto + abas.length) % abas.length, true);
        return;
      }
      if (e.key === 'Home') { e.preventDefault(); trocar(0, true); }
      if (e.key === 'End') { e.preventDefault(); trocar(abas.length - 1, true); }
    });
  });
}

const roi = document.getElementById('roi-form');

if (roi) {
  const campo = document.getElementById('roi-ticket');
  const saida = document.getElementById('roi-out');

  const mensalidade = (codigo) => {
    const cartao = document.querySelector(`.plan[data-plano="${codigo}"]`);
    const centavos = Number(cartao?.dataset.centavos || 0);
    return centavos > 0 ? centavos / 100 : 0;
  };

  const inicial = mensalidade('inicial');
  const pro = mensalidade('pro');

  const moeda = new Intl.NumberFormat('pt-BR', {
    style: 'currency', currency: 'BRL', maximumFractionDigits: 2,
  });

  const lerValor = (texto) => {
    const limpo = String(texto).replace(/[^\d,.]/g, '').replace(/\./g, '').replace(',', '.');
    const n = Number.parseFloat(limpo);
    return Number.isFinite(n) && n > 0 ? n : 0;
  };

  const plural = (n, um, muitos) => (n === 1 ? um : muitos);

  const contar = () => {
    const ticket = lerValor(campo.value);
    saida.replaceChildren();

    if (!ticket || !inicial) {
      saida.textContent = 'Digite quanto vale uma venda sua para ver a conta.';
      return;
    }

    const mesesInicial = Math.floor(ticket / inicial);
    const mesesPro = pro ? Math.floor(ticket / pro) : 0;

    if (mesesInicial < 1) {
      saida.textContent = `Uma venda de ${moeda.format(ticket)} não chega a cobrir um mês do plano Iniciante `
        + `(${moeda.format(inicial)}). Com um ticket desse tamanho, o ganho do Vertex está no volume — `
        + 'vale mais olhar quantas dessas vendas passam pela sua operação por mês.';
      return;
    }

    const frase = document.createDocumentFragment();
    frase.append(`Uma única venda de ${moeda.format(ticket)} paga `);
    const forte = document.createElement('b');
    forte.textContent = `${mesesInicial} ${plural(mesesInicial, 'mês', 'meses')}`;
    frase.append(forte, ' do plano Iniciante');
    if (mesesPro >= 1) {
      frase.append(` — ou ${mesesPro} ${plural(mesesPro, 'mês', 'meses')} do Pro.`);
    } else {
      frase.append('.');
    }
    saida.append(frase);
  };

  campo.addEventListener('input', contar);
  roi.addEventListener('submit', (e) => e.preventDefault());
  contar();
}
