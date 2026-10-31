/*
 * 介護施設の記録の AI の道具（templates/nursing/record_form.html・incident_form.html）
 * - AI で下書き：はい／いいえ・キーワード・メモ・言語を送って、介護記録と家族向けの文を欄に入れる（元の言語への訳があれば下に出す）
 * - 日本語に整える（.nr-rewrite）：欄の文を記録に使える日本語に置きかえる（「元に戻す」で戻せる）
 * - 事故報告の下書き（#inc-draft）：メモから状況・原因・対応・再発防止の 4 つの欄を作る
 */
(function () {
  'use strict';
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return { error: 'サーバーの返事を読めませんでした（' + r.status + '）' }; }); });
  }
  function setVal(el, v) { if (!el) return; el.value = v || ''; el.dispatchEvent(new Event('input', { bubbles: true })); }
  var URLS = window.NURSING_AI_URLS || {};
  var backBox = document.getElementById('ai-back');
  function showBack(text) {
    if (!backBox) return;
    if (text) { backBox.textContent = '（確認用の訳）\n' + text; backBox.classList.remove('d-none'); } else { backBox.classList.add('d-none'); }
  }
  var lang = document.getElementById('lang');
  function langVal() { return lang ? lang.value : 'ja'; }

  // 介護記録の下書き
  var form = document.getElementById('rec-form'), draft = document.getElementById('ai-draft'), status = document.getElementById('ai-status');
  if (form && draft) draft.addEventListener('click', function () {
    var checks = {};
    form.querySelectorAll('input[data-check]:checked').forEach(function (i) { checks[i.dataset.check] = i.value; });
    var body = document.getElementById('body'), fam = document.getElementById('family_message');
    if ((body.value.trim() || fam.value.trim()) && !confirm('介護記録と家族向けの文を、AI の下書きで書きかえます。よろしいですか？')) return;
    draft.disabled = true; status.textContent = '下書きを作っています…（20〜40 秒かかることがあります）';
    post(form.dataset.aiUrl, { date: form.querySelector('[name=d]').value, checks: checks, keywords: document.getElementById('keywords').value, memo: document.getElementById('memo').value, lang: langVal() })
      .then(function (d) {
        draft.disabled = false;
        if (d.error) { status.textContent = d.error; status.classList.add('text-danger'); return; }
        status.classList.remove('text-danger');
        setVal(body, d.body); setVal(fam, d.family_message); showBack(d.back);
        var ad = document.getElementById('ai-drafted'); if (ad) ad.value = '1';
        status.textContent = '下書きを入れました（' + (d.body || '').length + ' 字）。事実と違うところを直して保存してください。';
      }).catch(function () { draft.disabled = false; status.textContent = '送れませんでした。通信を確かめてください。'; });
  });

  // 日本語に整える
  document.querySelectorAll('.nr-rewrite').forEach(function (btn) {
    var area = document.getElementById(btn.dataset.target);
    var undo = document.querySelector('.nr-undo[data-target="' + btn.dataset.target + '"]');
    var before = null;
    btn.addEventListener('click', function () {
      var text = area.value.trim();
      if (!text) { alert('整える文がありません。話すか書いてから押してください。'); return; }
      btn.disabled = true;
      post(URLS.rewrite, { text: text, lang: langVal(), kind: btn.dataset.kind || 'record' }).then(function (d) {
        btn.disabled = false;
        if (d.error) { alert(d.error); return; }
        before = area.value; if (undo) undo.classList.remove('d-none');
        setVal(area, d.text); showBack(d.back);
      }).catch(function () { btn.disabled = false; alert('送れませんでした。通信を確かめてください。'); });
    });
    if (undo) undo.addEventListener('click', function () { if (before === null) return; setVal(area, before); before = null; undo.classList.add('d-none'); showBack(''); });
  });

  // 事故報告の下書き
  var inc = document.getElementById('inc-draft');
  if (inc) inc.addEventListener('click', function () {
    var memo = document.getElementById('memo'), st = document.getElementById('inc-status');
    if (!memo.value.trim()) { st.textContent = 'メモがありません。話すか書いてから押してください。'; return; }
    var keys = ['situation', 'cause', 'response', 'prevention'];
    if (keys.some(function (k) { var el = document.getElementById(k); return el && el.value.trim(); }) && !confirm('状況・原因・対応・再発防止の欄を、メモから作った下書きで書きかえます。よろしいですか？')) return;
    inc.disabled = true; st.textContent = '下書きを作っています…';
    post(URLS.incident, { text: memo.value, lang: langVal() }).then(function (d) {
      inc.disabled = false;
      if (d.error) { st.textContent = d.error; return; }
      keys.forEach(function (k) { setVal(document.getElementById(k), d[k]); });
      showBack(d.back);
      st.textContent = '下書きを入れました。事実と違うところを直して保存してください。';
    }).catch(function () { inc.disabled = false; st.textContent = '送れませんでした。通信を確かめてください。'; });
  });
})();
