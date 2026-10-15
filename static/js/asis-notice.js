/*
 * AI の返答に、原文に無い助言・評価・飾りの言い回しがあったときの知らせ（ai_assist/asis.py の check の結果）。
 *
 *   AsIsNotice.show(お知らせの下に出す要素, 返答のJSON, 書き換えたテキスト欄)
 *   AsIsNotice.clear(お知らせの下に出す要素)
 *
 * data.added（見つけた言い回し）があれば黄色の帯で知らせ、「その文を消す」で data.cleaned に置きかえる（「戻す」で元に）。
 */
(function () {
  'use strict';
  if (window.AsIsNotice) return;
  function box(anchor) {
    var el = anchor.nextElementSibling;
    if (el && el.classList.contains('asis-notice')) return el;
    el = document.createElement('div');
    el.className = 'asis-notice alert alert-warning small py-1 px-2 mb-1';
    el.setAttribute('role', 'status');
    anchor.insertAdjacentElement('afterend', el);
    return el;
  }
  function setValue(area, value) {
    area.value = value;
    area.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function clear(anchor) {
    var el = anchor && anchor.nextElementSibling;
    if (el && el.classList.contains('asis-notice')) el.remove();
  }
  function show(anchor, data, area) {
    clear(anchor);
    if (!anchor || !data || !data.added || !data.added.length) return;
    var el = box(anchor), before = area.value;
    function render(done) {
      el.innerHTML = '';
      var msg = document.createElement('span');
      msg.textContent = done
        ? '原文に無い言い回しを含む文を消しました。'
        : '話した言葉・メモに無い言い回しがあります（AI が足した見解・助言かもしれません）：' +
          data.added.map(function (w) { return '「' + w + '」'; }).join('') + '。確かめてください。';
      el.appendChild(msg);
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-sm py-0 ms-2 ' + (done ? 'btn-outline-secondary' : 'btn-warning');
      b.textContent = done ? '戻す' : 'その文を消す';
      b.addEventListener('click', function () {
        if (done) { setValue(area, before); render(false); }
        else { before = area.value; setValue(area, data.cleaned || ''); render(true); }
      });
      el.appendChild(b);
    }
    render(false);
  }
  window.AsIsNotice = {show: show, clear: clear};
})();
