/*
 * 音声入力（Web Speech API）。
 * <button type="button" data-voice-target="テキストエリアのid" data-voice-note="お知らせを出す要素のid"> を置くと、
 * 押しているあいだ話した言葉をテキストエリアの末尾に足していく。もう一度押すと止まる。
 *
 * - 長く話しても止まらない：ブラウザが黙った間などで聞き取りを切っても、止めるボタンを押すまで自動でつなぎ直す
 * - 話している途中の言葉を、欄の下に薄い字で出す（確定したら欄に入る）
 * - 文の切れ目に「。」を付けて続けて書く。ボタンを押し直したときは改行してから書く
 * - 録音中は画面が消えないようにする（スマートフォンで画面が消えると聞き取りが止まるため）
 * マイクは https のページ（または localhost）でしか使えないので、http のときは理由を出す。
 */
(function () {
  'use strict';
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  // Android の Chrome は continuous だと同じ言葉が何度も返ることがあるので、1文ずつ聞いてつなぎ直す
  var ONE_SHOT = /Android/i.test(navigator.userAgent);
  var MAX_RESTART_FAIL = 5;       // 続けて何も聞き取れずに切れた回数がこれを超えたら止める
  var active = null;

  function note(btn, text) {
    var id = btn.getAttribute('data-voice-note');
    var el = id && document.getElementById(id);
    if (el) { el.textContent = text; el.classList.toggle('d-none', !text); }
    else if (text) { alert(text); }
  }

  function liveBox(target) {
    var box = target.parentNode.querySelector('.voice-live[data-for="' + target.id + '"]');
    if (!box) {
      box = document.createElement('div');
      box.className = 'voice-live';
      box.setAttribute('data-for', target.id);
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

  function append(target, text, first) {
    text = text.trim();
    if (!text) return;
    var cur = target.value.replace(/\s+$/, '');
    if (!cur) target.value = text;
    else if (first) target.value = cur + '\n' + text;                        // 押し直したら改行
    else target.value = cur + (/[。．.!！?？、」』）)]$/.test(cur) ? '' : '。') + text;  // 同じ録音の続きは「。」でつなぐ
    target.scrollTop = target.scrollHeight;
    target.dispatchEvent(new Event('input', {bubbles: true}));
  }

  function keepAwake(on) {
    try {
      if (on && navigator.wakeLock && !active.lock) {
        navigator.wakeLock.request('screen').then(function (l) { if (active) active.lock = l; else l.release(); })
          .catch(function () { /* 使えない端末 */ });
      } else if (!on && active && active.lock) {
        active.lock.release(); active.lock = null;
      }
    } catch (e) { /* 使えない端末 */ }
  }

  function stop() {
    if (!active) return;
    var a = active;
    keepAwake(false);
    active = null;
    clearInterval(a.timer);
    try { a.rec.stop(); } catch (e) { /* もう止まっている */ }
    a.btn.classList.remove('recording');
    a.btn.innerHTML = a.label;
    liveBox(a.target).textContent = '';
  }

  function listen(a) {
    var rec = new SR();
    rec.lang = 'ja-JP';
    rec.continuous = !ONE_SHOT;
    rec.interimResults = true;
    a.rec = rec;
    a.heard = false;
    rec.onresult = function (e) {
      var interim = '';
      for (var i = e.resultIndex; i < e.results.length; i++) {
        var t = e.results[i][0].transcript;
        if (e.results[i].isFinal) {
          append(a.target, t, a.first);
          a.first = false;
          a.heard = true;
        } else {
          interim += t;
        }
      }
      liveBox(a.target).textContent = interim ? '聞き取り中：' + interim : '';
    };
    rec.onerror = function (e) {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        note(a.btn, 'マイクの使用が許可されていません。ブラウザのアドレス欄の鍵マークから、マイクを「許可」にしてください。');
        stop();
      } else if (e.error === 'network') {
        note(a.btn, '音声の聞き取りに通信が必要です。電波の良い所でもう一度押してください。');
        stop();
      } else if (e.error === 'audio-capture') {
        note(a.btn, 'マイクが見つかりません。マイクがつながっているか確かめてください。');
        stop();
      }
      // no-speech・aborted などは onend でつなぎ直す
    };
    rec.onend = function () {
      if (active !== a) return;                 // 止めるボタンで止めた
      a.fails = a.heard ? 0 : a.fails + 1;
      if (a.fails > MAX_RESTART_FAIL) {
        note(a.btn, 'しばらく声が聞こえなかったので止めました。続けるときはもう一度押してください。');
        stop();
        return;
      }
      liveBox(a.target).textContent = '';
      try { listen(a); } catch (err) { stop(); }  // 自動でつなぎ直す
    };
    rec.start();
  }

  function start(btn) {
    var target = document.getElementById(btn.getAttribute('data-voice-target'));
    if (!target) return;
    if (!window.isSecureContext) {
      note(btn, '音声入力は https のページでだけ使えます（いまは http のため、ブラウザがマイクを使わせません）。');
      return;
    }
    if (!SR) {
      note(btn, 'このブラウザは音声入力に対応していません。Chrome・Edge・Safari をお使いください。');
      return;
    }
    var a = {btn: btn, target: target, label: btn.innerHTML, first: true, fails: 0, started: Date.now(), lock: null};
    active = a;
    btn.classList.add('recording');
    var paint = function () {
      btn.innerHTML = '<i class="bi bi-stop-fill"></i> 止める ' + clock(Math.floor((Date.now() - a.started) / 1000));
    };
    paint();
    a.timer = setInterval(paint, 1000);
    note(btn, '');
    keepAwake(true);
    try { listen(a); } catch (e) { stop(); }
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-voice-target]');
    if (!btn) return;
    e.preventDefault();
    var same = active && active.btn === btn;
    stop();
    if (!same) start(btn);
  });
  // 画面を離れたら止める（戻ったときに勝手に録音が続かないように）
  document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); });
})();
