/*
 * 写真は送る前にブラウザで縮める（アプリ共通）。
 * スマートフォンの写真は 1 枚 3〜10MB あり、サーバーの前にある nginx の上限（既定 1MB）に当たって
 * 「413 Request Entity Too Large」になることがある。そこで、すべての <input type="file"> について、
 * 選んだ画像が MIN_BYTES より大きければ、長辺 MAX_SIDE px・JPEG（品質 QUALITY）に縮めて置き換える。
 * PDF・Excel などの画像でないファイルと、ブラウザで開けない画像（HEIC など）はそのまま送る。
 * data-no-shrink を付けた欄は縮めない。
 * 縮めている最中に送信されたときは、終わるまで待ってから送る。
 */
(function () {
  'use strict';
  if (!window.DataTransfer || !window.createImageBitmap || !document.createElement('canvas').toBlob) return;

  var MIN_BYTES = 800 * 1024, MAX_SIDE = 1800, QUALITY = 0.82;
  var pending = new WeakMap();          // form → 進行中の縮小の数

  function shrinkable(file) {
    return file && file.size > MIN_BYTES && /^image\/(jpeg|png|webp|gif|bmp)$/i.test(file.type || '');
  }

  function shrink(file) {
    return createImageBitmap(file).then(function (bmp) {
      var w = bmp.width, h = bmp.height, k = Math.min(1, MAX_SIDE / Math.max(w, h));
      var canvas = document.createElement('canvas');
      canvas.width = Math.round(w * k); canvas.height = Math.round(h * k);
      var ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);     // 透明は白にする
      ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
      if (bmp.close) bmp.close();
      return new Promise(function (resolve) { canvas.toBlob(resolve, 'image/jpeg', QUALITY); });
    }).then(function (blob) {
      if (!blob || blob.size >= file.size) return file;             // 小さくならないなら元のまま
      var name = (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
      return new File([blob], name, {type: 'image/jpeg', lastModified: file.lastModified || Date.now()});
    }).catch(function () { return file; });                        // 開けない形式（HEIC など）はそのまま
  }

  function onChange(e) {
    var input = e.target;
    if (input.type !== 'file' || input.dataset.noShrink !== undefined || input.dataset.shrunk === '1') return;
    var files = Array.prototype.slice.call(input.files || []);
    if (!files.some(shrinkable)) return;
    var form = input.form;
    if (form) pending.set(form, (pending.get(form) || 0) + 1);
    input.dataset.shrinking = '1';
    Promise.all(files.map(function (f) { return shrinkable(f) ? shrink(f) : Promise.resolve(f); })).then(function (out) {
      var dt = new DataTransfer();
      out.forEach(function (f) { dt.items.add(f); });
      input.dataset.shrunk = '1';                                  // 置き換えの change で二重に縮めない
      input.files = dt.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      delete input.dataset.shrunk;
    }).finally(function () {
      delete input.dataset.shrinking;
      if (form) {
        pending.set(form, Math.max(0, (pending.get(form) || 1) - 1));
        if (!pending.get(form) && form.dataset.shrinkSubmit === '1') { delete form.dataset.shrinkSubmit; form.requestSubmit ? form.requestSubmit() : form.submit(); }
      }
    });
  }

  // 縮めている最中の送信は、終わってから送る
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form && pending.get(form)) { e.preventDefault(); form.dataset.shrinkSubmit = '1'; }
  }, true);

  document.addEventListener('change', onChange, true);
})();
