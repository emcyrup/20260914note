/*
 * 日誌の「確定の前のチェック」（records/quality.py と同じ規則）。
 *
 *   <form data-journal-check='{"min": 0, "rules": [{"key","label","kind","name"}]}'> … <div data-journal-check-box></div> … </form>
 *
 * フォームの値（FormData）を見て、足りない項目を ✗、入っている項目を ✓ で出す。
 * 「確定」を選んでいて足りないときは、保存すると下書きになることを赤で知らせる（保存は止めない。サーバーが下書きに戻す）。
 */
(function () {
  'use strict';
  if (window.JournalCheck) return;
  var DOMAINS = ['domain_health_life', 'domain_motor_sensory', 'domain_cognition_behavior', 'domain_language_comm', 'domain_social'];
  function len(s) { return (s || '').replace(/\s/g, '').length; }
  function evaluate(form, cfg) {
    var fd = new FormData(form), out = [];
    cfg.rules.forEach(function (r) {
      var ok = true, note = '';
      if (r.kind === 'viewpoints') {
        var vps = [];
        try { vps = JSON.parse(fd.get(r.name) || '[]') || []; } catch (e) { vps = []; }
        vps = vps.filter(function (v) { return v && (v.text || '').trim(); });
        var left = vps.filter(function (v) { return v.answer !== 'yes' && v.answer !== 'no'; }).length;
        if (!vps.length) { ok = false; note = '観点がありません'; }
        else if (left) { ok = false; note = left + ' つが未確認'; }
      } else if (r.kind === 'domains') {
        ok = DOMAINS.some(function (d) { return fd.has(d); });
      } else {
        var n = len(fd.get(r.name));
        if (!n) ok = false;
        else if (r.kind === 'long' && cfg.min && n < cfg.min) { ok = false; note = n + ' 字（' + cfg.min + ' 字以上）'; }
      }
      out.push({label: r.label, ok: ok, note: note});
    });
    return out;
  }
  function render(form, cfg, box) {
    var rows = evaluate(form, cfg);
    var lack = rows.filter(function (r) { return !r.ok; }).length;
    var status = form.querySelector('input[name=status]:checked');
    var confirming = status && status.value === 'confirmed';
    var key = JSON.stringify(rows) + confirming;
    if (box.dataset.last === key) return;
    box.dataset.last = key;
    box.className = 'journal-check small rounded p-2 mb-2 ' + (lack ? (confirming ? 'border border-danger' : 'border') : 'border border-success');
    box.innerHTML = '';
    var head = document.createElement('div');
    head.className = 'fw-semibold mb-1';
    head.innerHTML = '<i class="bi bi-list-check me-1"></i>確定の前のチェック';
    var cnt = document.createElement('span');
    cnt.className = 'ms-2 fw-normal ' + (lack ? 'text-danger' : 'text-success');
    cnt.textContent = lack ? 'あと ' + lack + ' 項目' : 'すべて入っています';
    head.appendChild(cnt);
    box.appendChild(head);
    var ul = document.createElement('ul');
    ul.className = 'list-unstyled mb-0 d-flex flex-wrap gap-3';
    rows.forEach(function (r) {
      var li = document.createElement('li');
      li.innerHTML = r.ok ? '<i class="bi bi-check-circle-fill text-success me-1"></i>' : '<i class="bi bi-x-circle text-danger me-1"></i>';
      li.appendChild(document.createTextNode(r.label + (r.note ? '：' + r.note : '')));
      ul.appendChild(li);
    });
    box.appendChild(ul);
    if (lack && confirming) {
      var warn = document.createElement('div');
      warn.className = 'text-danger mt-1';
      warn.textContent = 'このまま「確定」で保存すると、足りない項目があるので下書きで保存されます。';
      box.appendChild(warn);
    }
  }
  function setup(form) {
    var cfg;
    try { cfg = JSON.parse(form.getAttribute('data-journal-check') || '{}'); } catch (e) { return; }
    if (!cfg.rules || !cfg.rules.length) return;
    var box = form.querySelector('[data-journal-check-box]');
    if (!box) return;
    var tick = function () { render(form, cfg, box); };
    // Alpine が hidden の値を書き換えるので、入力のあとと、一定の間隔で見直す
    ['input', 'change', 'click'].forEach(function (ev) { form.addEventListener(ev, function () { setTimeout(tick, 50); }); });
    setInterval(function () { if (box.offsetParent !== null) tick(); }, 1000);
    tick();
  }
  function init() { document.querySelectorAll('form[data-journal-check]').forEach(setup); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  window.JournalCheck = {evaluate: evaluate};
})();
