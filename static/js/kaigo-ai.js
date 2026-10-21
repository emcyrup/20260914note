/*
 * 居宅介護支援の記録の道具（kaigo/_ai_tools.html）
 * - 写真から文字に：写真・PDF を送って、書いてある文字を欄の最後に足す
 * - AIでまとめる：欄の文を、指定の文字数くらいの自然な文にまとめて置きかえる（「元に戻す」で戻せる）
 * - 会議の下書き（[data-meeting-draft]）：メモから第4表の4つの欄を作る
 */
(function () {
  'use strict';
  function csrf() { var el = document.querySelector('[name=csrfmiddlewaretoken]'); return el ? el.value : ''; }
  function post(url, body, isForm) {
    var opt = { method: 'POST', headers: { 'X-CSRFToken': csrf() }, body: body, credentials: 'same-origin' };
    if (!isForm) { opt.headers['Content-Type'] = 'application/json'; }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return { error: 'サーバーの返事を読めませんでした（' + r.status + '）' }; })
        .then(function (d) { if (!r.ok && !d.error) { d.error = 'エラー（' + r.status + '）'; } return d; });
    });
  }
  var URLS = window.KAIGO_AI_URLS || {};

  document.querySelectorAll('.kg-ai').forEach(function (bar) {
    var area = document.getElementById(bar.dataset.target);
    var status = bar.parentNode.querySelector('.kg-ai-status');
    var undo = bar.querySelector('.kg-ai-undo');
    var before = null;
    function say(msg, bad) { if (status) { status.textContent = msg || ''; status.classList.toggle('text-danger', !!bad); } }
    function busy(on) { bar.querySelectorAll('button, input, select').forEach(function (el) { el.disabled = on; }); }
    var photo = bar.querySelector('.kg-ai-photo');
    if (photo) photo.addEventListener('change', function () {
      var f = photo.files[0]; if (!f) return;
      var fd = new FormData(); fd.append('file', f);
      busy(true); say('写真の文字を読んでいます…（数十秒かかることがあります）');
      post(URLS.read, fd, true).then(function (d) {
        busy(false); photo.value = '';
        if (d.error) { say(d.error, true); return; }
        before = area.value; undo.classList.remove('d-none');
        area.value = (area.value.trim() ? area.value.replace(/\s+$/, '') + '\n' : '') + (d.text || '');
        area.dispatchEvent(new Event('input', { bubbles: true }));
        say('写真の文字を欄の最後に足しました。読み違いがないか確かめてください。');
      }).catch(function () { busy(false); say('送れませんでした。通信を確かめてください。', true); });
    });
    var sum = bar.querySelector('.kg-ai-sum');
    if (sum) sum.addEventListener('click', function () {
      var text = area.value.trim();
      if (!text) { say('まとめる文がありません。話すか書いてから押してください。', true); return; }
      var cat = bar.dataset.cat ? document.getElementById(bar.dataset.cat) : null;
      var chars = bar.querySelector('.kg-ai-chars').value;
      busy(true); say('まとめています…');
      post(URLS.summarize, JSON.stringify({ text: text, chars: chars, category: cat ? cat.value : '' })).then(function (d) {
        busy(false);
        if (d.error) { say(d.error, true); return; }
        before = area.value; undo.classList.remove('d-none');
        area.value = d.text || '';
        area.dispatchEvent(new Event('input', { bubbles: true }));
        var msg = 'まとめました（' + (d.length || area.value.length) + '字）。直すところを直して保存してください。';
        if (d.added && d.added.length) { msg += '　※ 話していない言い回しが入っています：' + d.added.join('、'); }
        say(msg, d.added && d.added.length);
      }).catch(function () { busy(false); say('送れませんでした。通信を確かめてください。', true); });
    });
    if (undo) undo.addEventListener('click', function () {
      if (before === null) return;
      area.value = before; before = null; undo.classList.add('d-none'); say('元に戻しました。');
      area.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });

  var draft = document.querySelector('[data-meeting-draft]');
  if (draft) draft.addEventListener('click', function () {
    var memo = document.getElementById(draft.dataset.memo), status = document.getElementById(draft.dataset.status);
    var text = memo.value.trim();
    if (!text) { status.textContent = '会議のメモがありません。話すか書いてから押してください。'; return; }
    var filled = ['topics', 'discussion', 'conclusion', 'remaining'].some(function (k) { var el = document.querySelector('[name=' + k + ']'); return el && el.value.trim(); });
    if (filled && !confirm('検討した項目・検討内容・結論・残された課題を、メモから作った下書きで書きかえます。よろしいですか？')) return;
    draft.disabled = true; status.textContent = '下書きを作っています…（数十秒かかることがあります）';
    post(URLS.meeting, JSON.stringify({ text: text })).then(function (d) {
      draft.disabled = false;
      if (d.error) { status.textContent = d.error; return; }
      ['topics', 'discussion', 'conclusion', 'remaining'].forEach(function (k) {
        var el = document.querySelector('[name=' + k + ']'); if (el && d[k] !== undefined) { el.value = d[k]; el.dispatchEvent(new Event('input', { bubbles: true })); }
      });
      status.textContent = '下書きを入れました。直すところを直して保存してください。';
    }).catch(function () { draft.disabled = false; status.textContent = '送れませんでした。通信を確かめてください。'; });
  });
})();
