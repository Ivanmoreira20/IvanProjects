(function () {
  try {
    var busca = window.location.search;
    if (/[?&](ficar|saiu)\b/.test(busca) || window.location.hash === '#ficar') return;
    if (!/(?:^|;\s*)vertex_csrf=/.test(document.cookie)) return;

    window.location.replace('/app');
  } catch (e) {

  }
})();
