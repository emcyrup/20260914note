"""
サーバーの中で動かす音声の文字起こし（faster-whisper。外部サービスに音を送らない）。

Google Cloud Speech-to-Text の代わり。公開モデル（Whisper 系）を `.env` の WHISPER_MODEL で選ぶ。
  WHISPER_MODEL    … 例: small（約 250MB・速い）／medium（約 800MB・精度は上がるが遅い）／
                     kotoba-tech/kotoba-whisper-v2.0-faster（日本語向け・約 1.5GB）。空なら使わない
  WHISPER_COMPUTE  … 計算の精度（既定 int8。CPU 向け）
  WHISPER_THREADS  … 使う CPU のスレッド数（既定 0 = CPU 数と 4 の小さいほう。共用サーバーを占有しない）
  WHISPER_BEAM     … 探索の幅（既定 2。大きいほど精度が上がるが遅い）
  WHISPER_DIR      … モデルを置く場所（既定は faster-whisper の既定 ~/.cache/huggingface）
モデルは最初に使うときにダウンロードされ（huggingface.co）、以後はサーバーに残る。
`python manage.py speech_check` で、読み込みと文字起こしにかかる時間を測れる。

話者を分ける機能（diarization）は無い。画面の「話者」ボタンで切り替えた印を付ける（voice-input.js）。
gunicorn のワーカーをまたいで 1 つずつ文字にする（ファイルロック）。共用サーバーの CPU を使い切らないため。
"""
import fcntl
import logging
import os
import threading
import time
from contextlib import contextmanager

from django.conf import settings

logger = logging.getLogger(__name__)

_model = None
_load_lock = threading.Lock()

# 無音や雑音に対して Whisper が出しがちな決まり文句（文字にしない）
HALLUCINATIONS = {
    'ご視聴ありがとうございました', 'ご視聴ありがとうございました。', 'ご視聴ありがとうございます',
    'チャンネル登録お願いします', 'チャンネル登録をお願いします', 'おやすみなさい', 'ありがとうございました',
    'ご清聴ありがとうございました', '字幕', '【字幕】',
}


class WhisperError(Exception):
    """画面に出せる文のエラー"""


def model_name():
    return (getattr(settings, 'WHISPER_MODEL', '') or '').strip()


def enabled():
    return bool(model_name())


def threads():
    n = int(getattr(settings, 'WHISPER_THREADS', 0) or 0)
    return n if n > 0 else max(1, min(4, os.cpu_count() or 1))


def load():
    """モデルを読み込む（プロセスごとに 1 回。最初は時間がかかる）"""
    global _model
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise WhisperError('サーバーに音声の文字起こしの部品（faster-whisper）が入っていません。'
                               '管理者に設定を依頼してください。') from e
        kwargs = {'device': 'cpu', 'compute_type': getattr(settings, 'WHISPER_COMPUTE', '') or 'int8',
                  'cpu_threads': threads()}
        if getattr(settings, 'WHISPER_DIR', ''):
            kwargs['download_root'] = settings.WHISPER_DIR
        t = time.monotonic()
        try:
            _model = WhisperModel(model_name(), **kwargs)
        except Exception as e:      # noqa: BLE001 ダウンロード失敗・メモリ不足など
            logger.exception('Whisper のモデルを読み込めない')
            raise WhisperError(f'音声のモデル（{model_name()}）を読み込めませんでした（{type(e).__name__}）。'
                               '管理者に確認を依頼してください。') from e
        logger.info('Whisper model %s loaded in %.1fs (threads=%s)', model_name(), time.monotonic() - t, kwargs['cpu_threads'])
    return _model


@contextmanager
def _one_at_a_time():
    """ワーカーをまたいで 1 つずつ（共用サーバーの CPU を使い切らない）"""
    path = getattr(settings, 'WHISPER_LOCK', '') or os.path.join(settings.BASE_DIR, 'run', 'whisper.lock')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _audio(pcm):
    """16bit の PCM → -1〜1 の float32 配列（faster-whisper が受け取る形）"""
    import numpy as np
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_pcm(pcm, language='ja'):
    """16kHz・モノラル・16bit の PCM を文字にする。聞き取れなければ空文字"""
    audio = _audio(pcm)
    model = load()
    t = time.monotonic()
    with _one_at_a_time():
        segments, _info = model.transcribe(
            audio, language=language, beam_size=int(getattr(settings, 'WHISPER_BEAM', 2) or 2),
            vad_filter=True, condition_on_previous_text=False, without_timestamps=True,
        )
        text = ''.join((s.text or '').strip() for s in segments)
    logger.info('Whisper %.1fs audio → %d chars in %.1fs', len(audio) / 16000, len(text), time.monotonic() - t)
    text = text.strip()
    if text.rstrip('。') in {h.rstrip('。') for h in HALLUCINATIONS}:
        return ''
    return text
