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

    def test_cache_on_failed_feeds(self):
        class Client:
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


if __name__ == '__main__':
    unittest.main()
