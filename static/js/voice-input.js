/*
 * 音声入力（Web Speech API）。
 * <button type="button" class="voice-btn" data-voice-target="テキストエリアのid"> を置くと、
 * 押しているあいだ話した言葉をテキストエリアの末尾に足していく。もう一度押すと止まる。
 * マイクは https のページ（または localhost）でしか使えないので、http のときは理由を出す。
 */
(function () {
  'use strict';
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var active = null;

  function note(btn, text) {
    var id = btn.getAttribute('data-voice-note');
    var el = id && document.getElementById(id);
    if (el) { el.textContent = text; el.classList.toggle('d-none', !text); }
    else if (text) { alert(text); }
  }

  function stop() {
    if (!active) return;
    try { active.rec.stop(); } catch (e) { /* もう止まっている */ }
    active.btn.classList.remove('recording');
    active.btn.innerHTML = active.label;
    active = null;
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
    var rec = new SR();
    rec.lang = 'ja-JP';
    rec.continuous = true;
    rec.interimResults = false;
    active = {rec: rec, btn: btn, label: btn.innerHTML};
    btn.classList.add('recording');
    btn.innerHTML = '<i class="bi bi-stop-fill"></i> 止める';
    note(btn, '');
    rec.onresult = function (e) {
      var text = '';
      for (var i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) text += e.results[i][0].transcript;
      }
      if (!text) return;
      target.value = target.value ? target.value.replace(/\s+$/, '') + '\n' + text : text;
      target.dispatchEvent(new Event('input', {bubbles: true}));
    };
    rec.onerror = function (e) {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        note(btn, 'マイクの使用が許可されていません。ブラウザのアドレス欄の鍵マークから、マイクを「許可」にしてください。');
      }
      stop();
    };
    rec.onend = function () { if (active && active.rec === rec) stop(); };
    rec.start();
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-voice-target]');
    if (!btn) return;
    e.preventDefault();
    var same = active && active.btn === btn;
    stop();
    if (!same) start(btn);
  });
})();
