/*
 * 音声入力（Web Speech API）。アプリの中のマイクのボタンはすべてこれを使う。
 *
 *   <button type="button" data-voice-target="テキストエリアのid" data-voice-note="お知らせを出す要素のid">
 *   または onclick="VoiceInput.toggle(this, テキストエリア)"
 *
 * 押している間、話した言葉をテキストエリアの末尾に足していく。もう一度押すと止まる。
 *
 * ブラウザごとのくせへの対策
 * - Chrome（パソコン）：黙った間や約1分ごとに聞き取りが切れる → 止めるボタンを押すまで自動でつなぎ直す
 * - Android の Chrome：続けて聞く（continuous）と同じ言葉が重なって返る → 1文ずつ聞いてつなぎ直す。
 *   同じ言葉が続けて2回確定したときは1回にする
 * - iPhone・iPad（Safari）：resultIndex が当てにならない・確定しないまま終わることがある
 *   → 何番目まで欄に入れたかを自分で数える。確定しないまま終わった言葉も欄に入れる
 * - 通信が一瞬切れた（network）：何回かはつなぎ直す
 * - 聞き取りを始め直せない（InvalidStateError）：少し待ってやり直す
 * マイクは https のページ（または localhost）でしか使えないので、http のときは理由を出す。
 */
(function () {
  'use strict';
  if (window.VoiceInput) return;          // 2回読み込まれても1回だけ動かす

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var UA = navigator.userAgent;
  var IOS = /iPhone|iPad|iPod/i.test(UA) || (/Macintosh/.test(UA) && navigator.maxTouchPoints > 1);
  var ONE_SHOT = /Android/i.test(UA) || IOS;
  var cfg = window.VOICE_INPUT_CONFIG || {};
  var SILENCE_MS = cfg.silenceMs || 3 * 60 * 1000;   // これだけ何も聞き取れなければ止める
  var MAX_NET_RETRY = 3;                             // 通信エラーでつなぎ直す回数（聞き取れたら数え直す）
  var MAX_QUICK_END = 8;                             // すぐ終わってしまうのが続いたら止める（マイクが使えない状態）
  var active = null;

  function note(btn, text) {
    var id = btn.getAttribute('data-voice-note');
    var el = id && document.getElementById(id);
    if (el) { el.textContent = text; el.classList.toggle('d-none', !text); }
    else if (text) { alert(text); }
  }

  function liveBox(target) {
    var box = target.parentNode.querySelector('.voice-live[data-for="' + (target.id || '') + '"]');
    if (!box) {
      box = document.createElement('div');
      box.className = 'voice-live';
      box.setAttribute('data-for', target.id || '');
      box.setAttribute('aria-live', 'polite');
      box.style.cssText = 'font-size:0.85rem;color:#8a8378;min-height:1.2em;margin-top:4px;';
      target.insertAdjacentElement('afterend', box);
    }
    return box;
  }

  function clock(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function norm(t) { return (t || '').replace(/[\s。、．，.,！!？?]/g, ''); }

  function append(a, text) {
    text = (text || '').trim();
    if (!text) return;
    var t = a.target;
    var cur = t.value.replace(/\s+$/, '');
    if (!cur) t.value = text;
    else if (a.first) t.value = cur + '\n' + text;                            // 押し直したら改行
    else t.value = cur + (/[。．.!！?？、」』）)]$/.test(cur) ? '' : '。') + text;   // 同じ録音の続きは「。」でつなぐ
    a.first = false;
    a.lastText = text;
    a.lastHeard = Date.now();
    t.scrollTop = t.scrollHeight;
    t.dispatchEvent(new Event('input', {bubbles: true}));
  }

  // 確定した言葉を欄に入れる。直前と同じ言葉（Android の重なり）や、止めたときに先に入れた言葉の残りは除く
  function commitFinal(a, text) {
    text = (text || '').trim();
    if (!text) return;
    if (a.committed) {                      // 止めたときに途中の言葉を先に入れていた
      var c = a.committed; a.committed = '';
      if (norm(text) === norm(c) || norm(c).indexOf(norm(text)) === 0) return;
      if (norm(text).indexOf(norm(c)) === 0) {
        var t = a.target, v = t.value.replace(/\s+$/, '');
        if (v.slice(-c.length) === c) {       // 先に入れた途中の言葉を、確定した言葉に置き換える
          t.value = v.slice(0, v.length - c.length) + text;
          a.lastText = text;
          t.dispatchEvent(new Event('input', {bubbles: true}));
          return;
        }
        text = text.slice(c.length).replace(/^[\s。、]+/, '');
        if (!text) return;
      }
    }
    if (a.lastText && norm(text) === norm(a.lastText) && Date.now() - a.lastHeard < 4000) return;
    append(a, text);
  }

  function keepAwake(a, on) {
    try {
      if (on && navigator.wakeLock && !a.lock) {
        navigator.wakeLock.request('screen').then(function (l) { if (active === a) a.lock = l; else l.release(); })
          .catch(function () { /* 使えない端末 */ });
      } else if (!on && a.lock) {
        a.lock.release(); a.lock = null;
      }
    } catch (e) { /* 使えない端末 */ }
  }

  function paint(a) {
    if (a.compact) { a.btn.innerHTML = '<i class="bi bi-stop-fill"></i>'; return; }
    a.btn.innerHTML = '<i class="bi bi-stop-fill"></i> 止める ' + clock(Math.floor((Date.now() - a.started) / 1000));
  }

  function stop(message) {
    if (!active) return;
    var a = active;
    active = null;
    clearInterval(a.timer);
    clearTimeout(a.retryTimer);
    keepAwake(a, false);
    if (a.pending) {                        // まだ確定していない言葉も捨てずに入れる
      var p = a.pending; a.pending = '';
      append(a, p);
      a.committed = p;                      // あとから同じ言葉が確定しても重ねない
    }
    try { if (a.rec) a.rec.stop(); } catch (e) { /* もう止まっている */ }
    a.btn.classList.remove('recording');
    a.btn.innerHTML = a.label;
    liveBox(a.target).textContent = '';
    if (message) note(a.btn, message);
  }

  function listen(a) {
    var rec = new SR();
    rec.lang = 'ja-JP';
    rec.continuous = !ONE_SHOT;
    rec.interimResults = true;
    var done = 0;                           // この聞き取りで、何番目の結果まで欄に入れたか
    a.rec = rec;
    a.sessionStart = Date.now();
    a.pending = '';
    rec.onresult = function (e) {
      var interim = '';
      for (var i = done; i < e.results.length; i++) {
        var r = e.results[i];
        if (r.isFinal) {
          if (i === done) {
            done = i + 1;
            a.pending = '';
            commitFinal(a, r[0].transcript);
            a.netFails = 0;
          }
        } else {
          interim += r[0].transcript;
        }
      }
      if (active !== a) return;            // 止めたあとに届いた確定の言葉は入れる。途中の表示はしない
      a.pending = interim.trim();
      if (a.pending) a.lastHeard = Date.now();
      liveBox(a.target).textContent = a.pending ? '聞き取り中：' + a.pending : '';
    };
    rec.onerror = function (e) {
      if (active !== a) return;
      a.lastError = e.error;
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        stop(a.restarts
          ? 'この端末では、自動で聞き取りをつなぎ直せないため止まりました。続けるときは、もう一度ボタンを押してください。'
          : 'マイクの使用が許可されていません。ブラウザのアドレス欄の鍵マークから、マイクを「許可」にしてください。');
      } else if (e.error === 'audio-capture') {
        stop('マイクが見つかりません。マイクがつながっているか、ほかのアプリがマイクを使っていないか確かめてください。');
      } else if (e.error === 'language-not-supported') {
        stop('この端末では日本語の音声入力が使えません。');
      } else if (e.error === 'network') {
        a.netFails = (a.netFails || 0) + 1;
        if (a.netFails > MAX_NET_RETRY) stop('音声の聞き取りに通信が必要です。電波の良い所でもう一度押してください。');
      }
      // no-speech・aborted などは onend でつなぎ直す
    };
    rec.onend = function () {
      if (a.pending) {                      // 確定しないまま終わった言葉（iPhone など）も入れる
        var p = a.pending; a.pending = '';
        append(a, p);
      }
      if (active !== a) return;             // 止めるボタンで止めた
      liveBox(a.target).textContent = '';
      a.quickEnds = (Date.now() - a.sessionStart < 1000 && !a.lastError) ? (a.quickEnds || 0) + 1 : 0;
      a.lastError = '';
      if (a.quickEnds > MAX_QUICK_END) {
        stop('音声の聞き取りがすぐに止まってしまいます。ほかのタブやアプリでマイクを使っていないか確かめて、もう一度押してください。');
        return;
      }
      if (Date.now() - a.lastHeard > SILENCE_MS) {
        stop('しばらく声が聞こえなかったので止めました。続けるときはもう一度押してください。');
        return;
      }
      a.restarts++;
      begin(a, 0);                          // 自動でつなぎ直す
    };
    rec.start();
  }

  // 聞き取りを始める。始め直せないとき（前の聞き取りがまだ終わりきっていない）は少し待ってやり直す
  function begin(a, tries) {
    if (active !== a) return;
    try {
      listen(a);
    } catch (err) {
      if (tries < 4) {
        a.retryTimer = setTimeout(function () { begin(a, tries + 1); }, 300);
      } else {
        stop('音声入力を始められませんでした。少し待ってから、もう一度押してください。');
      }
    }
  }

  function start(btn, target) {
    if (!target) return;
    if (!window.isSecureContext) {
      note(btn, '音声入力は https のページでだけ使えます（いまは http のため、ブラウザがマイクを使わせません）。');
      return;
    }
    if (!SR) {
      note(btn, 'このブラウザは音声入力に対応していません。Chrome・Edge・Safari をお使いください。');
      return;
    }
    var a = {
      btn: btn, target: target, label: btn.innerHTML, compact: !btn.textContent.trim(),
      first: true, started: Date.now(), lastHeard: Date.now(), restarts: 0, lock: null,
      pending: '', committed: '', lastText: '',
    };
    active = a;
    btn.classList.add('recording');
    paint(a);
    a.timer = setInterval(function () { paint(a); }, 1000);
    note(btn, '');
    keepAwake(a, true);
    begin(a, 0);
  }

  function toggle(btn, target) {
    if (typeof target === 'string') target = document.getElementById(target);
    var same = active && active.btn === btn;
    stop();
    if (!same) start(btn, target);
  }

  window.VoiceInput = {toggle: toggle, stop: function () { stop(); }, isActive: function () { return !!active; }};

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-voice-target]');
    if (!btn) return;
    e.preventDefault();
    toggle(btn, btn.getAttribute('data-voice-target'));
  });
  // 画面を離れたら止める（戻ったときに勝手に録音が続かないように）。入りかけの言葉は欄に入れる
  document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); });
  // フォームを送る前に止める（最後の言葉を欄に入れてから送る）
  document.addEventListener('submit', function () { stop(); }, true);
  // 開いていたダイアログ（メモなど）を閉じたら止める
  document.addEventListener('hidden.bs.modal', function (e) {
    if (active && e.target.contains(active.btn)) stop();
  });
})();
