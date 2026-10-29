"""
音声の文字起こしの設定を確かめ、Whisper（サーバー内）のときは読み込みと文字起こしにかかる時間を測る。

  python manage.py speech_check                 # いまの設定（.env）で
  WHISPER_MODEL=small python manage.py speech_check --seconds 15
  python manage.py speech_check --wav 録音.wav   # 16kHz・モノラル・16bit の WAV を文字にして時間を測る
  python manage.py speech_check --sample [--seconds 12] [--beam 1]
      # 同梱の合成音声（ai_assist/fixtures/speech_sample.wav。架空の支援経過を読み上げた 33 秒。個人の声ではない）の
      # 先頭 N 秒を文字にして時間を測る。区切りの長さや探索の幅（beam）で速さがどう変わるかを見る

出力は CPU の数・メモリ・モデル名・時間だけ（話した言葉は --wav・--sample のときだけ出す）。
"""
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from ai_assist import speech


def _meminfo():
    try:
        with open('/proc/meminfo', encoding='ascii') as f:
            rows = dict(line.split(':', 1) for line in f if ':' in line)
        total = int(rows['MemTotal'].split()[0]) // 1024
        avail = int(rows['MemAvailable'].split()[0]) // 1024
        return f'{total} MB（空き {avail} MB）'
    except Exception:   # noqa: BLE001
        return '不明'


def _tone(seconds):
    """テスト用の音（声ではないので文字にはならない。時間を測るため）"""
    import math
    import struct
    rate = 16000
    out = bytearray()
    for i in range(rate * seconds):
        v = 0.2 * math.sin(2 * math.pi * 220 * i / rate) * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * i / rate))
        out += struct.pack('<h', int(v * 32767))
    return bytes(out)


class Command(BaseCommand):
    help = '音声の文字起こしの設定と速さを確かめる'

    def add_arguments(self, parser):
        parser.add_argument('--seconds', type=int, default=15, help='テスト用の音の長さ（秒）')
        parser.add_argument('--wav', default='', help='文字にする WAV（16kHz・モノラル・16bit）')
        parser.add_argument('--sample', action='store_true', help='同梱の合成音声（架空の文）を使う。--seconds で先頭だけに切れる')
        parser.add_argument('--beam', type=int, default=0, help='探索の幅（WHISPER_BEAM）を一時的に変えて測る')

    def handle(self, *args, **opts):
        out = self.stdout.write
        out(f'CPU: {os.cpu_count()} / メモリ: {_meminfo()}')
        out(f'文字起こし: {speech.backend() or "なし"}（話者分け: {"あり" if speech.diarization() else "なし（画面のボタンで切り替え）"}）')
        if speech.backend() != 'whisper':
            out('WHISPER_MODEL が無いので Whisper の計測はしません。')
            return
        from ai_assist import whisper_local
        beam = opts['beam'] or int(getattr(settings, 'WHISPER_BEAM', 5) or 5)
        out(f'モデル: {whisper_local.model_name()}（compute={getattr(settings, "WHISPER_COMPUTE", "") or "int8"}, threads={whisper_local.threads()}, beam={beam}）')
        t = time.monotonic()
        whisper_local.load()
        out(f'読み込み: {time.monotonic() - t:.1f} 秒（最初はダウンロードを含む）')
        if opts['sample']:
            opts['wav'] = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'fixtures', 'speech_sample.wav')
        if opts['wav']:
            with open(opts['wav'], 'rb') as f:
                pcm = speech.read_wav(f.read())
            if opts['sample'] and opts['seconds'] and opts['seconds'] * 32000 < len(pcm):
                pcm = pcm[:opts['seconds'] * 32000]
            label = f'{"合成音声" if opts["sample"] else "WAV"} {len(pcm) / 32000:.1f} 秒'
        else:
            pcm = _tone(opts['seconds'])
            label = f'テスト音 {opts["seconds"]} 秒'
        for n in (1, 2):
            t = time.monotonic()
            text = whisper_local.transcribe_pcm(pcm, beam=beam)
            out(f'{label} → {time.monotonic() - t:.1f} 秒（{n} 回目、{len(text)} 字）')
        if opts['wav']:
            out(text)
        elif text:
            out(f'（テスト音に対して文が出ました：「{text[:40]}」。無音・雑音への決まり文句なら HALLUCINATIONS に足す）')
