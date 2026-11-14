/*
 * 印をまとめて付ける（共演NG など）。<div data-check-group> の中の
 *   .cg-filter（名前・かなで絞る）・.cg-all（見えている人に印）・.cg-none（見えている人の印を外す）・.cg-count（印の数）
 *   .cg-list の中の <label data-kana> <input type=checkbox>
 * 印を付けただけでは保存しない（フォームの「保存」で保存）。
 */
(function () {
  'use strict';
  function setup(g) {
    var list = g.querySelector('.cg-list');
    if (!list) return;
    var labels = Array.prototype.slice.call(list.querySelectorAll('label'));
    var filter = g.querySelector('.cg-filter'), count = g.querySelector('.cg-count');
    function visible() { return labels.filter(function (l) { return l.style.display !== 'none'; }); }
    function paint() {
      if (!count) return;
      var n = labels.filter(function (l) { var c = l.querySelector('input'); return c && c.checked; }).length;
      count.textContent = n + ' 人に印（' + labels.length + ' 人のうち）';
    }
    function setAll(on) {
      visible().forEach(function (l) {
        var c = l.querySelector('input');
        if (c && !c.disabled && c.checked !== on) { c.checked = on; c.dispatchEvent(new Event('change', {bubbles: true})); }
      });
      paint();
    }
    if (filter) filter.addEventListener('input', function () {
      var q = filter.value.replace(/\s/g, '');
      labels.forEach(function (l) {
        var text = (l.textContent + (l.dataset.kana || '')).replace(/\s/g, '');
        l.style.display = !q || text.indexOf(q) >= 0 ? '' : 'none';
      });
    });
    var all = g.querySelector('.cg-all'), none = g.querySelector('.cg-none');
    if (all) all.addEventListener('click', function () { setAll(true); });
    if (none) none.addEventListener('click', function () { setAll(false); });
    list.addEventListener('change', paint);
    paint();
  }
  function init() { document.querySelectorAll('[data-check-group]').forEach(setup); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
