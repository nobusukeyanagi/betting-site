import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('betting_build', Path(__file__).resolve().parents[1] / 'scripts/build.py')
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class ScraperTests(unittest.TestCase):
    def feed(self, key):
        return next(feed for feed in mod.FEEDS if feed.key == key)

    def test_duplicate_source_removed(self):
        self.assertEqual(len(mod.FEEDS), len({f.key for f in mod.FEEDS}))
        self.assertEqual(len([f for f in mod.FEEDS if f.site == 'チャリロト']), 1)

    def test_gamboo_query_url(self):
        f = self.feed('gamboo')
        self.assertEqual(mod.safe_url(f, '/news/?tid=info&tsid=03477'),
                         'https://gamboo.jp/news/?tid=info&tsid=03477')
        self.assertIsNone(mod.safe_url(f, '/news/'))

    def test_betimo_only_news(self):
        f = self.feed('betimo')
        self.assertEqual(mod.safe_url(f, '/news/261019/'), 'https://www.betimo.co.jp/news/261019')
        self.assertIsNone(mod.safe_url(f, '/about/'))
        self.assertIsNone(mod.safe_url(f, 'https://evil.example/news/261019/'))
        html = '<a href="/news/261019/">Betimoが新サービスを発表</a><a href="/">ホーム</a>'
        self.assertEqual(len(mod.extract_articles(f, html)), 1)

    def test_anchor_and_date(self):
        f = self.feed('odds-info')
        html = '<ul><li>2026/10/22 <a href="/info/2026/10/post-687.html">【障害】ネット新聞の配信遅延につきまして</a></li></ul>'
        items = mod.extract_articles(f, html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['date'], '2026-10-22')

    def test_dates_and_fake_dates(self):
        self.assertEqual(mod.date_from('2026年10月20日'), '2026-10-20')
        self.assertEqual(mod.date_from('2026.09.18'), '2026-09-18')
        self.assertEqual(mod.date_from('2026-02-30'), '')

    def test_keirinjp_and_netkeirin_query_identity(self):
        f = self.feed('keirinjp')
        self.assertEqual(mod.safe_url(f, '/pc/topicsdetail?nnum=18119'),
                         'https://keirin.jp/pc/topicsdetail?nnum=18119')
        self.assertIsNone(mod.safe_url(f, '/pc/topicsdetail'))
        f = self.feed('netkeirin')
        self.assertEqual(mod.safe_url(f, '/info/info_detail/?id=1585'),
                         'https://keirin.netkeiba.com/info/info_detail/?id=1585')

    def test_winticket_date_in_link(self):
        f = self.feed('winkeirin-topics')
        html = '<a href="/keirin/topics/3ugXHe9NcdrDx6qn9LHYhC">小田原競輪F1 初日中止について2026年10月20日</a>'
        items = mod.extract_articles(f, html)
        self.assertEqual(items[0]['date'], '2026-10-20')
        self.assertNotIn('2026年10月20日', items[0]['title'])

    def test_pist6_heading_only(self):
        f = self.feed('pist6')
        html = '<a href="/news/10390.html"><h2>FAIRLADY Z 大集合、11月1日に開催</h2><p>' + 'イベント開催の概要。' * 70 + '</p><time>2026年10月1日</time></a>'
        items = mod.extract_articles(f, html)
        self.assertEqual(items[0]['title'], 'FAIRLADY Z 大集合、11月1日に開催')
        self.assertEqual(items[0]['date'], '2026-10-01')

    def test_html_escape_and_unavailable(self):
        old = mod.load_snapshot(None)
        html = mod.render(old)
        self.assertIn('競輪オート投票ニュース', html)
        self.assertIn('取得不可', html)
        f = self.feed('betimo')
        old['feeds'][f.key] = {'status': 'ok', 'last_success': '', 'items': [
            {'title': '<script>alert(1)</script>', 'url': 'https://www.betimo.co.jp/news/261019/',
             'date': '2026-10-19', 'site': 'Betimo', 'section': 'ニュースリリース', 'new': True}]}
        html = mod.render(old)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert(1)', html)
        self.assertIn('NEW', html)

    def test_article_body_extraction_without_site_navigation(self):
        document = """<html><body>
        <header><a href='/'>トップへ</a></header>
        <main><article><h1>料金改定のお知らせ</h1>
        <div class='article-body'><p>いつもご利用いただき、ありがとうございます。</p>
        <p>11月から一部サービスの料金を変更いたします。詳細は以下のとおりです。</p>
        <h2>ご案内</h2><ul><li>2026年11月1日より適用します。</li></ul>
        </div><div class='related'>関連記事 <a href='/news/1'>別のお知らせ</a></div>
        </article></main><footer><p>プライバシーポリシーなどのご案内です。</p></footer>
        </body></html>"""
        blocks = mod.extract_body(document, '料金改定のお知らせ')
        self.assertIn('いつもご利用いただき、ありがとうございます。', blocks)
        self.assertIn('2026年11月1日より適用します。', blocks)
        self.assertNotIn('プライバシーポリシーなどのご案内です。', blocks)
        self.assertNotIn('別のお知らせ', blocks)

    def test_jsonld_article_body(self):
        document = """<script type="application/ld+json">{"@type":"NewsArticle","articleBody":"第1段落：新サービスを開始します。第2段落：今後も順次拡大していく予定です。利用可能なサービスを増やしてまいります。"}</script>
        <main><a href='/a'>関連記事</a></main>"""
        blocks = mod.extract_body(document, '新サービス')
        self.assertTrue(any('新サービスを開始します' in b for b in blocks))
        self.assertFalse(any('関連記事' in b for b in blocks))

    def test_right_column_text_source_link_and_left_ellipsis(self):
        feed = self.feed('odds-info')
        snapshot = mod.load_snapshot(None)
        snapshot['feeds'][feed.key] = {'status': 'ok', 'items': [{
            'title': 'サービスのメンテナンスについて',
            'url': 'https://www.oddspark.com/info/2026/10/post-687.html',
            'date': '2026-10-22', 'body': ['サービスの本文がこちらに入ります。']}]}
        page = mod.render(snapshot)
        self.assertIn('サービスの本文がこちらに入ります。', page)
        self.assertIn('text-overflow:ellipsis', page)
        self.assertIn('class="meta"><a href="https://www.oddspark.com/info/2026/10/post-687.html"', page)
        self.assertNotIn('公式サイトで記事を読む', page)
        self.assertNotIn('すべてのサイト', page)
        self.assertNotIn('visible-count', page)
        self.assertNotIn('class="count"', page)

    def test_eight_titles_per_site_across_multiple_feeds(self):
        snap = mod.load_snapshot(None)
        snap['feeds'] = {}
        for feed in mod.FEEDS:
            if feed.site == 'WINTICKET':
                items = []
                for n in range(10):
                    base = feed.url.rstrip('/')
                    items.append({'title': f'ニュース記事 {feed.key} {n}',
                                  'url': base + f'/id{n}', 'date': f'2026-10-{(n%22)+1:02d}',
                                  'body': ['本文がここにあります。']})
                snap['feeds'][feed.key] = {'status': 'ok', 'items': items}
        # Ensure URLs used in the test adhere to each feed's rules.
        for key in list(snap['feeds']):
            if key not in ('winkeirin-topics', 'winauto-topics'):
                del snap['feeds'][key]
        page = mod.render(snap)
        self.assertEqual(page.count('<article id="news-'), 8)
        self.assertEqual(page.count('<li data-new="false"><a href="#news-'), 8)

    def test_oddspark_extracts_actual_notice_instead_of_header(self):
        document = """<html><body><div class="common-header">
          <p>Odds Park</p><p>ログイン</p><p>新規会員登録(無料)</p>
          <p>よくあるご質問</p><p>総合TOP</p></div>
          <div class="content"><h3>お知らせ</h3>
          <h4>【障害】ネット新聞の配信遅延につきまして</h4>
          <time>2026/10/22</time><!--コンテンツ-->
          <div class="notice-text">
          <p>平素よりオッズパークをご利用いただき誠にありがとうございます。</p>
          <p>現在、オッズパークサイトにおいてネット新聞が閲覧できない状態となっております。</p>
          <p>復旧次第、改めてお知らせにてご案内させていただきます。</p>
          <p>お客様にはご不便、ご迷惑をお掛けしますことを深くお詫び申し上げます。</p>
          </div><!--//コンテンツ-->
          <a href="#">ページ先頭へ↑</a><a href="/info/">お知らせTOPへ</a>
          </div><h3>バックナンバー</h3><p>2026年10月</p></body></html>"""
        blocks = mod.extract_oddspark_body(document, '【障害】ネット新聞の配信遅延につきまして')
        self.assertEqual(blocks, [
            '平素よりオッズパークをご利用いただき誠にありがとうございます。',
            '現在、オッズパークサイトにおいてネット新聞が閲覧できない状態となっております。',
            '復旧次第、改めてお知らせにてご案内させていただきます。',
            'お客様にはご不便、ご迷惑をお掛けしますことを深くお詫び申し上げます。'])
        self.assertEqual(mod.extract_oddspark_body(document, '別記事'), [])

    def test_oddspark_old_navigation_cache_is_replaced(self):
        class Client:
            calls = []
            def fetch(self, feed):
                if feed.key != 'odds-info':
                    raise RuntimeError('unavailable')
                return ('<a href="/info/2026/10/post-687.html">'
                        '【障害】ネット新聞の配信遅延につきまして</a>')
            def fetch_article(self, feed, url):
                self.calls.append(url)
                return ('<h4>【障害】ネット新聞の配信遅延につきまして</h4>'
                        '<div><p>平素よりオッズパークをご利用いただき誠にありがとうございます。</p>'
                        '<p>現在ネット新聞が閲覧できない状態です。</p>'
                        '<a>ページ先頭へ↑</a></div>')
        old = {'feeds': {'odds-info': {'status': 'ok', 'items': [
            {'title': '【障害】ネット新聞の配信遅延につきまして',
             'url': 'https://www.oddspark.com/info/2026/10/post-687.html',
             'date': '2026-10-22', 'body': ['Odds Park', 'ログイン']}]}}}
        client = Client()
        updated = mod.update(client, old, datetime(2026, 10, 22, tzinfo=ZoneInfo('Asia/Tokyo')))
        entry = updated['feeds']['odds-info']['items'][0]
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(entry['body_extractor'], 'odds-v3')
        self.assertNotIn('ログイン', entry['body'])
        self.assertIn('現在ネット新聞が閲覧できない状態です。', entry['body'])

    def test_right_column_order_matches_left_site_grouping(self):
        import re
        snap = mod.load_snapshot(None)
        snap['feeds'] = {
            'winkeirin-topics': {'status': 'ok', 'items': [
                {'title': 'WINTICKET古い記事', 'url': 'https://www.winticket.jp/keirin/topics/old123',
                 'date': '2026-10-07', 'body': ['本文A']},
                {'title': 'WINTICKET新しい記事', 'url': 'https://www.winticket.jp/keirin/topics/new123',
                 'date': '2026-10-15', 'body': ['本文B']}]},
            'odds-info': {'status': 'ok', 'items': [
                {'title': 'オッズパーク最新記事', 'url': 'https://www.oddspark.com/info/2026/10/post-687.html',
                 'date': '2026-10-22', 'body': ['本文C']}]},
            'betimo': {'status': 'ok', 'items': [
                {'title': 'Betimoの記事', 'url': 'https://www.betimo.co.jp/news/261019',
                 'date': '2026-10-20', 'body': ['本文D']}]},
        }
        page = mod.render(snap)
        index = page.split('<nav id="news-nav"', 1)[1].split('</nav>', 1)[0]
        left_ids = re.findall(r'<li data-new="(?:true|false)"><a href="#(news-\d+)"', index)
        right_ids = re.findall(r'<article id="(news-\d+)"', page)
        right_titles = re.findall(r'<article id="news-\d+" data-new="false"><h2>(.*?)</h2>', page)
        self.assertEqual(left_ids, right_ids)
        self.assertEqual(right_titles, ['WINTICKET新しい記事', 'WINTICKET古い記事',
                                       'オッズパーク最新記事', 'Betimoの記事'])

    def test_cache_on_failed_feeds(self):
        class Client:
            def fetch_article(self, feed, url):
                return '<article><p>記事の本文を取得できています。今後もサイトを安全にお使いいただけます。</p><p>詳細を確認して更新しました。</p></article>'

            def fetch(self, feed):
                if feed.key == 'betimo':
                    raise RuntimeError('blocked')
                return '<a href="%s">テスト用ニュースが掲載されました</a>' % ({
                    'winkeirin-topics': '/keirin/topics/1234',
                }.get(feed.key, '/not-an-article'))
        prior = {'feeds': {'betimo': {'last_success': '2026-10-20T01:00:00+09:00',
                                    'items': [{'title': 'ニュースが公開されました', 'url': 'https://www.betimo.co.jp/news/261019',
                                               'date': '2026-10-19', 'new': True}]}}}
        snap = mod.update(Client(), prior, datetime(2026, 10, 22, tzinfo=ZoneInfo('Asia/Tokyo')))
        b = snap['feeds']['betimo']
        self.assertEqual(b['status'], 'cached')
        self.assertFalse(b['items'][0]['new'])
        self.assertEqual(b['last_success'], '2026-10-20T01:00:00+09:00')
        self.assertTrue(snap['feeds']['winkeirin-topics']['items'][0]['body'])


if __name__ == '__main__':
    unittest.main()
