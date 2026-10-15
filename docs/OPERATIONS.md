# 運用の手順（更新・切り戻し・障害時）

対象：AWS 共用サーバーの本番 **ゆあーず**（https://michinote.yours.ai-labo.cloud/ ・ `~/michinoteyours` ・ポート 8030）と **オウル**（https://michinote.owl.ai-labo.cloud/ ・ `~/michinoteowl` ・ポート 8034）。
開発環境（https://st-michinotedemo.ai-labo.cloud/ ・ `~/michinotedemo`）は develop の古いコードのまま置いてあり、この手順の対象外。

> パスワード・`SECRET_KEY`・API キー・SSH 鍵はこのファイルにも Actions のログにも書かない。置き場所はサーバーの `.env` と GitHub Secrets だけ。

## 1. 仕組み（まとめ）

| 何を | どうなっているか |
|---|---|
| 配備 | ブランチ `ryoiku` に push すると Actions の **Deploy (yours)** と **Deploy (owl)** が動く：テスト全件 → サーバーで `deploy/venv-deploy.sh <コミット>`（コードの切り替え・pip・migrate・collectstatic・`check --deploy`）→ Gunicorn に HUP（ワーカーだけ入れ替え。ポートは開いたまま）→ 公開 URL の `/healthz/` が 200 か確かめる。テストが 1 件でも落ちたら配備しない |
| 戻し方 | Deploy (yours)／Deploy (owl) を手動実行し、`ref` に前のコミットの番号を入れる（3 章） |
| 自動起動 | サーバーの crontab に `@reboot`（再起動のあと 30 秒で起動）と 5 分ごとの `--ensure`（止まっていれば起動）。Manage の `autostart_install` で入れる（4 章） |
| 見回り | Actions の **Watch (production)** が 15 分ごとに両方の `/healthz/` を確かめ、応答が無ければサーバーで `--ensure` して立て直し、その実行を「失敗」にして知らせる（GitHub から失敗のメールが届く） |
| バックアップ | 毎日 3:15 に `venv-deploy.sh --backup`（crontab。`autostart_install` で入る）が DB を `backups/` に取り、14 日ぶん残す。Manage の `backup`（いま取る）・`backup_list`・`backup_check`（2 日以内に取れているか）。写真・書類（`media/`）はサーバーのディスクにあるだけなので、プロバイダのサーバーのバックアップに頼る（7 章） |
| 確かめる道具 | **Site check**（外から healthz と 3MB の送信）・**Log check**（直近の 500 の件数と場所）・**Manage** の `status`（動いているか・いまのコミット・最近の配備）と `error_log` |

## 2. ふだんの更新の流れ

1. 作業ブランチで直し、**テスト全件**（`python manage.py test`）と、手元のブラウザで変えた画面を確かめる。
2. **更新する時間帯**：営業時間の外にする。ゆあーずの営業は平日 10:00〜18:00・土日祝 9:00〜17:00（月・木休み）なので、**平日 18:30 以降か朝 9:00 前、または月・木**。オウルの営業時間に合わせて、両方にとって外の時間を選ぶ。migration（DB の形の変更）がある更新は特に営業時間外に。
3. `ryoiku` に push → Deploy (yours)・Deploy (owl) の両方が緑（成功）になるのを確かめる（どちらも 5 分ほど）。
4. **Site check (yours)／Site check (owl)** を手動実行して緑を確かめ、本番の画面を 1〜2 か所開く（ログインできる・変えた画面が出る）。
5. 変えた内容を Notion の「みちノート展開版」の末尾に書く（コミットの番号も）。

### ステージング（本番の前に確かめる場所）について
- いまは本番の前に確かめる場所が「手元の環境＋テスト全件」だけ（開発環境は古いコードのまま残す方針のため）。
- **提案**：同じサーバーに「ステージング」を 1 つ作る（プロバイダに、サブドメイン（例：`michinote-stg.ai-labo.cloud`）・nginx の転送先ポート（例：8036）・DB を 1 つ依頼）。作れたら Deploy (staging) を用意し、`ryoiku` に入れる前にステージングで確かめてから本番へ、という順にする。費用・手間とのかね合いで判断する。

## 3. 切り戻し（前の版に戻す）

1. GitHub の **Commits**（ブランチ `ryoiku`）で、戻したい先のコミットの番号（例：`f333cf7`）を確かめる。Notion の記録にも番号がある。
2. **migration の確認**：戻す先と今のあいだに `migrations/` の新しいファイルがあるか見る。
   - **無い**：そのまま 3 へ。
   - **ある**：新しいコードのうちに DB を戻してから切り戻す（新しい列が残ると古いコードの保存が失敗することがあるため）。例：サーバーで `python manage.py migrate reservations 0014`（戻す先のコードにある最後の番号）。不安なときは先にバックアップ（`deploy/backup.sh`）。切り戻しの配備のログに `WARNING: DB に適用ずみで、このコードに無い migration があります` と出たら、この手順が要る。
3. Actions → **Deploy (yours)**（オウルは **Deploy (owl)**）→ Run workflow → `ref` に戻す先のコミットの番号を入れて実行。テストも戻す先のコードで動く。
4. Site check で確かめ、Notion に「いつ・どの番号に戻したか・理由」を書く。
5. 直した版ができたら、ふつうの更新（2 章）で入れ直す。

## 4. サーバーの再起動への備え（各環境で 1 回）

- Actions → **Manage (yours)** → task を `autostart_install` にして実行。**Manage (owl)** でも同じ。
- crontab にこの環境の行だけを入れる（ほかの行は触らない。以前に手で入れた `@reboot … venv-deploy.sh` の行があれば置き換える）。`autostart_check` で中身を確かめられる。
- 入る行（例）：`@reboot sleep 30 && … venv-deploy.sh --ensure` と `*/5 * * * * … venv-deploy.sh --ensure`。`--ensure` は止まっているときだけ起動し、動いていれば何もしない。
- サーバーに cron が無いときは `ERROR: crontab コマンドがありません` と出る。そのときはプロバイダに自動起動の方法を相談し、それまでは Watch (production) の立て直しに頼る。

## 5. 障害のとき

1. **気づく**：Watch (production) の失敗のメール、または利用者からの連絡。
2. **状態を見る**：Manage の `status`（動いているか・いまのコミット）と `error_log`（エラーの行。日本語と長い数字は伏せて出る）、Log check（500 の件数）。
3. **立て直す**：
   - 止まっている → Manage の `status` で確かめてから、Deploy を手動実行（同じコミットで入れ直す。止まっていれば起動もする）。Watch が先に `--ensure` で起動していることも多い。
   - 更新のあとから 500 が出る → 3 章で前の版に戻す。
   - サーバーそのものが落ちている（SSH もつながらない）→ プロバイダに連絡。
4. **記録**：Notion に、いつ・何が起きたか・原因・やったこと・再発を防ぐことを書く。利用者に影響があったときは報告する。

## 6. 2026-10-06 の件（デモの時間の一時エラー）

- **起きたこと**：10/6 にプロバイダがサーバーを増強（CPU 4・メモリ 16GB）して再起動した。アプリ（Gunicorn）は再起動のあと自動では上がらない構成だったため、手で起動し直すまで画面がエラーになり、同じ日のデモに重なった。
- **その場の対応**：Actions の Deploy を手動実行してアプリを起動し直し、healthz 200 を確かめた（ゆあーず・開発環境）。
- **再発を防ぐこと**：
  1. サーバーの再起動のあとに自動で起動し、5 分ごとに止まっていないか見回る（4 章。`deploy/venv-deploy.sh --autostart install`）。
  2. 外からも 15 分ごとに見回り、止まっていれば立て直して知らせる（Watch (production)）。
  3. 更新は営業時間の外に行い、前の版にすぐ戻せる手順を用意した（2・3 章。Deploy の `ref`）。
  4. デモの前日に Site check と Manage の `status` で状態を確かめる。

## 7. バックアップと復旧

- **何を**：DB（利用者・記録・予約・設定のすべて）。PostgreSQL は `pg_dump`、無い環境は `manage.py dumpdata`。`~/michinoteyours/backups/`・`~/michinoteowl/backups/` に `db-日時.sql.gz` で置き、14 日より古いものは消す。中身は全利用者の記録と職員のパスワードのハッシュなので、フォルダは持ち主だけが読める（700、ファイルは 600）。取り損ねたときは途中のファイルを残さない（`backup: FAILED`）。
- **いつ**：毎日 3:15（crontab。`autostart_install` を一度実行した環境）。手で取るときは Manage の `backup`。配備の前に取っておくとよい。
- **確かめる**：Manage の `backup_check`（最後のバックアップが 2 日以内なら OK）。前日点検の項目に入れる。
- **戻す**（DB を壊した・消したとき。プロバイダに頼らず自分で）：サーバーで `gunzip -c backups/db-….sql.gz | psql -h localhost -U <DB_USER> <DB_NAME>`（空の DB に入れる。既存の DB に上書きするときは先に `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`）。dumpdata のときは `manage.py loaddata`。戻したあと Site check。
- **写真・書類**（`media/`）：バックアップの対象に入っていない。プロバイダのサーバーのバックアップ（有無・頻度）を確かめ、必要なら `tar` で別の場所に写す手順を足す。
- **残すところ**：いまは同じサーバーの中にしか無い。サーバーごと失うと戻せないので、SLA を決めるときに「別の場所（S3 など）にも日次で写す」を入れるか判断する（`deploy/backup.sh` に Cloud Storage へ送る例がある）。

