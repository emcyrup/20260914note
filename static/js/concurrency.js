/*
 * 複数の職員が同じ画面を同時に編集したときの競合対策（画面側）
 *  - form[data-concurrency="種類:ID"] を開いている間、サーバーに「編集中」を知らせ、
 *    同じものを開いている他の職員がいればフォームの先頭に表示する
 *  - 保存の直前にサーバーの版を確かめ、開いたあとに他の職員が保存していれば確認する
 */
(function () {
  var URL = document.body.getAttribute('data-editing-url');
  if (!URL) return;
  var TOUCH_INTERVAL = 45000;

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var i = document.querySelector('input[name=csrfmiddlewaretoken]');
    return i ? i.value : '';
  }
  function target(form) {
    var v = form.getAttribute('data-concurrency');
    if (!v) return null;
    var p = v.split(':');
    if (!p[1] || p[1] === '0' || p[1] === 'None') return null;
    return { kind: p[0], id: p[1] };
  }
  function post(data, beacon) {
    if (beacon && navigator.sendBeacon) {
      var fd = new FormData();
      Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
      fd.append('csrfmiddlewaretoken', csrf());
      navigator.sendBeacon(URL, fd);
      return Promise.resolve(null);
    }
    return fetch(URL, { method: 'POST', body: new URLSearchParams(data), credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/x-www-form-urlencoded' } })
      .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function banner(form, others) {
    var el = form.querySelector('.editing-banner');
    if (!others || !others.length) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement('div');
      el.className = 'editing-banner';
      var host = form.querySelector('.modal-body') || form;
      host.insertBefore(el, host.firstChild);
    }
    var names = others.map(function (o) { return o.name + '（' + o.since + ' から）'; }).join('、');
    el.innerHTML = '<i class="bi bi-people-fill me-1"></i>' + names + ' も同じ画面を編集中です。' +
      '同時に保存すると、あとから保存するときに確認が出ます。';
  }
  function watch(form) {
    var t = target(form);
    if (!t || form._editingTimer) return;
    var tick = function () {
      post({ action: 'touch', kind: t.kind, id: t.id }).then(function (d) { if (d) banner(form, d.others); });
    };
    tick();
    form._editingTimer = setInterval(tick, TOUCH_INTERVAL);
  }
  function unwatch(form, beacon) {
    var t = target(form);
    if (!t || !form._editingTimer) return;
    clearInterval(form._editingTimer);
    form._editingTimer = null;
    post({ action: 'release', kind: t.kind, id: t.id }, beacon);
  }
  function check(form) {
    var t = target(form);
    var mine = form.querySelector('input[name=version]');
    if (!t || !mine) return Promise.resolve(true);
    return fetch(URL + '?kind=' + encodeURIComponent(t.kind) + '&id=' + encodeURIComponent(t.id), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || d.version === mine.value) return true;
        var ok = window.confirm('この画面を開いたあとに、他の職員が ' + (d.updated_at || '') + ' に保存しています。\n' +
          'そのまま保存すると、その職員の内容を上書きします。\n\n' +
          'OK：上書きして保存する　／　キャンセル：保存をやめる（画面を開き直して最新の内容を確認してください）');
        if (!ok) return false;
        var f = form.querySelector('input[name=force_save]');
        if (!f) { f = document.createElement('input'); f.type = 'hidden'; f.name = 'force_save'; form.appendChild(f); }
        f.value = '1';
        return true;
      }).catch(function () { return true; });
  }
  function init() {
    document.querySelectorAll('form[data-concurrency]').forEach(function (form) {
      var modal = form.closest('.modal');
      if (modal) {
        modal.addEventListener('shown.bs.modal', function () { watch(form); });
        modal.addEventListener('hidden.bs.modal', function () { unwatch(form); });
      } else {
        watch(form);
      }
      form.addEventListener('submit', function (e) {
        if (form.dataset.concurrencyOk || form.hasAttribute('x-data')) return;   // Alpine のフォームは submitForm 側で確認する
        e.preventDefault();
        var submitter = e.submitter;
        check(form).then(function (ok) {
          if (!ok) return;
          form.dataset.concurrencyOk = '1';
          if (form.requestSubmit) form.requestSubmit(submitter || undefined); else form.submit();
        });
      });
    });
    window.addEventListener('pagehide', function () {
      document.querySelectorAll('form[data-concurrency]').forEach(function (f) { unwatch(f, true); });
    });
  }
  window.Concurrency = { check: check, watch: watch, unwatch: unwatch };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
