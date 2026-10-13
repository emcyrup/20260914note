/*
 * 編集中の警告：<form data-unsaved-warn> の中身を変えたまま、保存せずに別の画面へ移ろうとしたら知らせる。
 *
 * - 画面の中のリンク（一覧へ・前後の利用者・メニューなど）を押したとき：確認の窓（「保存せずに移りますか？」）
 * - ブラウザの戻る・再読み込み・タブを閉じる・ほかのフォームの送信：ブラウザの確認の窓（文はブラウザが決める）
 * - そのフォームを「保存」したときは知らせない。中身を元に戻したときも知らせない
 * 開いたときの中身と比べる（ファイルは選んだ名前で比べる）。
 */
(function () {
  'use strict';
  if (window.UnsavedWarn) return;
  var MESSAGE = '編集中の内容があります（まだ保存していません）。\n保存せずにこの画面を離れますか？';
  var forms = Array.prototype.slice.call(document.querySelectorAll('form[data-unsaved-warn]'));
  if (!forms.length) return;
  var leaving = false;

  function state(form) {
    var parts = [];
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.name === 'csrfmiddlewaretoken' || el.name === 'version' || el.disabled) return;
      if (el.type === 'file') { parts.push(el.name + '=' + Array.prototype.map.call(el.files || [], function (f) { return f.name; }).join('|')); return; }
      if ((el.type === 'checkbox' || el.type === 'radio') && !el.checked) return;
      if (el.type === 'submit' || el.type === 'button') return;
      parts.push(el.name + '=' + el.value);
    });
    return parts.join('&');
  }
  forms.forEach(function (f) {
    f._uwInitial = state(f);
    f.addEventListener('submit', function () { f._uwSaving = true; });
  });
  function dirty() {
    return forms.some(function (f) { return !f._uwSaving && state(f) !== f._uwInitial; });
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented) return;
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || /^javascript:/i.test(href) || a.target === '_blank' || a.hasAttribute('download')) return;
    if (a.pathname === location.pathname && a.search === location.search && a.hash) return;   // 同じ画面の中の移動
    if (!dirty()) return;
    if (window.confirm(MESSAGE)) { leaving = true; return; }
    e.preventDefault();
    e.stopPropagation();
  }, true);

  window.addEventListener('beforeunload', function (e) {
    if (leaving || !dirty()) return;
    e.preventDefault();
    e.returnValue = '';
    return '';
  });

  window.UnsavedWarn = {dirty: dirty};
})();
