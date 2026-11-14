/*
 * 言葉づかいのチェック（保存の前に知らせる。止めはしない）。ai_assist/wording.py の check をサーバーにたずねる。
 *
 *   <form data-wording> … </form>         保存を押したとき、中の textarea（data-wording-skip 以外）の文を調べる
 *   WordingCheck.check(form) → Promise<bool>   Alpine などで自分で送るフォームから呼ぶ（true なら送ってよい）
 *
 * 気になる言葉があれば、フォームの上に一覧（言葉 → 言いかえ）を出し、「直す」（止める）か「このまま保存」を選ぶ。
 * 2 回目の保存（「このまま保存」）は調べない。
 */
(function () {
  'use strict';
  if (window.WordingCheck) return;
  var URL = window.WORDING_CHECK_URL || '';
  function texts(form) {
    return Array.prototype.filter.call(form.querySelectorAll('textarea'), function (t) {
      return !t.hasAttribute('data-wording-skip') && !t.disabled && !t.readOnly && t.value.trim();
    }).map(function (t) { return t.value; });
  }
  function banner(form) {
    var box = form.querySelector('.wording-banner');
    if (!box) {
      box = document.createElement('div');
      box.className = 'wording-banner alert alert-warning small py-2 px-2 mb-2';
      box.setAttribute('role', 'alert');
      var at = form.querySelector('[data-wording-banner]');
      if (at) at.appendChild(box); else form.insertBefore(box, form.firstChild);
    }
    return box;
  }
  function check(form) {
    if (!URL || form.dataset.wordingOk === '1') return Promise.resolve(true);
    var list = texts(form);
    if (!list.length) return Promise.resolve(true);
    var token = (form.querySelector('[name=csrfmiddlewaretoken]') || document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    return fetch(URL, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                       body: JSON.stringify({texts: list})})
      .then(function (r) { return r.ok ? r.json() : {findings: []}; })
      .then(function (d) {
        var found = (d && d.findings) || [];
        if (!found.length) { var old = form.querySelector('.wording-banner'); if (old) old.remove(); return true; }
        return new Promise(function (resolve) {
          var box = banner(form);
          box.innerHTML = '';
          var head = document.createElement('div');
          head.innerHTML = '<i class="bi bi-chat-square-text me-1"></i><b>気になる言葉があります。</b>第三者が読んでも誤解しないよう、見直してから保存できます（そのまま保存もできます）。';
          box.appendChild(head);
          var ul = document.createElement('ul'); ul.className = 'mb-2 mt-1';
          found.forEach(function (f) {
            var li = document.createElement('li');
            li.innerHTML = '<span class="badge ' + (f.kind === 'ng' ? 'text-bg-danger' : f.kind === 'hard' ? 'text-bg-secondary' : 'text-bg-warning text-dark') + ' me-1">' + f.label + '</span>「<b></b>」 → ';
            li.querySelector('b').textContent = f.word;
            li.appendChild(document.createTextNode(f.hint));
            ul.appendChild(li);
          });
          box.appendChild(ul);
          var fix = document.createElement('button'); fix.type = 'button'; fix.className = 'btn btn-sm btn-primary me-2'; fix.textContent = '直す';
          var go = document.createElement('button'); go.type = 'button'; go.className = 'btn btn-sm btn-outline-secondary'; go.textContent = 'このまま保存';
          fix.addEventListener('click', function () { box.remove(); var t = form.querySelector('textarea'); if (t) t.focus(); resolve(false); });
          go.addEventListener('click', function () { form.dataset.wordingOk = '1'; box.remove(); resolve(true); });
          box.appendChild(fix); box.appendChild(go);
          box.scrollIntoView({block: 'center', behavior: 'smooth'});
        });
      })
      .catch(function () { return true; });     // 調べられないときは止めない
  }
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.hasAttribute || !form.hasAttribute('data-wording') || form.dataset.wordingOk === '1') return;
    if (!texts(form).length) return;
    e.preventDefault();
    var submitter = e.submitter;
    check(form).then(function (ok) {
      if (!ok) return;
      form.dataset.wordingOk = '1';
      if (submitter && submitter.name && submitter.value !== undefined) {
        // 押したボタンの name=value も送る（保存してステップへ など）
        var h = document.createElement('input'); h.type = 'hidden'; h.name = submitter.name; h.value = submitter.value; form.appendChild(h);
      }
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    });
  }, true);
  window.WordingCheck = {check: check};
})();
