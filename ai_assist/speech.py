"""
音声の文字起こし（Google Cloud Speech-to-Text）。

iPhone・iPad のブラウザの音声認識は、1ページで1回しか文字にならないことがある（2回目以降は音が届かない）。
そのため iPhone では、画面で録った音声（16kHz・モノラルの WAV を1分未満ずつ）をサーバーに送り、ここで文字にする。

設定（サーバーの .env）
  GOOGLE_SPEECH_API_KEY  … Google Cloud の API キー（Cloud Speech-to-Text API だけに制限したもの）。空なら使わない
  GOOGLE_SPEECH_MODEL    … 認識モデル（既定 latest_long。使えないときは default でやり直す）
音声はここで Google に送るだけで、サーバーには残さない。

話者を分ける（speakers=True）：Google の speaker diarization で、区切りの中の発言を「話者1：…」「話者2：…」の行にする。
番号は区切り（10〜40 秒）ごとに付け直されるので、同じ人が別の番号になることがある（議事録の AI 整理で名前や役割にまとめる）。
"""
import base64
import logging
import struct

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ENDPOINT = 'https://speech.googleapis.com/v1/speech:recognize'
SAMPLE_RATE = 16000
MAX_SECONDS = 59          # 同期の認識は1分まで
MAX_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 1024


class SpeechError(Exception):
    """画面に出せる文のエラー"""


def enabled():
    return bool(getattr(settings, 'GOOGLE_SPEECH_API_KEY', ''))


def read_wav(data):
    """16bit・モノラル・16kHz の WAV を確かめて、音声の部分（PCM）を返す"""
    if len(data) < 44 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise SpeechError('音声の形式が正しくありません。')
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack('<I', data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b'fmt ' and len(body) >= 16:
            fmt = struct.unpack('<HHIIHH', body[:16])
        elif cid == b'data':
            pcm = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise SpeechError('音声の形式が正しくありません。')
    audio_format, channels, rate, _, _, bits = fmt
    if audio_format != 1 or channels != 1 or bits != 16 or rate != SAMPLE_RATE:
        raise SpeechError('音声の形式が正しくありません（16kHz・モノラル・16bit）。')
    if len(pcm) > SAMPLE_RATE * 2 * (MAX_SECONDS + 1):
        raise SpeechError('音声が長すぎます（1回に送れるのは1分まで）。')
    return pcm


SPEAKERS_MAX = 6          # 1つの区切りに入る話者の数の上限


def _recognize(pcm, model, punctuation, speakers=False):
    config = {
        'encoding': 'LINEAR16',
        'sampleRateHertz': SAMPLE_RATE,
        'languageCode': 'ja-JP',
        'maxAlternatives': 1,
    }
    if model:
        config['model'] = model
    if punctuation:
        config['enableAutomaticPunctuation'] = True
    if speakers:
        config['diarizationConfig'] = {'enableSpeakerDiarization': True, 'minSpeakerCount': 1,
                                       'maxSpeakerCount': SPEAKERS_MAX}
    return requests.post(
        ENDPOINT, params={'key': settings.GOOGLE_SPEECH_API_KEY}, timeout=60,
        json={'config': config, 'audio': {'content': base64.b64encode(pcm).decode('ascii')}},
    )


def speaker_lines(results):
    """
    話者を分けた返答（最後の result の words に speakerTag が付く）を「話者1：…」の行にする。
    speakerTag が無ければ None（呼ぶ側はふつうの文にする）
    """
    words = []
    for r in results:
        alts = r.get('alternatives') or []
        if alts and alts[0].get('words'):
            words = alts[0]['words']        # 話者付きの words は最後の result に全部入る
    if not any(w.get('speakerTag') for w in words):
        return None
    lines, cur, tag = [], [], None
    for w in words:
        t = w.get('speakerTag') or tag
        if cur and t != tag:
            lines.append(f'話者{tag}：' + ''.join(cur))
            cur = []
        tag = t
        cur.append((w.get('word') or '').strip())
    if cur:
        lines.append(f'話者{tag}：' + ''.join(cur))
    return '\n'.join(line for line in lines if line.split('：', 1)[1])


def transcribe(wav_bytes, speakers=False):
    """WAV（16kHz・モノラル・16bit）を文字にする。聞き取れなかったときは空文字。speakers=True で話者ごとの行にする"""
    if not enabled():
        raise SpeechError('音声を文字にする設定（GOOGLE_SPEECH_API_KEY）がサーバーにありません。管理者に設定を依頼してください。')
    pcm = read_wav(wav_bytes)
    model = getattr(settings, 'GOOGLE_SPEECH_MODEL', '') or 'latest_long'
    try:
        res = _recognize(pcm, model, True, speakers)
        if res.status_code == 400:          # モデルや句読点・話者分けが日本語で使えない設定のとき
            logger.warning('音声の文字起こし：%s で 400（%s）。default でやり直す', model, res.text[:300])
            res = _recognize(pcm, 'default', False, speakers)
        if res.status_code == 400 and speakers:
            logger.warning('音声の文字起こし：話者分けで 400（%s）。話者を分けずにやり直す', res.text[:300])
            res = _recognize(pcm, 'default', False, False)
            speakers = False
    except requests.RequestException as e:
        logger.exception('音声の文字起こしで通信エラー')
        raise SpeechError(f'音声を文字にするサービスにつながりませんでした（{type(e).__name__}）。') from e
    if res.status_code != 200:
        logger.error('音声の文字起こしでエラー %s: %s', res.status_code, res.text[:500])
        if res.status_code in (401, 403):
            raise SpeechError('音声を文字にするサービスの設定（API キー）が正しくありません。管理者に確認を依頼してください。')
        if res.status_code == 429:
            raise SpeechError('音声を文字にするサービスが混み合っています。少し待ってから、もう一度お試しください。')
        raise SpeechError(f'音声を文字にできませんでした（{res.status_code}）。')
    results = res.json().get('results') or []
    if speakers:
        lines = speaker_lines(results)
        if lines is not None:
            return lines
    parts = []
    for r in results:
        alts = r.get('alternatives') or []
        if alts and alts[0].get('transcript'):
            parts.append(alts[0]['transcript'].strip())
    return ''.join(parts)
