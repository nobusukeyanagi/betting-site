# 競輪オート投票ニュース

`betting-site` — 競輪・オートレースの投票サイトが公式に発信する**お知らせ、キャンペーン、サービス更新、コラム、イベント**を一覧表示する GitHub Pages サイトです。スポーツ競技ニュースの全文転載ではなく、記事の見出し、日付、公式記事へのリンクを集約します。

## 公開先

`https://<GitHubユーザー名>.github.io/betting-site/`

`jbsbackgammon` など別の GitHub アカウントでも構成変更なしで動作します。公開 URL はリポジトリ所有者によって変わります。

## GitHubの設定

1. GitHubに公開リポジトリ **`betting-site`** を新規作成し、ZIP内のフォルダの**中身**をリポジトリ直下にアップロードします。`data/news.json`、`.github/workflows/update.yml` もアップロードしてください（隠しフォルダに注意）。
2. **Settings → Pages → Build and deployment → Source** で **GitHub Actions** を選択します。
3. **Settings → Actions → General → Workflow permissions** は、スナップショットを保存するために **Read and write permissions** に設定します。組織ポリシーで変更できない場合は管理者による権限調整が必要です。
4. **Actions → Update betting-site news → Run workflow** を一度実行してください。完了するとPagesが公開されます。

## 自動更新

- 1日1回、日本時間 **01:00**。GitHub Actions の `cron: '0 16 * * *'` は UTC 16:00（日本時間翌01:00）です。GitHub側の実行混雑により開始が遅れる場合があります。
- 任意のタイミングでは Actions の **Run workflow** から手動更新できます。
- `ubuntu-24.04` を固定し、Node.js 24 世代に対応する Action を使用しています。旧ランナーの移行注意や旧ActionのNode警告を避ける構成です。ただしサイト側の変更やGitHub側の新しい仕様による将来の警告まで保証するものではありません。
- 成功時は `data/news.json` をリポジトリにコミットし、翌日の NEW 判定の基準にします。自動コミットが次の再実行を生じさせないよう push トリガーは設定していません。
- サイトにアクセスできない場合、当該サイトの前回分を保持し「前回取得分」と表示します。初回から取得できないサイトは、**取得元・取得状況**の欄に「取得不可」と表示します。他のサイトの取得は継続します。
- robots.txt・アクセス制限に従い、ログイン、CAPTCHA回避、非公開API利用はしません。各サイトの取得成功は保証できません。GitHub Actions での実サイト疎通確認はアップロード後に必要です。
- 最初の収集では取得済み記事を一斉に NEW にせず、2回目以降に新しいURLが検出された場合だけ NEW を表示します。

## 取得元

| サイト | 取得ページ | 取り扱う主な情報 |
|:--|:--|:--|
| Gamboo | https://gamboo.jp/news/list | お知らせ（アクセス制限がある場合は取得不可） |
| WINTICKET | https://www.winticket.jp/keirin/topics/ | 競輪・お知らせ |
| WINTICKET | https://www.winticket.jp/keirin/column | 競輪・コラム |
| WINTICKET | https://www.winticket.jp/autorace/topics/ | オート・お知らせ |
| WINTICKET | https://www.winticket.jp/autorace/column | オート・コラム |
| オッズパーク | https://www.oddspark.com/info/ | お知らせ |
| オッズパーク | https://www.oddspark.com/pickup/ | ピックアップ |
| 楽天Kドリームス | https://info2.kdreams.jp/ | インフォメーション |
| チャリロト | https://www.chariloto.com/articles | ニュース（ご指定URLが2回出ていたので1系統に統一） |
| KEIRIN.JP | https://keirin.jp/pc/topicslist | 競輪トピックス（ページが動的表示のため取得不可になる場合あり） |
| AUTORACE.JP | https://autorace.jp/news/view/ | 同ページからリンクされている下記「キャンペーン」「イベント」「アップデート」の公式分類ページを収集。レース記事を優先しない |
| AUTORACE.JP | https://autorace.jp/news/campaign/view/ | キャンペーン |
| AUTORACE.JP | https://autorace.jp/news/event/view/ | イベント |
| AUTORACE.JP | https://autorace.jp/news/update/view/ | アップデート |
| netkeirin | https://keirin.netkeiba.com/info/ | キャンペーン・お知らせ |
| DMM競輪 | https://keirin.dmm.com/news | お知らせ（動的表示等で取得不可になる場合あり） |
| みんなの競輪 | https://bet.minnano-keirin.com/info | お知らせ |
| TIPSTAR | https://about.tipstar.com/news/ | お知らせ |
| Loto Place | https://www.lotoplace.jp/articles | ニュース |
| 当たるんです | https://www.atarundesu.com/notification | お知らせ（アクセス制限時は取得不可） |
| PIST6 | https://chiba.pist6.com/news | ニュース |
| AOKEIスタジアム | https://www.aokei-stadium.com/articles | ニュース |
| Betimo | https://www.betimo.co.jp/ | **`/news/` 配下のみ** |

リストの最新12件（WINTICKETコラムのみ8件）を対象とします。複数サイトに同じURLが掲載されている場合は重複表示しません。記事の日付が取得できる場合は日付降順に表示し、日付を取得できないものは後方に表示します。

## ファイル

- `scripts/build.py` — 取得、サイトごとの抽出ルール、NEW判定、HTML生成
- `data/news.json` — 前回結果（GitHub Actionsから更新）
- `.github/workflows/update.yml` — 自動更新、テスト、Pagesデプロイ
- `tests/test_build.py` — サイト構造の例による抽出テスト
- `index.html` — 初回公開前の案内ページ。Actions実行後、Pagesでは生成済みページに置き換わります。

## 手動テスト

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/build.py --from-snapshot --output site
```

オンライン取得を行うときは `--from-snapshot` を外します。独自の非公開情報を取得する実装ではありません。
