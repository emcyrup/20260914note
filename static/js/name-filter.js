/*
 * 名前で探す：入れた文字（漢字・ひらがな・カタカナ。空白は無視）を名前とふりがなに含む行や箱だけを見せる・目立たせる。
 *
 *   <input type="search" data-name-filter="#list" data-nf-mode="hide" data-nf-count="#nf-count">
 *   <div id="list"> <div data-nf="青木 子 あおき こ">…</div> … </div>
 *
 * - data-nf-mode="hide"（既定）：合わない行を隠す
 * - data-nf-mode="mark"        ：合う箱に .nf-hit を付け、入れ物に .nf-on（ほかを薄くするのは画面の CSS で）
 * - data-nf-count              ：「n 件」を出す所（空のときは消す）
 * Esc で消す。window.NameFilter.norm(文字) は比べるための形（ほかの画面でも使う）。
 */
(function () {
  'use strict';
  if (window.NameFilter) return;
  function norm(s) {
    s = String(s || '').normalize ? String(s || '').normalize('NFKC') : String(s || '');
    return s.replace(/[\s　]+/g, '').replace(/[ァ-ヶ]/g, function (c) {
      return String.fromCharCode(c.charCodeAt(0) - 0x60);
    }).toLowerCase();
  }
  function apply(input) {
    var box = document.querySelector(input.getAttribute('data-name-filter'));
    if (!box) return;
    var mode = input.getAttribute('data-nf-mode') || 'hide';
    var q = norm(input.value), n = 0;
    Array.prototype.forEach.call(box.querySelectorAll('[data-nf]'), function (el) {
      var hit = !!q && norm(el.getAttribute('data-nf')).indexOf(q) >= 0;
      if (hit) n++;
      if (mode === 'mark') el.classList.toggle('nf-hit', hit);
      else el.style.display = (!q || hit) ? '' : 'none';
    });
    if (mode === 'mark') box.classList.toggle('nf-on', !!q);
    var c = input.getAttribute('data-nf-count') && document.querySelector(input.getAttribute('data-nf-count'));
    if (c) c.textContent = q ? (n ? n + ' 件' : '見つかりません') : '';
  }
  document.addEventListener('input', function (e) {
    if (e.target.matches && e.target.matches('[data-name-filter]')) apply(e.target);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && e.target.matches && e.target.matches('[data-name-filter]')) { e.target.value = ''; apply(e.target); }
  });
  window.NameFilter = {norm: norm, apply: apply};
})();
