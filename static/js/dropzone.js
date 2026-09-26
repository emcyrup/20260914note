/*
 * ドラッグ＆ドロップでファイルを選べるようにする（アプリ共通）。
 * すべての <input type="file"> について、その入力欄を含む枠（label か .drop-target か form）にファイルを落とすと、
 * 「ファイルを選ぶ」で選んだのと同じ状態にして change を起こす（accept に合わないファイルは除く。1つだけの欄は先頭の1つ）。
 * 落とせる枠には、引きずっている間だけ点線の印を付ける。
 */
(function () {
  'use strict';
  if (!window.DataTransfer || !('files' in HTMLInputElement.prototype)) return;

  var style = document.createElement('style');
  style.textContent = '.dz-over{outline:3px dashed var(--color-primary,#c2703a)!important;outline-offset:3px;background:rgba(194,112,58,.06)!important;}' +
    '.dz-hint{font-size:.78rem;color:#8a8378;margin-top:2px;}';
  document.head.appendChild(style);

  function zoneOf(input) {
    return input.closest('.drop-target') || input.closest('label') || input.closest('.card-body') || input.closest('form') || input.parentElement;
  }

  function accepts(input, file) {
    var accept = (input.getAttribute('accept') || '').split(',').map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
    if (!accept.length) return true;
    var name = (file.name || '').toLowerCase(), type = (file.type || '').toLowerCase();
    return accept.some(function (a) {
      if (a.charAt(0) === '.') return name.slice(-a.length) === a;
      if (a.slice(-2) === '/*') return type.indexOf(a.slice(0, -1)) === 0;
      return type === a;
    });
  }

  function assign(input, files) {
    var dt = new DataTransfer(), ok = 0;
    for (var i = 0; i < files.length; i++) {
      if (!accepts(input, files[i])) continue;
      dt.items.add(files[i]); ok++;
      if (!input.multiple) break;
    }
    if (!ok) { alert('この欄に入れられる種類のファイルではありません。'); return; }
    input.files = dt.files;
    input.dispatchEvent(new Event('change', {bubbles: true}));
  }

  function wire(input) {
    if (input.dataset.dzWired) return;
    input.dataset.dzWired = '1';
    var zone = zoneOf(input);
    if (!zone) return;
    zone.addEventListener('dragenter', function (e) { if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.indexOf('Files') >= 0) { e.preventDefault(); zone.classList.add('dz-over'); } });
    zone.addEventListener('dragover', function (e) { if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.indexOf('Files') >= 0) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; zone.classList.add('dz-over'); } });
    zone.addEventListener('dragleave', function (e) { if (!zone.contains(e.relatedTarget)) zone.classList.remove('dz-over'); });
    zone.addEventListener('drop', function (e) {
      zone.classList.remove('dz-over');
      if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
      e.preventDefault();
      assign(input, e.dataTransfer.files);
    });
  }

  function wireAll(root) {
    (root || document).querySelectorAll('input[type=file]').forEach(wire);
  }
  // ページの外に落としてブラウザがファイルを開いてしまわないように
  document.addEventListener('dragover', function (e) { if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.indexOf('Files') >= 0) e.preventDefault(); });
  document.addEventListener('drop', function (e) { if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length && !e.defaultPrevented) e.preventDefault(); });
  document.addEventListener('DOMContentLoaded', function () { wireAll(); });
  document.addEventListener('shown.bs.modal', function (e) { wireAll(e.target); });
  document.addEventListener('shown.bs.collapse', function (e) { wireAll(e.target); });
  wireAll();
  window.DropZone = {wire: wireAll};
})();
