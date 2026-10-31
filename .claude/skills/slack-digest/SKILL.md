---
name: slack-digest
description: Slackのチャンネル投稿を取得し、ニュース形式のダイジェストに要約する。デフォルトは昨日 (5:00〜29:00)、日付や期間の指定も可能。「Slackまとめ」「Slackダイジェスト」「Slackの情報を集約して」と言われたときに使う。
argument-hint: "[YYYY-MM-DD | YYYY-MM-DD..YYYY-MM-DD]"
model: sonnet
allowed-tools: Bash(uv run .claude/skills/slack-digest/scripts/fetch_slack_browser.py:*)
---

# Slack Digest

Slack MCPが使えない環境向けに、Playwright (ブラウザ操作) で投稿を取得し、ニュース形式に集約する。

引数: $ARGUMENTS

## 事前準備

- Google Chrome をインストールしておく。
- `config.example.json` を `config.json` にコピーし、`channel_urls` に対象チャンネルのURL (`https://app.slack.com/client/Txxx/Cxxx`) を設定する。`workspace_url` を設定するとパーマリンクが正しく出力される。
- 初回のみ、ユーザー自身のターミナルでログインする (ブラウザが開くのでSlackにログイン)。セッションは `.browser-profile/` に保存される。

  ```
  uv run .claude/skills/slack-digest/scripts/fetch_slack_browser.py --login
  ```
- 認証情報やプロファイルをコミットしない (`.gitignore` 済み)。

## 手順

1. 対象期間を決める。
   - 引数なし: 昨日の5:00〜翌5:00 (オプションなし)。日付指定も同様に当日5:00〜翌5:00
   - `YYYY-MM-DD` (または「昨日」等の自然な日付表現を絶対日付に変換): `--date YYYY-MM-DD`
   - `YYYY-MM-DD..YYYY-MM-DD`: `--start YYYY-MM-DD --end YYYY-MM-DD`
   - 時間数の指定 (例: 「12時間」): `--hours 12`
2. 取得スクリプトを `uv run` で実行する。

   ```
   uv run .claude/skills/slack-digest/scripts/fetch_slack_browser.py [--date D | --start D --end D | --hours N] [--keyword W] [--no-threads]
   ```

   出力は対象期間 (`from`/`to`) とチャンネルごとの投稿のJSON。対象は「期間内に立った投稿」と「期間内に返信が付いたスレッド (`new_post: false` が付く。新規投稿は項目なし)」。各投稿に時刻・投稿者・本文・返信数・リアクション数・パーマリンク・`thread` (返信の時刻/投稿者/本文)・`last_reply`・`active_replies_in_period` を含む。継続スレッド (`new_post: false`) の `thread` は期間内の返信のみで、それ以前の返信は `earlier_context` に入る。メンション行の不在情報・cc羅列は圧縮済みで、宛先 (誰宛か) は残る。
   - 既定でチャンネルを5プロセスで並列取得する (`--workers N` で変更、1で逐次。`--channel 名前` で特定チャンネルのみ)。
   - 絞り込みは `config.json` の `filters` (`thread_lookback_days`, `max_threads_per_channel`, `min_thread_replies`, `max_replies_per_thread`, `max_text_chars`, `include_keywords`, `exclude_keywords`, `exclude_users`) と `--keyword` で行う。`--no-threads` で従来の親投稿のみ。
   - 親投稿が期間外でも、`thread_lookback_days` 以内なら返信日時を確認する。画面確認が必要な場合は `--show` を付ける。
3. 失敗時はエラー内容 (未ログイン等) をユーザーに伝え、必要な対応を案内する。`missing_channels` があれば併せて報告する。
4. 出力JSONは1回だけ読み、加工用の追加コマンド (整形表示など) を実行しない。JSONを読み、下記フォーマットで日本語のダイジェストを作成する。
5. ダイジェストをMarkdownファイルに保存し、内容と保存先パスをユーザーに伝える。
   - 保存先: プロジェクト直下の `digests/slack/` (なければ作成)
   - ファイル名: `YYYY-MM-DD.md` (`--date` は対象日、引数なしは昨日の日付、期間指定は `YYYY-MM-DD_YYYY-MM-DD.md`)
   - 同名ファイルがある場合は上書きする
   - `digests/` は `.gitignore` 済み。コミットしない

## 出力フォーマット

```
# 📰 Slackダイジェスト (対象期間)

## #channel-name
- **話題** (継続中 / 新規) [リンク]
  - 相談: 誰 → 誰 に何を相談・依頼したか
  - 決定: 決まったこと (なければ「未決定」)
  - 未解決: 残っている論点・期限 (あれば)

## 更新なしチャンネル
```

## 要約ルール

- スレッドは返信を読み、「誰が誰に何を相談しているか」と「決定事項」を必ず明記する。未解決点・期限は分かれば添える (1〜4行) (`thread` が空でない投稿)。`new_post: false` は「継続中のスレッド」と明記する。
- 返信数・リアクション数が多い投稿、決定事項、障害、期限、メンション付き依頼を優先する。
- 同じ話題は1項目にまとめる。投稿のないチャンネルは末尾に「更新なし」としてまとめる。アクション・依頼事項やその他の独立セクションは作らない。
- 事実のみを書き、推測は明示する。必ず元投稿のリンクを付ける。
- 定型投稿 (退勤予定、朝会テンプレ、実績入力リマインド、進捗の定型報告) は内容に決定・依頼がなければ省略するか1行にまとめる。
- 「相談/決定」に当てはまらない投稿 (告知・共有・承認済みの報告) は「共有: 誰 → 誰/全体 に何を」「結果: 承認済み 等」の形で1〜2行にする。
- 1チャンネルの項目が多い場合は優先度の高い順に最大8項目とし、残りは同種ごと (例: QA問合せ、PRマージ報告) に1項目へ束ねて並べる。
- 同一人物の氏名は原文のまま (日本語名/英字名の統一はしない)。
- 機密情報 (トークン、パスワード等) が含まれていたら出力に含めない。
