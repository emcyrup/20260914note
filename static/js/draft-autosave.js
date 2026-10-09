/*
 * 書きかけの自動保存（この端末に下書きを残す）。音声入力やAIで入れた文が、保存を押す前に消えないようにする。
 *
 *   <form data-draft="療育-留意点:12"> … </form>
 *     data-draft-fields="textarea, [name=title]"  … 残す欄（省略すると文字・日付・選ぶ欄の全部。hidden は残さない）
 *     data-draft-status="要素のid"                … 「保存済み」「まだ保存していません」を出す所（省略すると保存ボタンの前に作る）
 *   <div data-draft-banner></div>                  … 下書きのお知らせを出す所（省略するとフォームの先頭）
 *   <div class="d-none" data-draft-reveal>        … 下書きを戻したら開く（閉じている「直す」欄など）
 *   <form data-draft data-draft-new>               … 新しく書く欄（中身が入っていても「保存済み」と出さない）
 *   <form data-draft-clear>                        … 送るときに下書きをすべて消す（ログアウト）
 *
 * 動き
 * - 欄を変えると少し待ってから localStorage に残す（開いたときの内容と同じに戻したら消す）。7日たったら消す
 * - 開き直したとき、開いたときの内容が前と同じなら下書きを欄に戻して知らせる。違う（ほかの人が先に保存した・日が変わった）
 *   ときは欄はそのままにして「下書きを戻す」を出す
 * - 保存を押したら「送った」印を付ける。次の画面で「保存しました」が出ていれば消し、出ていなければ（通信が切れた・
 *   ログインし直した・入れ忘れがあった）下書きを戻して「もう一度保存」を出す
 * - 送るときに通信が切れていたら止めて知らせる
 * 下書きはログインした人ごとに分ける（window.DRAFT_SCOPE）。保存したかどうかは window.DRAFT_SAVED（成功のお知らせが出た）で見る。
 */
(function () {
  'use strict';
  if (window.DraftAutosave) return;
  var PREFIX = 'michinote-draft:', TTL = 7 * 24 * 3600 * 1000, SENT_TTL = 30 * 60 * 1000, WAIT = 600;
  var scope = String(window.DRAFT_SCOPE || '0');
  var ok = true;
  try { localStorage.setItem(PREFIX + 'test', '1'); localStorage.removeItem(PREFIX + 'test'); } catch (e) { ok = false; }

  function keyOf(form) { return PREFIX + scope + ':' + form.getAttribute('data-draft'); }
  function load(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; } }
  function store(key, d) { try { localStorage.setItem(key, JSON.stringify(d)); return true; } catch (e) { return false; } }
  function drop(key) { try { localStorage.removeItem(key); } catch (e) { /* 使えない */ } }
  function allKeys(mine) {
    var out = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(PREFIX) === 0 && (!mine || k.indexOf(PREFIX + scope + ':') === 0)) out.push(k);
      }
    } catch (e) { /* 使えない */ }
    return out;
  }
  // 古い下書きを消す
  allKeys(false).forEach(function (k) { var d = load(k); if (!d || !d.t || Date.now() - d.t > TTL) drop(k); });

  function fields(form) {
    var sel = form.getAttribute('data-draft-fields') ||
      'textarea, select, input:not([type]), input[type=text], input[type=date], input[type=time], input[type=number], input[type=search], input[type=tel], input[type=email], input[type=url]';
    return Array.prototype.filter.call(form.querySelectorAll(sel), function (el) {
      return (el.name || el.id) && !el.readOnly && !el.disabled && !el.hasAttribute('data-draft-skip') && !el.multiple;
    });
  }
  function fid(el, i) { return (el.name || '#' + el.id) + '@' + i; }
  function values(form) {
    var v = {};
    fields(form).forEach(function (el, i) { v[fid(el, i)] = el.value; });
    return v;
  }
  function same(a, b) {
    var ka = Object.keys(a || {}), kb = Object.keys(b || {});
    if (ka.length !== kb.length) return false;
    return ka.every(function (k) { return a[k] === b[k]; });
  }
  function hasText(v) { return Object.keys(v).some(function (k) { return (v[k] || '').trim(); }); }
  function put(form, v) {
    fields(form).forEach(function (el, i) {
      var k = fid(el, i);
      if (!(k in v) || el.value === v[k]) return;
      el.value = v[k];
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    });
  }
  function when(t) {
    var d = new Date(t), now = new Date();
    var hm = d.getHours() + ':' + ('0' + d.getMinutes()).slice(-2);
    return d.toDateString() === now.toDateString() ? 'きょう ' + hm : (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hm;
  }

  function statusBox(form) {
    var id = form.getAttribute('data-draft-status'), el = id && document.getElementById(id);
    if (el) return el;
    el = document.createElement('span');
    el.className = 'draft-status small';
    el.style.whiteSpace = 'nowrap';
    var btn = form.querySelector('button:not([type=button]), [type=submit]');
    var host = btn && btn.parentNode;
    if (host) host.insertBefore(el, btn); else form.appendChild(el);
    return el;
  }
  function banner(form, cls, html, buttons) {
    var box = form.querySelector('.draft-banner');
    if (!box) {
      box = document.createElement('div');
      box.className = 'draft-banner alert small py-1 px-2 mb-2';
      box.setAttribute('role', 'status');
      var at = form.querySelector('[data-draft-banner]');
      if (at) at.appendChild(box); else form.insertBefore(box, form.firstChild);
    }
    box.className = 'draft-banner alert small py-1 px-2 mb-2 alert-' + cls;
    box.innerHTML = '<span class="msg"></span>';
    box.querySelector('.msg').textContent = html;
    (buttons || []).forEach(function (b) {
      var el = document.createElement('button');
      el.type = 'button';
      el.className = 'btn btn-sm ms-2 py-0 ' + (b.primary ? 'btn-primary' : 'btn-outline-secondary');
      el.textContent = b.label;
      el.addEventListener('click', function () { b.run(); });
      box.appendChild(el);
    });
    return box;
  }
  function hideBanner(form) { var b = form.querySelector('.draft-banner'); if (b) b.remove(); }
  function reveal(form) {
    var el = form.closest('[data-draft-reveal]');
    if (el) el.classList.remove('d-none');
    form.dispatchEvent(new CustomEvent('draft:restored', {bubbles: true}));
  }

  function setup(form) {
    var key = keyOf(form), base = values(form), timer = null, last = JSON.stringify(base);
    var status = statusBox(form), savedNow = false;
    function show() {
      var v = values(form), dirty = !same(v, base);
      status.classList.remove('text-success', 'text-danger', 'text-muted');
      status.style.color = '';
      status.title = '';
      if (dirty) {
        status.textContent = ok ? 'まだ保存していません' : 'まだ保存していません（下書きを残せません）';
        status.title = ok ? 'この端末に下書きを残しています。画面を閉じても、開き直すと戻ります' : 'ブラウザの設定で下書きを残せません。早めに保存してください';
        if (ok) status.style.color = '#a15c00'; else status.classList.add('text-danger');
      } else if (savedNow) {
        status.textContent = '保存しました'; status.classList.add('text-success');
      } else if (hasText(v) && !form.hasAttribute('data-draft-new')) {
        status.textContent = '保存済み'; status.classList.add('text-muted');
      } else {
        status.textContent = '';
      }
    }
    function keep() {
      var v = values(form), s = JSON.stringify(v);
      if (s === last) return;
      last = s;
      if (same(v, base)) drop(key);
      else if (ok) ok = store(key, {v: v, base: base, t: Date.now(), sent: 0, path: location.pathname});
      show();
    }
    function later() { clearTimeout(timer); timer = setTimeout(keep, WAIT); show(); }
    form.addEventListener('input', later);
    form.addEventListener('change', later);
    setInterval(keep, 1500);              // 音声入力・AI の書き換えは input が出ないことがある
    form._draft = {key: key, keep: keep, show: show, base: function () { return base; }};

    var d = load(key);
    if (key === justSaved) savedNow = true;
    if (d && d.v) {
      var v = values(form);
      var sent = d.sent && Date.now() - d.sent < SENT_TTL;
      if (same(d.v, v)) {
        drop(key);                        // 保存できた（欄の内容が下書きと同じ）
        savedNow = !!sent;
      } else if (same(d.base, base)) {
        put(form, d.v); reveal(form);
        last = JSON.stringify(values(form));
        if (sent) {
          banner(form, 'warning', '前に「保存」を押した内容（' + when(d.sent) + '）が保存できていませんでした（通信が切れた・ログインし直した・入れ忘れがあったなど）。下書きを戻しました。確かめて保存し直してください。', [
            {label: 'もう一度保存', primary: true, run: function () { if (form.requestSubmit) form.requestSubmit(); else form.submit(); }},
            {label: '下書きを消す', run: function () { put(form, base); drop(key); last = JSON.stringify(base); hideBanner(form); show(); }}
          ]);
        } else {
          banner(form, 'info', '保存していない下書き（' + when(d.t) + '）を戻しました。', [
            {label: '下書きを消す', run: function () { put(form, base); drop(key); last = JSON.stringify(base); hideBanner(form); show(); }}
          ]);
        }
        d.sent = 0; d.t = Date.now(); store(key, d);
      } else {
        banner(form, 'warning', 'この端末に保存していない下書き（' + when(d.t) + '）があります。開いたあとの内容が前と違う（ほかの人が先に保存した・日が変わったなど）ので、欄は今の内容のままにしています。', [
          {label: '下書きを戻す', primary: true, run: function () { put(form, d.v); reveal(form); keep(); hideBanner(form); }},
          {label: '下書きを消す', run: function () { drop(key); hideBanner(form); }}
        ]);
      }
    }
    show();
  }

  // 保存を押した：送った印を付ける（ほかの処理で止められたときは付けない）。通信が切れていたら止める
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form.hasAttribute && form.hasAttribute('data-draft-clear')) {
      var left = allKeys(true).filter(function (k) { var d = load(k); return d && !d.sent; });
      if (left.length && !confirm('保存していない下書きが ' + left.length + ' 件あります。ログアウトすると消えます。よろしいですか？')) { e.preventDefault(); return; }
      allKeys(true).forEach(drop);
      return;
    }
    if (!form._draft || e.defaultPrevented) return;
    if (navigator.onLine === false) {
      e.preventDefault();
      form._draft.keep();
      banner(form, 'danger', '通信が切れているため保存できません。下書きはこの端末に残しています。つながってから保存してください。', [
        {label: 'もう一度保存', primary: true, run: function () { if (form.requestSubmit) form.requestSubmit(); else form.submit(); }}
      ]);
      return;
    }
    var v = values(form);
    if (!same(v, form._draft.base())) store(form._draft.key, {v: v, base: form._draft.base(), t: Date.now(), sent: Date.now(), path: location.pathname});
  });
  // 戻るで前の画面に戻ったときは、送った印を外す（送れていない）
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    document.querySelectorAll('form[data-draft]').forEach(function (form) {
      if (!form._draft) return;
      var d = load(form._draft.key);
      if (d && d.sent) { d.sent = 0; store(form._draft.key, d); }
    });
  });

  // 「保存しました」が出た画面：いちばん最後に送った下書きは保存できたので消す（新しく書く欄は空に戻り、
  // 議事録などは別の画面に移ることもあるので、欄と見比べずに消す）
  var justSaved = null;
  if (window.DRAFT_SAVED) {
    var latest = 0;
    allKeys(true).forEach(function (k) {
      var d = load(k);
      if (d && d.sent && Date.now() - d.sent < SENT_TTL && d.sent > latest) { latest = d.sent; justSaved = k; }
    });
    if (justSaved) drop(justSaved);
  }

  function init() { document.querySelectorAll('form[data-draft]').forEach(function (f) { if (!f._draft) setup(f); }); }
  // Alpine の x-model が欄を入れ終わってから見る
  if (document.readyState === 'complete') setTimeout(init, 0);
  else window.addEventListener('load', function () { setTimeout(init, 0); });
  window.DraftAutosave = {init: init, available: function () { return ok; }};
})();
