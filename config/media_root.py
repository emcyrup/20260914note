"""
MEDIA_ROOT の決め方。

.env の MEDIA_ROOT は `~` を展開し、作れない・書けないパス（別ユーザーのホームなど）なら
BASE_DIR/media に切り替えて警告を出す。写真・書類の保存が Permission denied で 500 になるのを防ぐ。
"""
import os
import warnings
from pathlib import Path


def choose_media_root(raw, base_dir):
    fallback = Path(base_dir) / 'media'
    raw = (raw or '').strip()
    if not raw:
        return fallback
    path = Path(os.path.expanduser(raw))
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.access(path, os.W_OK):
            return path
        reason = '書き込めません'
    except OSError as e:
        reason = f'作れません（{e.__class__.__name__}）'
    warnings.warn(f'MEDIA_ROOT={raw} は{reason}。代わりに {fallback} を使います（.env の MEDIA_ROOT を確認してください）', stacklevel=2)
    return fallback
