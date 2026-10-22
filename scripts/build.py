"""競輪・オート投票サイトの公式お知らせを取得し、本文付きニュースを生成する。

公開HTMLの本文のみを取得する。認証・アクセス制限の回避や非公開API解析は行わない。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import shutil
import subprocess
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

JST = ZoneInfo('Asia/Tokyo')
VERSION = 1
MAX_PER_SITE = 8
MAX_BODY_CHARS = 40000
MAX_IMAGES_PER_ARTICLE = 6
MAX_IMAGE_BYTES = 5_000_000
MEDIA_VERSION = 1
LINK_VERSION = 1
AGENT = 'BettingSiteNews/1.0 (+GitHub Pages public-news-index; respectful robot policy)'
MAX_PER_FEED = 12
MAX_BYTES = 5_000_000
TIMEOUT = 12
DATE_RE = re.compile(r'(?<!\d)(20\d{2})\s*[/\.\-年]\s*(1[0-2]|0?[1-9])\s*[/\.\-月]\s*(3[01]|[12]\d|0?[1-9])\s*日?(?:\s*\([^)]{1,3}\))?')
NOISE = re.compile(r'^(?:NEW|重要|お知らせ|ニュース|記事一覧|一覧|詳細|もっと見る|続きを読む|次へ|前へ|[0-9]+)$', re.I)
TITLE_CLEAN = re.compile(r'\s+')


@dataclass(frozen=True)
class Feed:
    key: str
    site: str
    section: str
    url: str
    path_re: str
    # Allow the alternate official hostname used when the same content is linked.
    hosts: tuple[str, ...] = ()
    limit: int = MAX_PER_FEED


FEEDS = (
    Feed('gamboo', 'Gamboo', 'お知らせ', 'https://gamboo.jp/news/list', r'/news/?$', ('www.gamboo.jp',)),
    Feed('winkeirin-topics', 'WINTICKET', '競輪・お知らせ', 'https://www.winticket.jp/keirin/topics/', r'/keirin/topics/[^/]+/?$'),
    Feed('winkeirin-column', 'WINTICKET', '競輪・コラム', 'https://www.winticket.jp/keirin/column', r'/keirin/column/[^/]+/?$', limit=8),
    Feed('winauto-topics', 'WINTICKET', 'オート・お知らせ', 'https://www.winticket.jp/autorace/topics/', r'/autorace/topics/[^/]+/?$'),
    Feed('winauto-column', 'WINTICKET', 'オート・コラム', 'https://www.winticket.jp/autorace/column', r'/autorace/column/[^/]+/?$', limit=8),
    Feed('odds-info', 'オッズパーク', 'お知らせ', 'https://www.oddspark.com/info/', r'/info/20\d\d/\d{1,2}/[^/]+\.html$'),
    Feed('odds-pickup', 'オッズパーク', 'ピックアップ', 'https://www.oddspark.com/pickup/', r'/pickup/20\d\d/\d{1,2}/[^/]+\.html$'),
    Feed('kdreams', '楽天Kドリームス', 'インフォメーション', 'https://info2.kdreams.jp/', r'/entry/20\d\d/\d{1,2}/\d{1,2}/[^/]+/?$'),
    Feed('chariloto', 'チャリロト', 'ニュース', 'https://www.chariloto.com/articles', r'/articles/\d+/?$'),
    Feed('keirinjp', 'KEIRIN.JP', '競輪トピックス', 'https://keirin.jp/pc/topicslist', r'/(?:pc|sp)/topicsdetail/?$', ('www.keirin.jp',)),
    Feed('autorace-campaign', 'AUTORACE.JP', 'キャンペーン', 'https://autorace.jp/news/campaign/view/', r'/news/campaign/20\d\d/\d{1,2}/\d{1,2}/[^/]+/?$', ('www.autorace.jp',)),
    Feed('autorace-event', 'AUTORACE.JP', 'イベント', 'https://autorace.jp/news/event/view/', r'/news/event/20\d\d/\d{1,2}/\d{1,2}/[^/]+/?$', ('www.autorace.jp',)),
    Feed('autorace-update', 'AUTORACE.JP', 'アップデート', 'https://autorace.jp/news/update/view/', r'/news/update/20\d\d/\d{1,2}/\d{1,2}/[^/]+/?$', ('www.autorace.jp',)),
    Feed('netkeirin', 'netkeirin', 'キャンペーン・お知らせ', 'https://keirin.netkeiba.com/info/', r'/info/info_detail/?$'),
    Feed('dmm', 'DMM競輪', 'お知らせ', 'https://keirin.dmm.com/news', r'/news/[^/]+(?:/[^/]+)*/?$', limit=12),
    Feed('minnano', 'みんなの競輪', 'お知らせ', 'https://bet.minnano-keirin.com/info', r'/info/\d+/?$'),
    Feed('tipstar', 'TIPSTAR', 'お知らせ', 'https://about.tipstar.com/news/', r'/news/[^/]+/?$'),
    Feed('lotoplace', 'Loto Place', 'ニュース', 'https://www.lotoplace.jp/articles', r'/articles/\d+/?$'),
    Feed('atarundesu', '当たるんです', 'お知らせ', 'https://www.atarundesu.com/notification', r'/notification/[^/]+/?$'),
    Feed('pist6', 'PIST6', 'ニュース', 'https://chiba.pist6.com/news', r'/news/[^/]+\.html$'),
    Feed('aokei', 'AOKEIスタジアム', 'ニュース', 'https://www.aokei-stadium.com/articles', r'/articles/\d+/?$'),
    Feed('betimo', 'Betimo', 'ニュースリリース', 'https://www.betimo.co.jp/', r'/news/[^/]+/?$'),
)


def clean(value: str) -> str:
    return TITLE_CLEAN.sub(' ', value.replace('\u3000', ' ')).strip()


def date_from(value: str) -> str:
    m = DATE_RE.search(value)
    if not m:
        return ''
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date().isoformat()
    except ValueError:
        return ''


def safe_url(feed: Feed, href: str) -> str | None:
    if not isinstance(href, str) or href.startswith(('#', 'javascript:', 'data:', 'mailto:')):
        return None
    try:
        p = urllib.parse.urlsplit(urllib.parse.urljoin(feed.url, href))
    except ValueError:
        return None
    base_host = urllib.parse.urlsplit(feed.url).hostname
    if p.scheme != 'https' or p.hostname not in (base_host, *feed.hosts) or p.port not in (None, 443):
        return None
    if not re.fullmatch(feed.path_re, p.path):
        return None
    # KEIRIN.JP/netkeirin use a query parameter as the article identifier.
    query = urllib.parse.parse_qs(p.query)
    if feed.key == 'gamboo' and not re.fullmatch(r'[A-Za-z0-9_-]{2,60}', query.get('tsid', [''])[0]):
        return None
    if feed.key == 'keirinjp' and not re.fullmatch(r'\d+', query.get('nnum', [''])[0]):
        return None
    if feed.key == 'netkeirin' and not re.fullmatch(r'\d+', query.get('id', [''])[0]):
        return None
    if feed.key == 'gamboo':
        identity = {'tsid': query['tsid'][0]}
        if re.fullmatch(r'[A-Za-z0-9_-]{1,30}', query.get('tid', [''])[0]):
            identity = {'tid': query['tid'][0], **identity}
        canonical_query = urllib.parse.urlencode(identity)
    elif feed.key in {'keirinjp', 'netkeirin'}:
        param = 'nnum' if feed.key == 'keirinjp' else 'id'
        canonical_query = urllib.parse.urlencode({param: query[param][0]})
    elif feed.key == 'minnano':
        canonical_query = ''  # page=... is not part of an article identity
    else:
        canonical_query = ''
    path = p.path if feed.key in {'gamboo', 'netkeirin'} else p.path.rstrip('/')
    return urllib.parse.urlunsplit(('https', p.netloc.lower(), path, canonical_query, ''))



def safe_image_url(href: str, base_url: str) -> str | None:
    """Limit embedded media to HTTPS image URLs (no local/private destinations)."""
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        p = urllib.parse.urlsplit(urllib.parse.urljoin(base_url, href.strip()))
        host = p.hostname or ''
        if p.scheme != 'https' or not host or p.port not in (None, 443) or p.username or p.password:
            return None
        if '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        return urllib.parse.urlunsplit(('https', p.netloc.lower(), p.path, p.query, ''))
    except ValueError:
        return None


def safe_article_link(href: str, page_url: str) -> str | None:
    """Only preserve ordinary public HTTPS links."""
    return safe_image_url(href, page_url)


def nearby_date(anchor) -> str:
    # Date in a nested time tag, or the nearest short list/card wrapper.
    for date_node in anchor.select('time[datetime], time'):
        value = date_from(date_node.get('datetime', '') or date_node.get_text(' ', strip=True))
        if value:
            return value
    for node in [anchor, *list(anchor.parents)[:4]]:
        if node.name not in ('a', 'li', 'article', 'div', 'section', 'time', 'p'):
            continue
        text = clean(node.get_text(' ', strip=True))
        if len(text) > 700:
            continue
        found = date_from(text)
        if found:
            return found
    return ''


def extract_articles(feed: Feed, document: str) -> list[dict]:
    soup = BeautifulSoup(document, 'html.parser')
    for tag in soup(['script', 'style', 'template', 'noscript', 'svg']):
        tag.decompose()
    items = []
    seen = set()
    for anchor in soup.select('a[href]'):
        url = safe_url(feed, anchor.get('href', ''))
        if not url or url in seen:
            continue
        # Prefer a card heading to its description and date (e.g. PIST6).
        heading = anchor.select_one('h1, h2, h3, h4, h5, [class*="title"], [class*="Title"]')
        title = clean(heading.get_text(' ', strip=True)) if heading else ''
        if not title:
            title = clean(anchor.get_text(' ', strip=True))
        if not title:
            img = anchor.select_one('img[alt]')
            title = clean(img['alt']) if img else ''
        if len(title) > 300:
            # If the card includes an excerpt, accept only a clearly separated heading.
            continue
        if not title:
            # Some websites separate the link icon and heading within the same card.
            for wrapper in list(anchor.parents)[:2]:
                if wrapper.name in ('li', 'article'):
                    title = clean(wrapper.get_text(' ', strip=True))
                    break
        title = clean(DATE_RE.sub(' ', title))
        title = re.sub(r'^(?:NEW\s+|重要\s+)+', '', title, flags=re.I).strip()
        if (len(title) < 5 or NOISE.fullmatch(title) or len(title) > 300 or
                (feed.key == 'tipstar' and re.fullmatch(
                    r'(?:前のページへ|次のページへ|前へ|次へ|ページトップへ|ページ[0-9]+)', title))):
            continue
        seen.add(url)
        items.append({'title': title, 'url': url, 'date': nearby_date(anchor),
                      'feed': feed.key, 'site': feed.site, 'section': feed.section})
        if len(items) >= feed.limit:
            break
    return items



def extract_embedded_news(feed: Feed, document: str) -> list[dict]:
    """Read explicitly published article objects in the HTML's embedded JSON."""
    soup = BeautifulSoup(document, 'html.parser')
    items: list[dict] = []
    seen: set[str] = set()

    def walk(obj, depth: int = 0) -> None:
        if depth > 24 or len(items) >= feed.limit:
            return
        if isinstance(obj, dict):
            title = clean(str(obj.get('title') or obj.get('headline') or
                              obj.get('newsTitle') or obj.get('name') or ''))
            href = obj.get('url') or obj.get('href') or obj.get('link') or obj.get('path')
            if not href and isinstance(obj.get('slug'), str):
                href = '/news/' + obj['slug']
            url = safe_url(feed, href) if isinstance(href, str) else None
            if url and url not in seen and 6 <= len(title) <= 240:
                seen.add(url)
                published = obj.get('publishedAt') or obj.get('date') or obj.get('createdAt') or ''
                items.append({'title': title, 'url': url, 'date': date_from(str(published)),
                              'feed': feed.key, 'site': feed.site, 'section': feed.section})
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth+1)
        elif isinstance(obj, list):
            for value in obj[:120]:
                walk(value, depth+1)

    for tag in soup.select('script[type="application/json"],script[type="application/ld+json"],script#__NEXT_DATA__,script#__NUXT_DATA__'):
        raw = tag.string or tag.get_text()
        if not raw or len(raw) > MAX_BYTES:
            continue
        try:
            walk(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return items


class WebClient:
    def __init__(self):
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last = 0.0

    def _read(self, url: str, host: str) -> str:
        self.last = time.monotonic()
        req = urllib.request.Request(url, headers={
            'User-Agent': AGENT,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ja-JP,ja;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            p = urllib.parse.urlsplit(response.url)
            if p.scheme != 'https' or p.hostname != host:
                raise RuntimeError('別ドメインへの転送を検出しました')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise RuntimeError('取得サイズが上限を超えました')
            charset = response.headers.get_content_charset() or 'utf-8'
            # Japanese sites sometimes declare Shift_JIS in HTML only.
            if charset.lower() in ('iso-8859-1', 'latin-1'):
                charset = 'utf-8'
            try:
                decoded = raw.decode(charset, 'replace')
            except LookupError:
                decoded = raw.decode('utf-8', 'replace')
            if '\ufffd' in decoded and ('charset=shift_jis' in decoded.lower() or 'charset=windows-31j' in decoded.lower()):
                decoded = raw.decode('cp932', 'replace')
            return decoded

    def _check_robots(self, url: str) -> None:
        p = urllib.parse.urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            robots_url = origin + '/robots.txt'
            try:
                text = self._read(robots_url, p.hostname)
            except urllib.error.HTTPError as err:
                if err.code in (404, 410):
                    text = 'User-agent: *\nAllow: /\n'
                else:
                    raise RuntimeError('robots.txtを確認できません') from err
            except (TimeoutError, OSError, urllib.error.URLError) as err:
                raise RuntimeError('robots.txtを確認できません') from err
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(AGENT, url):
            raise RuntimeError('robots.txtにより取得できません')

    def fetch(self, feed: Feed) -> str:
        target = feed.url
        if feed.key == 'keirinjp':
            # The official mobile page is a public alternate to the PC view;
            # only try it when its own robots rules permit retrieval.
            alternatives = (feed.url, 'https://keirin.jp/sp/topicslist')
            for candidate in alternatives:
                try:
                    self._check_robots(candidate)
                    target = candidate
                    break
                except RuntimeError:
                    continue
            else:
                raise RuntimeError('PC・スマートフォン版ともrobots.txtにより取得できません')
        else:
            self._check_robots(target)
        # Avoid bursts to sites; check for challenge page without bypassing it.
        time.sleep(max(0, 0.5 - (time.monotonic() - self.last)))
        host = urllib.parse.urlsplit(target).hostname
        doc = self._read(target, host)
        if any(marker in doc[:6000].lower() for marker in (
            'verify that you\'re not a robot', 'cf-chl-', 'enable javascript and reload the page',
            'javascript is disabled</h',
        )):
            raise RuntimeError('サイト側のアクセス確認画面が表示されました')
        return doc

    def fetch_article(self, feed: Feed, url: str) -> str:
        # 検出元サイトの許可されたURLだけを取得し、リダイレクトもサイト外へ出さない。
        url = safe_url(feed, url)
        if not url:
            raise RuntimeError('記事URLの検証に失敗しました')
        self._check_robots(url)
        time.sleep(max(0, 0.5 - (time.monotonic() - self.last)))
        document = self._read(url, urllib.parse.urlsplit(url).hostname)
        if any(mark in document[:6000].lower() for mark in (
            'verify that you\'re not a robot', 'cf-chl-',
            'enable javascript and reload the page',
        )):
            raise RuntimeError('サイト側のアクセス確認画面が表示されました')
        return document




    def render_listing(self, feed: Feed) -> str:
        """Use an ordinary headless browser for public JS-rendered listing pages."""
        if feed.key not in ('dmm', 'keirinjp'):
            raise RuntimeError('ブラウザ表示対象外です')
        self._check_robots(feed.url)
        binary = next((shutil.which(name) for name in
                       ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')
                       if shutil.which(name)), None)
        if not binary:
            raise RuntimeError('Chromeが利用できません')
        process = subprocess.run([
            binary, '--headless=new', '--disable-gpu', '--disable-extensions',
            '--disable-dev-shm-usage', '--no-first-run',
            '--virtual-time-budget=7000', '--dump-dom', feed.url
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=24, check=False)
        if process.returncode or not process.stdout or len(process.stdout) > MAX_BYTES:
            raise RuntimeError('Chromeで記事一覧を取得できませんでした')
        output = process.stdout.decode('utf-8', 'replace')
        if 'verify that you' in output[:6000].lower() and 'robot' in output[:6000].lower():
            raise RuntimeError('アクセス確認画面が表示されました')
        return output


    def render_article(self, feed: Feed, url: str) -> str:
        """Render client-side notice text as a last resort (public pages only)."""
        if feed.key != 'minnano' or safe_url(feed, url) != url:
            raise RuntimeError('ブラウザで表示できる記事URLではありません')
        binary = next((shutil.which(name) for name in
                       ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')
                       if shutil.which(name)), None)
        if not binary:
            raise RuntimeError('ヘッドレスブラウザが利用できません')
        args = [binary, '--headless=new', '--disable-gpu', '--disable-extensions',
                '--disable-background-networking', '--disable-dev-shm-usage',
                '--no-first-run', '--no-default-browser-check',
                '--virtual-time-budget=6500', '--dump-dom', url]
        # The sandbox remains enabled; never execute the remote page unsandboxed.
        response = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=22, check=False)
        if response.returncode != 0:
            raise RuntimeError('ヘッドレスブラウザで記事を表示できませんでした')
        raw = response.stdout
        if not raw or len(raw) > MAX_BYTES:
            raise RuntimeError('ブラウザで描画したHTMLが不正です')
        return raw.decode('utf-8', 'replace')


    def save_image(self, image_url: str, directory: Path) -> str:
        safe = safe_image_url(image_url, image_url)
        if not safe:
            raise RuntimeError('画像URLが安全でありません')
        p = urllib.parse.urlsplit(safe)
        # Obey each CDN's robots rules; do not fetch inaccessible media.
        self._check_robots(safe)
        directory.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(safe.encode()).hexdigest()[:24]
        for ext in ('.jpg', '.png', '.webp', '.gif'):
            if (directory / (stem + ext)).is_file():
                return 'images/' + stem + ext
        time.sleep(max(0, 0.5 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        request = urllib.request.Request(safe, headers={
            'User-Agent': AGENT,
            'Accept': 'image/avif,image/webp,image/jpeg,image/png,image/gif;q=0.9,*/*;q=0.5',
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            redirect = urllib.parse.urlsplit(response.url)
            if redirect.scheme != 'https' or redirect.hostname != p.hostname:
                raise RuntimeError('外部画像ドメインへの転送を検出しました')
            kind = response.headers.get_content_type()
            ext = {'image/jpeg': '.jpg', 'image/png': '.png',
                   'image/webp': '.webp', 'image/gif': '.gif'}.get(kind)
            if not ext:
                raise RuntimeError('対応外の画像形式')
            raw = response.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise RuntimeError('画像サイズの上限を超過')
            if len(raw) < 100:
                raise RuntimeError('画像の内容が不正です')
        filename = stem + ext
        (directory / filename).write_bytes(raw)
        return 'images/' + filename


def load_snapshot(path: Path | None) -> dict:
    if not path or not path.is_file():
        return {'format_version': VERSION, 'feeds': {}, 'updated': ''}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('format_version') == VERSION and isinstance(data.get('feeds'), dict):
            return data
    except (ValueError, OSError):
        pass
    return {'format_version': VERSION, 'feeds': {}, 'updated': ''}


ARTICLE_SELECTORS = (
    '[itemprop="articleBody"]', '[data-testid="article-body"]',
    '.article-body', '.articleBody', '.article__body', '.articleBodyWrap',
    '.article-content', '.articleContent', '.article__content',
    '.entry-content', '.entryContent', '.post-content', '.post-body',
    '.news-body', '.news__body', '.news-content', '.newsContent',
    '.news-detail', '.newsDetail', '.topic-body', '.topic-content',
    '.detail-content', '.detailBody', '.contents-body', '.main-content',
    'article', 'main', '[role="main"]', '#article', '#content',
)
NOISE_BLOCK = re.compile(
    r'^(?:シェア|記事をシェアしよう|SNSでシェア|URLをコピー|お知らせ一覧(?:へ|に戻る)?|'
    r'記事一覧(?:へ|に戻る)?|ページの先頭へ|前の記事|次の記事|TOP|戻る|'
    r'関連情報|関連記事|関連ニュース|詳しくはこちら|続きを読む|一覧に戻る)$', re.I)
# Remove entire non-article sections before estimating article content.
DROP_SELECTORS = (
    'script,style,noscript,template,svg,nav,header,footer,aside,form,button,iframe,'
    '.breadcrumb,.breadcrumbs,.pagination,.share,.sns,.social,.related,.related-articles,'
    '.recommend,.recommendations,.ranking,.ad,.advertisement,.sidebar,'
    '[aria-label="パンくずリスト"],[aria-label="breadcrumb"]'
)


def content_blocks(node, headline: str = '') -> list[str]:
    """Extract readable paragraphs, subheads and short list/table rows, not navigation."""
    root = BeautifulSoup(str(node), 'html.parser')
    for garbage in root.select(DROP_SELECTORS):
        garbage.decompose()
    blocks = []
    for elem in root.find_all(['p', 'h2', 'h3', 'h4', 'li', 'blockquote', 'tr']):
        # Skip the same content under a nested block (e.g. a list inside a p).
        if elem.find_parent(['p', 'li', 'tr', 'blockquote']):
            continue
        if elem.name == 'li' and elem.find_parent(['ul', 'ol']) and len(elem.find_all('a')) > 0:
            continue  # link navigation / related stories, not prose
        if elem.name == 'tr':
            value = ' ｜ '.join(clean(c.get_text(' ', strip=True)) for c in elem.find_all(['td', 'th']))
        else:
            value = clean(elem.get_text(' ', strip=True))
        if not value or (value == headline) or NOISE_BLOCK.fullmatch(value):
            continue
        if len(value) > 3500:
            value = value[:3500]
        if value not in blocks[-2:]:
            blocks.append(value)
    if sum(map(len, blocks)) < 45:
        # Many official notices use only <div> and <br> instead of <p>.
        text = root.get_text('\n', strip=True)
        lines = [clean(line) for line in text.splitlines()]
        lines = [line for line in lines if line and line != headline and not NOISE_BLOCK.fullmatch(line)]
        if sum(map(len, lines)) > sum(map(len, blocks)):
            blocks = lines
    # Remove dates and isolated labels at the top of an article when no prose is lost.
    while blocks and (blocks[0] == headline or (len(blocks[0]) <= 20 and (
            date_from(blocks[0]) or blocks[0] in ('お知らせ', 'ニュース', 'アップデート')))):
        blocks.pop(0)
    out, total = [], 0
    for block in blocks:
        if total + len(block) > MAX_BODY_CHARS:
            break
        out.append(block)
        total += len(block)
    return out


def from_jsonld(soup, headline: str) -> list[str]:
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            typ = value.get('@type', '')
            types = typ if isinstance(typ, list) else [typ]
            if any(t in ('Article', 'NewsArticle', 'BlogPosting', 'Report', 'WebPage') for t in types):
                body = value.get('articleBody', '')
                if isinstance(body, str) and body:
                    yield body
            for nested in value.get('@graph', []):
                yield from walk(nested)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(tag.string or tag.get_text())
            for body in walk(parsed):
                blocks = content_blocks(BeautifulSoup(body, 'html.parser'), headline)
                if sum(map(len, blocks)) >= 25:
                    return blocks
        except (ValueError, TypeError):
            continue
    return []


def extract_body(document: str, headline: str = '') -> list[str]:
    """Prefer a narrow article node; never mistake the site's whole link list for text."""
    soup = BeautifulSoup(document, 'html.parser')
    structured = from_jsonld(soup, headline)
    if structured:
        return structured
    for tag in soup.select('script,style,noscript,template'):
        tag.decompose()
    candidates = []
    seen = set()
    for pos, selector in enumerate(ARTICLE_SELECTORS):
        for node in soup.select(selector)[:12]:
            if id(node) in seen:
                continue
            seen.add(id(node))
            blocks = content_blocks(node, headline)
            text_length = sum(map(len, blocks))
            if text_length < (18 if pos < 23 else 30):
                continue
            # Specific article-body selectors take precedence; generic main/body
            # can include lots of unrelated navigation.
            link_text = sum(len(clean(a.get_text(' ', strip=True))) for a in node.select('a'))
            tag_count = len(node.select('a'))
            if tag_count >= 12 and link_text > text_length * .35:
                continue
            priority = 4 if pos < 23 else (2 if selector == 'article' else 0)
            ratio = text_length / max(1, len(clean(node.get_text(' ', strip=True))))
            score = priority * 10000 + min(text_length, 12000) * .6 + ratio * 1000 - link_text * .25
            candidates.append((score, blocks))
    if not candidates:
        # Fallback to a small text-containing parent around the page's H1.
        heading = soup.find('h1')
        if heading:
            parent = heading.parent
            for _ in range(4):
                if not parent:
                    break
                blocks = content_blocks(parent, headline)
                if sum(map(len, blocks)) >= 30 and len(blocks) >= 2:
                    return blocks
                parent = parent.parent
        return []
    blocks = max(candidates, key=lambda x: x[0])[1]
    if sum(map(len, blocks)) < 20:
        return []
    return blocks


def extract_oddspark_body(document: str, headline: str) -> list[str]:
    """Read text after the exact announcement heading, never the site navigation.

    Odds Park embeds its notice in a page with substantial shared header,
    sidebar and campaign text; scoring the full main node is not reliable.
    """
    soup = BeautifulSoup(document, 'html.parser')
    for unwanted in soup.select('script,style,noscript,template,svg'):
        unwanted.decompose()

    def normalized(text: str) -> str:
        return clean(text).translate(str.maketrans({'〖': '【', '〗': '】'}))

    title = normalized(headline)
    heading = next((tag for tag in soup.select('h1,h2,h3,h4,h5,h6')
                    if normalized(tag.get_text(' ', strip=True)) == title), None)
    if heading is None:
        # Do not fall back to a shared page container and show header text.
        return []

    stop = re.compile(r'^(?:ページ先頭へ|ページの先頭へ|お知らせTOPへ|お知らせ一覧へ|'
                      r'バックナンバー|キャンペーン一覧|関連記事|関連ニュース)')
    ignored = {'script', 'style', 'noscript', 'template', 'nav', 'header',
               'footer', 'aside', 'form', 'button', 'iframe'}
    blocks: list[str] = []
    pieces: list[str] = []
    active_block = None

    def flush() -> None:
        if pieces:
            value = clean(''.join(pieces))
            if value and value != title and not NOISE_BLOCK.fullmatch(value):
                if value not in blocks[-2:]:
                    blocks.append(value)
            pieces.clear()

    for node in heading.next_elements:
        if isinstance(node, Tag):
            if node.name == 'br':
                flush()
                active_block = None
            if node.name in ('a', 'h2', 'h3', 'h4', 'h5', 'h6'):
                marker = clean(node.get_text(' ', strip=True))
                if stop.match(marker):
                    break
            continue
        if not isinstance(node, NavigableString) or isinstance(node, Comment) or not str(node).strip():
            continue
        ancestors = [node.parent, *node.parents]
        if any(parent.name in ignored for parent in ancestors if isinstance(parent, Tag)):
            continue
        value = clean(str(node))
        if (not value or normalized(value) == title or
                value in ('コンテンツ', '//コンテンツ') or
                re.fullmatch(r'20\d{2}[/\.\-年]\d{1,2}[/\.\-月]\d{1,2}日?(?:\s*\([^)]{1,3}\))?', value)):
            continue
        if stop.match(value):
            break
        semantic = next((parent for parent in ancestors if isinstance(parent, Tag)
                         and parent.name in ('p', 'li', 'tr', 'h5', 'h6', 'blockquote', 'div')), node.parent)
        if semantic is not active_block:
            flush()
            active_block = semantic
        # Retain whitespace from the original text nodes; combined with the
        # line-break handling above this preserves short labelled sections.
        pieces.append(str(node))
    flush()
    if sum(map(len, blocks)) < 20:
        return []
    result: list[str] = []
    total = 0
    for paragraph in blocks:
        if total + len(paragraph) > MAX_BODY_CHARS:
            break
        result.append(paragraph)
        total += len(paragraph)
    return result



def ordered_article_media(document: str, title: str, paragraphs: list[str],
                          article_url: str, is_oddspark: bool) -> list[dict]:
    """Locate article pictures, interleave them with established extracted paragraphs.

    Text remains sourced from the existing text-only extractors, so a shared
    header image or caption cannot alter a previously correct notice.
    """
    text_blocks = [{'type': 'text', 'text': p} for p in paragraphs if isinstance(p, str) and p.strip()]
    soup = BeautifulSoup(document, 'html.parser')
    for trash in soup.select('script,style,noscript,template,nav,header,footer,aside,form,iframe'):
        trash.decompose()

    root = None
    odds_heading = None
    if is_oddspark:
        odds_heading = next((h for h in soup.select('h1,h2,h3,h4,h5,h6')
                             if clean(h.get_text(' ', strip=True)) == clean(title)), None)
        if odds_heading is None:
            return text_blocks
        root = odds_heading.parent
    else:
        sample = next((p[:18] for p in paragraphs if isinstance(p, str) and len(p) >= 18), '')
        # Prefer narrow dedicated article containers, not the site's homepage.
        for selector in ARTICLE_SELECTORS:
            if selector in ('main', '[role="main"]', '#content'):
                continue
            candidates = [node for node in soup.select(selector)[:12]
                          if sample and sample in clean(node.get_text(' ', strip=True))]
            if candidates:
                root = min(candidates, key=lambda tag: len(tag.get_text(' ', strip=True)))
                break
        if root is None:
            for selector in ('article', 'main'):
                nodes = [node for node in soup.select(selector)[:8]
                         if sample and sample in clean(node.get_text(' ', strip=True))]
                if nodes:
                    root = min(nodes, key=lambda tag: len(tag.get_text(' ', strip=True)))
                    break
    if root is None:
        return text_blocks

    positions = {id(node): i for i, node in enumerate(soup.descendants)}
    text_matches = []
    last_position = -1
    # Map preserved paragraph text back to DOM positions without changing it.
    candidates = list(root.select('p,li,blockquote,h2,h3,h4,h5,h6,div'))
    for block in text_blocks:
        target = clean(block['text'])
        matching = []
        for tag in candidates:
            position = positions.get(id(tag), -1)
            if position <= last_position:
                continue
            content = clean(tag.get_text(' ', strip=True))
            compact = clean(tag.get_text('', strip=True))
            if content and (content == target or compact == target or
                            (len(target) >= 18 and target[:18] in compact and len(compact) < len(target) * 2)):
                matching.append((len(content), position, tag))
        if matching:
            _, match_pos, text_tag = min(matching, key=lambda x: (x[0], x[1]))
            source_links = []
            for anchor in text_tag.select('a[href]'):
                label = clean(anchor.get_text(' ', strip=True))
                href = safe_article_link(anchor.get('href', ''), article_url)
                if href and label and label in block['text'] and not any(
                        existing['text'] == label for existing in source_links):
                    source_links.append({'text': label, 'href': href})
            if source_links:
                block['links'] = source_links[:12]
            last_position = match_pos
        else:
            match_pos = last_position + 0.01
            last_position = match_pos
        text_matches.append(match_pos)

    pictures = []
    stop = re.compile(r'^(?:ページ先頭へ|ページの先頭へ|お知らせTOPへ|お知らせ一覧へ|'
                      r'バックナンバー|関連記事|関連ニュース)')
    if is_oddspark:
        # Odds Park mixes body and global content within the same wrapper.
        # Consider only images following the precise article title until its
        # first "back to top"/article-index link.
        picture_nodes = []
        for node in odds_heading.next_elements:
            if isinstance(node, Tag) and node.name in ('a', 'h2', 'h3', 'h4', 'h5', 'h6'):
                if stop.match(clean(node.get_text(' ', strip=True))):
                    break
            if isinstance(node, Tag) and node.name == 'img':
                picture_nodes.append(node)
    else:
        picture_nodes = root.select('img')

    seen_urls = set()
    for image in picture_nodes:
        if len(pictures) >= MAX_IMAGES_PER_ARTICLE:
            break
        if image.find_parent(['header', 'nav', 'aside', 'footer']):
            continue
        if image.find_parent(class_=re.compile(r'(^|[-_ ])(?:logo|icon|avatar|advert|related|share)(?:[-_ ]|$)', re.I)):
            continue
        try:
            width, height = int(image.get('width', '0')), int(image.get('height', '0'))
        except (ValueError, TypeError):
            width = height = 0
        if 0 < width <= 40 and 0 < height <= 40:
            continue
        srcset = image.get('data-srcset') or image.get('srcset') or ''
        srcset_url = srcset.split(',')[-1].strip().split(' ')[0] if srcset else ''
        raw = (image.get('data-src') or image.get('data-original') or
               image.get('data-lazy-src') or image.get('src') or srcset_url or '')
        if isinstance(raw, str) and raw.startswith('data:') and srcset_url:
            raw = srcset_url
        url = safe_image_url(raw, article_url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        figure = image.find_parent('figure')
        caption = clean(figure.find('figcaption').get_text(' ', strip=True))[:300] if figure and figure.find('figcaption') else ''
        pictures.append((positions.get(id(image), 10**10),
                         {'type': 'image', 'url': url, 'caption': caption}))

    if not pictures:
        return text_blocks
    merged = []
    for n, block in enumerate(text_blocks):
        merged.append((text_matches[n] if n < len(text_matches) else n, 1, block))
    merged.extend((pos, 0, block) for pos, block in pictures)
    merged.sort(key=lambda x: (x[0], x[1]))
    return [block for _, _, block in merged]



def extract_notice_body(document: str, title: str, site: str) -> list[str]:
    """Extract notices anchored to the article heading, never sitewide navigation."""
    soup = BeautifulSoup(document, 'html.parser')
    structured = from_jsonld(soup, title)
    if structured and len(''.join(structured)) >= 50:
        return structured
    for tag in soup.select('script,style,noscript,template'):
        tag.decompose()

    normalized = clean(title).translate(str.maketrans({'〖': '【', '〗': '】'}))
    if site == 'netkeirin':
        normalized = re.sub(r'^(?:お知らせ|メンテナンス|キャンペーン|イベント)\s+', '', normalized)
    else:
        normalized = re.sub(r'^\d{1,2}:\d{2}\s+', '', normalized)
    allowed_headings = 'h1,h2,h3' if site == 'netkeirin' else 'h1,h2,h3,h4'
    heading = next((tag for tag in soup.select(allowed_headings)
                    if clean(tag.get_text(' ', strip=True)).translate(
                        str.maketrans({'〖': '【', '〗': '】'})) == normalized), None)
    if heading is None:
        return []
    ignore = {'nav', 'header', 'footer', 'aside', 'script', 'style', 'noscript', 'button'}
    endings = ('このニュースをポストする', 'このニュースをシェアする',
               '競輪 キャンペーン・お知らせ一覧', 'お知らせを探す', 'ご意見・ご要望',
               'お知らせ一覧', '戻る', '運営会社', '利用規約',
               '車券の購入は20歳')
    blocks: list[str] = []
    parts: list[str] = []
    active = None

    def flush():
        if parts:
            line = clean(''.join(parts))
            if (line and line != normalized and line not in blocks[-2:]
                    and not NOISE_BLOCK.fullmatch(line)):
                blocks.append(line)
            parts.clear()

    # Extract only text after the confirmed article title. Stop at the
    # site's "share" links, related index or footer.
    for node in heading.next_elements:
        if isinstance(node, Tag):
            if node.name == 'br':
                flush()
                active = None
            if node.name in ('a', 'h1', 'h2', 'h3', 'h4'):
                label = clean(node.get_text(' ', strip=True))
                if label.startswith(endings):
                    break
            continue
        if not isinstance(node, NavigableString) or isinstance(node, Comment):
            continue
        ancestors = [node.parent, *node.parents]
        if any(getattr(parent, 'name', None) in ignore for parent in ancestors):
            continue
        value = clean(str(node))
        if not value or value == normalized or (date_from(value) and len(value) <= 28):
            continue
        if value.startswith(endings):
            break
        node_group = next((parent for parent in ancestors if isinstance(parent, Tag)
                           and parent.name in ('p', 'li', 'blockquote', 'h4', 'h5', 'h6', 'div')), node.parent)
        if node_group is not active:
            flush()
            active = node_group
        parts.append(str(node))
    flush()
    # A date or heading alone is not a readable article.
    return blocks if sum(map(len, blocks)) >= 40 else []


def extract_minnano_json_body(document: str, title: str) -> list[str]:
    """Use publicly embedded page-state article HTML when SSR omits the body."""
    soup = BeautifulSoup(document, 'html.parser')
    normalized = re.sub(r'^\d{1,2}:\d{2}\s+', '', clean(title))
    texts: list[str] = []
    for tag in soup.select('script[type="application/json"], script#__NEXT_DATA__, script#__NUXT_DATA__'):
        raw = tag.string or tag.get_text()
        if not raw or len(raw) > 3_000_000:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        def walk(obj, depth=0):
            if depth > 18:
                return
            if isinstance(obj, dict):
                # Only consider records that also contain this announcement title.
                if normalized in clean(str(obj))[:20000]:
                    for key in ('content', 'body', 'description', 'html', 'bodyHtml', 'text'):
                        value = obj.get(key)
                        if isinstance(value, str) and len(value) >= 55:
                            texts.append(value)
                for value in obj.values():
                    if isinstance(value, (dict, list)):
                        walk(value, depth + 1)
            elif isinstance(obj, list):
                for value in obj[:80]:
                    if isinstance(value, (dict, list)):
                        walk(value, depth + 1)
        walk(data)
    if not texts:
        return []
    candidate = max(texts, key=len)
    body = BeautifulSoup(candidate, 'html.parser')
    for br in body.find_all('br'):
        br.replace_with('\n')
    paragraphs = [clean(t) for t in body.get_text('\n', strip=True).splitlines()]
    return [p for p in paragraphs if p and p != normalized][:160]


def news_identity(feed: Feed, item: dict) -> str:
    """Treat identical WINTICKET announcements across racing types as one."""
    url = item.get('url', '')
    if feed.site == 'WINTICKET' and '/topics/' in url:
        title = clean(item.get('title', '')).casefold()
        return f'winticket-notice:{title}:{item.get("date", "")}'
    return url


def update(client: WebClient, old: dict, now: datetime) -> dict:
    results = {}
    # Separate listing collection from detail fetching so sites with multiple
    # feeds (e.g. WINTICKET) are still limited to eight articles total.
    for feed in FEEDS:
        prior = old.get('feeds', {}).get(feed.key, {})
        previous_items = prior.get('items') if isinstance(prior.get('items'), list) else []
        try:
            document = client.fetch(feed)
            items = extract_articles(feed, document)
            if not items and feed.key == 'dmm':
                items = extract_embedded_news(feed, document)
            if not items and feed.key in ('dmm', 'keirinjp') and hasattr(client, 'render_listing'):
                try:
                    rendered = client.render_listing(feed)
                    items = extract_articles(feed, rendered)
                    if not items and feed.key == 'dmm':
                        items = extract_embedded_news(feed, rendered)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    print(f'INFO ブラウザ一覧未取得 {feed.site}: {clean(str(error))[:100]}')
            if not items:
                raise RuntimeError('公開HTMLに記事リンクが見つかりません（動的表示・構造変更の可能性）')
            seen_old = {i['url'] for i in previous_items if 'url' in i}
            old_by_url = {i['url']: i for i in previous_items if 'url' in i}
            for item in items:
                item['new'] = bool(previous_items and item['url'] not in seen_old)
                # Preserve successfully fetched bodies across daily updates.
                if item['url'] in old_by_url:
                    previous = old_by_url[item['url']]
                    # Discard old Odds Park bodies polluted with shared headers.
                    old_body_is_valid = (
                        (not feed.key.startswith('odds-') or previous.get('body_extractor') == 'odds-v4')
                        and (feed.key not in ('netkeirin', 'minnano')
                             or previous.get('body_extractor') == 'notice-v1'))
                    if old_body_is_valid:
                        item['body'] = previous.get('body', [])
                        item['note'] = previous.get('note', '')
                        if feed.key.startswith('odds-'):
                            item['body_extractor'] = 'odds-v4'
                        elif feed.key in ('netkeirin', 'minnano'):
                            item['body_extractor'] = 'notice-v1'
                    if previous.get('media_version') == MEDIA_VERSION:
                        item['media_version'] = MEDIA_VERSION
                        item['blocks'] = previous.get('blocks', [])
                        item['link_version'] = previous.get('link_version', 0)
            results[feed.key] = {'status': 'ok', 'last_success': now.isoformat(), 'items': items}
            print(f'INFO {feed.site} / {feed.section}: {len(items)}件')
        except Exception as exc:
            reason = clean(str(exc))[:130] or type(exc).__name__
            preserved = [{**item, 'new': False} for item in previous_items]
            results[feed.key] = {'status': 'cached' if preserved else 'unavailable',
                                 'last_success': prior.get('last_success', ''),
                                 'items': preserved, 'message': reason}
            print(f'INFO {feed.site} / {feed.section}: 取得不可（{reason}）、前回{len(preserved)}件を保持')

    # Date order by site, newest first, then gather the eight that are displayed.
    selected: set[str] = set()
    per_site: dict[str, list[tuple[Feed, dict]]] = {}
    for feed in FEEDS:
        for order, item in enumerate(results[feed.key]['items']):
            per_site.setdefault(feed.site, []).append((feed, item))
    for site, candidates in per_site.items():
        candidates.sort(key=lambda x: (x[1].get('date', ''), -x[0].limit, -FEEDS.index(x[0])), reverse=True)
        seen_links: set[str] = set()
        for feed, item in candidates:
            identity = news_identity(feed, item)
            if identity in seen_links:
                continue
            seen_links.add(identity)
            if len(seen_links) > MAX_PER_SITE:
                break
            selected.add(identity)
    for feed in FEEDS:
        record = results[feed.key]
        # Keep only the selected articles in the snapshot; prevents unnecessary
        # requests and allows next run to re-use bodies while preserving NEW flags.
        record['items'] = [i for i in record['items'] if news_identity(feed, i) in selected]
        if record['status'] != 'ok':
            continue
        for item in record['items']:
            if (item.get('body') and item.get('media_version') == MEDIA_VERSION
                    and item.get('link_version') == LINK_VERSION):
                continue
            try:
                document = client.fetch_article(feed, item['url'])
                if not item.get('body'):
                    if feed.key.startswith('odds-'):
                        paragraphs = extract_oddspark_body(document, item['title'])
                    elif feed.key in ('netkeirin', 'minnano'):
                        paragraphs = extract_notice_body(document, item['title'], feed.key)
                        if not paragraphs and feed.key == 'minnano':
                            paragraphs = extract_minnano_json_body(document, item['title'])
                        if not paragraphs and feed.key == 'minnano':
                            try:
                                rendered = client.render_article(feed, item['url'])
                                paragraphs = (extract_notice_body(rendered, item['title'], 'minnano')
                                              or extract_minnano_json_body(rendered, item['title']))
                                if paragraphs:
                                    document = rendered
                            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                                print(f'INFO ブラウザ本文未取得 みんなの競輪: {clean(str(error))[:100]}')
                    else:
                        paragraphs = extract_body(document, item['title'])
                    if not paragraphs:
                        raise RuntimeError('本文の領域が見つかりませんでした')
                    item['body'] = paragraphs
                    item['note'] = ''
                    if feed.key.startswith('odds-'):
                        item['body_extractor'] = 'odds-v4'
                    elif feed.key in ('netkeirin', 'minnano'):
                        item['body_extractor'] = 'notice-v1'
                blocks = ordered_article_media(document, item['title'], item['body'], item['url'],
                                               feed.key.startswith('odds-'))
                complete_blocks = []
                for block in blocks:
                    if block['type'] != 'image':
                        complete_blocks.append(block)
                        continue
                    try:
                        location = client.save_image(block['url'], Path('data/images'))
                        complete_blocks.append({'type': 'image', 'src': location,
                                                'caption': block.get('caption', '')})
                    except (OSError, RuntimeError, urllib.error.URLError, ValueError) as error:
                        print(f'INFO 画像未取得 {feed.site}: {type(error).__name__}: {clean(str(error))[:100]}')
                item['blocks'] = complete_blocks
                item['media_version'] = MEDIA_VERSION
                item['link_version'] = LINK_VERSION
            except Exception as exc:
                if not item.get('body'):
                    item['body'] = []
                    item['note'] = '本文を取得できませんでした。出典元の記事をご確認ください。'
                print(f'INFO 本文未取得 {feed.site}: {item["url"]} ({clean(str(exc))[:100]})')
    return {'format_version': VERSION, 'updated': now.isoformat(), 'feeds': results}


def e(value: str) -> str:
    return html.escape(str(value), quote=True)



def linked_text(value: str, source_links: list[dict] | None = None) -> str:
    """Render only validated original anchors and URL literals; escape other text."""
    found = []
    for link in source_links or []:
        if not isinstance(link, dict):
            continue
        label, url = link.get('text', ''), safe_article_link(link.get('href', ''), 'https://example.com/')
        if not label or not url or label not in value:
            continue
        start = 0
        while True:
            pos = value.find(label, start)
            if pos < 0:
                break
            found.append((pos, pos + len(label), url))
            start = pos + len(label)
    # Auto-link bare HTTPS URLs that occur in a paragraph.
    for match in re.finditer(r"""https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+""", value):
        candidate = match.group().rstrip('.,;:)]}。）、')
        url = safe_article_link(candidate, 'https://example.com/')
        if url:
            found.append((match.start(), match.start() + len(candidate), url))
    found.sort(key=lambda span: (span[0], -(span[1]-span[0])))
    parts, end = [], 0
    for start, finish, url in found:
        if start < end:
            continue
        parts.append(e(value[end:start]))
        parts.append(f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer nofollow">{e(value[start:finish])}</a>')
        end = finish
    parts.append(e(value[end:]))
    return ''.join(parts)


def render(snapshot: dict) -> str:
    updated = snapshot.get('updated', '')
    if updated:
        try:
            when = datetime.fromisoformat(updated).astimezone(JST).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            when = ''
    else:
        when = ''
    feeds = snapshot.get('feeds', {})
    all_items, statuses = [], []
    success = 0
    for feed in FEEDS:
        record = feeds.get(feed.key, {})
        status = record.get('status', 'unavailable')
        if status == 'ok':
            success += 1
        label = {'ok': '取得済み', 'cached': '前回分を表示', 'unavailable': '取得不可'}.get(status, '未取得')
        statuses.append(f'<li><a href="{e(feed.url)}" target="_blank" rel="noopener noreferrer nofollow">{e(feed.site)} / {e(feed.section)}</a><span class="source-state">{label}</span></li>')
        for rank, item in enumerate(record.get('items', [])):
            url = safe_url(feed, item.get('url', ''))
            if not url or not item.get('title'):
                continue
            all_items.append({**item, 'url': url, 'feed': feed.key, 'site': feed.site,
                              'section': feed.section, 'stale': status != 'ok', '_rank': rank})
    unique = {}
    feeds_by_key = {feed.key: feed for feed in FEEDS}
    for item in all_items:
        identity = news_identity(feeds_by_key[item['feed']], item)
        unique.setdefault(identity, item)
    # Match the right-hand article order to the left-hand site-grouped index.
    # Preserve newest-first ordering within each site and the eight-item cap.
    site_order = list(dict.fromkeys(feed.site for feed in FEEDS))
    items_by_site: dict[str, list[dict]] = {site: [] for site in site_order}
    chronological = sorted(unique.values(), key=lambda item: (item.get('date', ''), -item['_rank']), reverse=True)
    for item in chronological:
        site_items = items_by_site[item['site']]
        if len(site_items) < MAX_PER_SITE:
            site_items.append(item)
    all_items = [item for site in site_order for item in items_by_site[site]]
    groups: dict[str, list[str]] = {site: [] for site in site_order}
    articles = []
    for item in all_items:
        ident = f'news-{len(articles) + 1}'
        new = 'true' if item.get('new') and not item.get('stale') else 'false'
        title, site, section = e(item['title']), e(item['site']), e(item['section'])
        date = e(item.get('date', ''))
        stamp = f'<time datetime="{date}">{date}</time>' if date else '日付未取得'
        stale = '<span class="stale-label">前回取得分</span>' if item.get('stale') else ''
        new_icon = '<span class="new-label">NEW</span>' if new == 'true' else ''
        groups[item['site']].append(f'<li data-new="{new}"><a href="#{ident}" title="{title}">{title}</a></li>')
        # Article title is text; the attribution directly underneath is clickable.
        attribution = f'{site} <span class="separator">/</span> {section} <span class="separator">/</span> {stamp}'
        body = item.get('body', [])
        ordered = item.get('blocks') if item.get('media_version') == MEDIA_VERSION else None
        if not isinstance(ordered, list) or not ordered:
            ordered = [{'type': 'text', 'text': p} for p in body if isinstance(p, str)] if isinstance(body, list) else []
        html_blocks = []
        for block in ordered:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'image':
                asset = block.get('src', '')
                # Only locally saved image assets may appear in generated HTML.
                if not re.fullmatch(r'images/[a-f0-9]{24}\.(?:jpg|png|webp|gif)', asset):
                    continue
                caption = f'<figcaption>{e(block.get("caption", ""))}</figcaption>' if block.get('caption') else ''
                html_blocks.append(f'<figure><img src="{e(asset)}" alt="記事に掲載された画像" loading="lazy" decoding="async">{caption}</figure>')
            elif block.get('type') == 'text' and isinstance(block.get('text'), str) and block['text'].strip():
                html_blocks.append(f'<p>{linked_text(block["text"], block.get("links"))}</p>')
        paragraphs = ''.join(html_blocks)
        note = e(item.get('note') or '本文を取得できませんでした。出典元の記事をご確認ください。')
        content = f'<div class="article-body">{paragraphs}</div>' if paragraphs else f'<p class="notice">{note}</p>'
        articles.append(f'<article id="{ident}" data-new="{new}">'
                        f'<h2>{title}</h2>'
                        f'<p class="meta"><a href="{e(item["url"])}" target="_blank" rel="noopener noreferrer nofollow">{attribution}</a> {new_icon} {stale}</p>'
                        f'{content}</article>')
    nav = ''.join(f'<section class="category" data-site="{e(site)}"><h2>{e(site)}</h2><ul>{"".join(links)}</ul></section>'
                  for site, links in groups.items() if links)
    status_html = ''.join(statuses)
    empty = '<p class="empty">現在、表示できるニュースがありません。各公式サイトへのリンクや取得状況をご確認ください。</p>'
    articles_html = ''.join(articles) if articles else empty
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="referrer" content="no-referrer">
<link rel="icon" href="favicon.ico" sizes="any"><link rel="apple-touch-icon" href="apple-touch-icon.png" sizes="180x180">
<title>競輪オート投票ニュース</title>
<style>
*{{box-sizing:border-box}}html{{scroll-behavior:auto}}body{{margin:0;background:#fff;color:#202020;font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;font-size:17px;line-height:1.85;overflow-wrap:anywhere}}
main{{max-width:1440px;margin:auto;padding:0 24px 56px}}header{{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #bbb;padding:4px 0}}.header-row{{display:flex;align-items:center;gap:10px 18px;min-height:34px}}
h1{{font-size:23px;line-height:1.3;margin:0;flex:none}}h1 a{{color:inherit;text-decoration:none}}h2{{font-size:20px;line-height:1.55;margin:0 0 8px}}.feed article h2{{color:#14532d}}a{{color:#174c86;text-underline-offset:3px}}a:focus-visible,button:focus-visible,select:focus-visible{{outline:2px solid #14532d;outline-offset:3px}}
.update-controls{{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;line-height:1.25}}.new-filter{{border:0;background:transparent;color:#555;font:inherit;font-size:14px;padding:2px 0;cursor:pointer}}.updated{{font-size:14px;color:#555}}
[hidden]{{display:none!important}}.layout{{display:grid;grid-template-columns:minmax(230px,320px) minmax(0,1fr);gap:36px;align-items:start}}
nav{{font-size:14px;position:sticky;top:calc(var(--header-height,42px) + 8px);max-height:calc(100dvh - var(--header-height,42px) - 16px);overflow:auto;padding-top:12px}}
nav h2{{font-size:14px;line-height:1.4;margin:0 0 4px}}.category{{margin:0 0 18px}}.category ul{{list-style:none;margin:0;padding:0}}.category li{{padding:2px 0;min-width:0}}.category li a{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}.source-status{{margin-top:22px;border-top:1px solid #ddd;padding-top:12px}}.source-status summary{{font-weight:600;cursor:pointer}}.source-status ul{{list-style:none;padding:0;margin:8px 0 0}}.source-status li{{margin-bottom:7px;line-height:1.4}}.source-state{{display:block;color:#777;font-size:12px}}.source-summary{{font-size:13px;color:#666;margin:6px 0 0}}
article{{border-top:1px solid #bbb;padding:24px 0;scroll-margin-top:calc(var(--header-height,42px) + 12px)}}.feed article:first-child,.feed article.visible-first{{border-top:0;padding-top:12px}}
.meta{{font-size:14px;color:#555;margin:4px 0 8px}}.meta a{{color:inherit}}.separator{{color:#aaa}}.new-label{{font-weight:700;color:#b42318;margin-left:6px}}.stale-label{{color:#777;border:1px solid #aaa;border-radius:3px;font-size:12px;padding:0 4px;margin-left:7px}}.article-body p{{margin:0 0 16px;white-space:pre-wrap}}figure{{margin:0 0 18px}}figure img{{display:block;width:auto;max-width:300px;height:auto;max-height:300px;object-fit:contain}}figcaption{{font-size:14px;color:#555;line-height:1.55;margin-top:5px}}.notice{{font-size:14px;color:#666}}.empty{{margin:22px 0;color:#555}}.menu-toggle,.pull-refresh{{display:none}}
@media(max-width:700px){{body{{line-height:1.75}}main{{padding:0 16px 36px}}header{{padding:1px 0;min-height:42px}}.header-row{{gap:6px 10px}}h1{{font-size:20px}}.new-filter{{font-size:14px;padding:0}}.layout{{display:block}}.menu-toggle{{display:inline-flex;align-items:center;justify-content:center;flex:none;order:3;margin-left:auto;width:32px;height:34px;border:0;background:transparent;color:inherit;padding:4px}}.hamburger{{display:flex;flex-direction:column;gap:4px}}.hamburger span{{display:block;width:20px;height:2px;background:currentColor}}.layout nav{{display:none;position:fixed;top:var(--header-height,42px);left:0;right:0;z-index:19;max-height:calc(100dvh - var(--header-height,42px));overflow:auto;padding:12px 16px;background:#fff;border-bottom:1px solid #bbb;box-shadow:0 5px 10px #0002}}body.menu-open .layout nav{{display:block}}.pull-refresh{{display:block;position:fixed;top:0;left:50%;z-index:25;padding:3px 14px;background:#14532d;color:#fff;border-radius:0 0 12px 12px;font-size:14px;pointer-events:none;transform:translate(-50%,-110%);transition:transform .15s}}.pull-refresh.active{{transform:translate(-50%,0)}}article{{padding:18px 0}}h2{{font-size:18px}}}}
@media print{{header{{position:static}}.menu-toggle,nav,.pull-refresh{{display:none!important}}main{{max-width:none;padding:0}}.layout{{display:block}}}}
</style></head><body><div class="pull-refresh" role="status" aria-live="polite">下に引いて更新</div><main id="top"><header><div class="header-row">
<button class="menu-toggle" type="button" aria-label="タイトル一覧を開く" aria-controls="news-nav" aria-expanded="false"><span class="hamburger" aria-hidden="true"><span></span><span></span><span></span></span></button>
<h1><a href="#top">競輪オート投票ニュース</a></h1><div class="update-controls"><time class="updated">{('更新 ' + e(when)) if when else '未更新'}</time><button class="new-filter" type="button" aria-pressed="false">すべて表示中</button></div></div></header>
<div class="layout"><nav id="news-nav" aria-label="タイトル一覧">{nav}<details class="source-status"><summary>取得元・取得状況（{success}/{len(FEEDS)}系統）</summary><p class="source-summary">取得できないサイトは前回取得分を表示する場合があります。</p><ul>{status_html}</ul></details></nav><div class="feed">
{articles_html}
</div></div></main><script>
const filterButton=document.querySelector('.new-filter');
function applyFilters(){{
 const onlyNew=filterButton.getAttribute('aria-pressed')==='true';
 document.querySelectorAll('.feed article').forEach(node=>{{node.hidden=onlyNew&&node.dataset.new!=='true';node.classList.remove('visible-first')}});
 document.querySelector('.feed article:not([hidden])')?.classList.add('visible-first');
 document.querySelectorAll('nav .category').forEach(category=>{{category.querySelectorAll('li').forEach(li=>{{li.hidden=onlyNew&&li.dataset.new!=='true'}});category.hidden=!category.querySelector('li:not([hidden])')}});
}}
filterButton.addEventListener('click',()=>{{const active=filterButton.getAttribute('aria-pressed')!=='true';filterButton.setAttribute('aria-pressed',String(active));filterButton.textContent=active?'NEWのみ表示中':'すべて表示中';applyFilters()}});applyFilters();
const header=document.querySelector('header'),menuButton=document.querySelector('.menu-toggle'),menu=document.getElementById('news-nav');
const updateHeaderHeight=()=>document.documentElement.style.setProperty('--header-height',`${{header.getBoundingClientRect().height}}px`);updateHeaderHeight();if(window.ResizeObserver)new ResizeObserver(updateHeaderHeight).observe(header);
function setMenu(open){{document.body.classList.toggle('menu-open',open);menuButton.setAttribute('aria-expanded',String(open));menuButton.setAttribute('aria-label',open?'タイトル一覧を閉じる':'タイトル一覧を開く')}}
menuButton.addEventListener('click',()=>setMenu(menuButton.getAttribute('aria-expanded')!=='true'));menu.addEventListener('click',event=>{{if(event.target.closest('a[href^="#"]'))setMenu(false)}});document.addEventListener('keydown',event=>{{if(event.key==='Escape')setMenu(false)}});
document.querySelector('header h1 a').addEventListener('click',()=>setMenu(false));
const indicator=document.querySelector('.pull-refresh');let startY=null,startX=0,distance=0;
document.addEventListener('touchstart',event=>{{startY=event.touches.length===1&&matchMedia('(max-width:700px)').matches&&window.scrollY<=0&&!document.body.classList.contains('menu-open')?event.touches[0].clientY:null;startX=event.touches[0]?.clientX||0;distance=0}},{{passive:true}});
document.addEventListener('touchmove',event=>{{if(startY===null||event.touches.length!==1)return;const dy=event.touches[0].clientY-startY;if(dy<=10||Math.abs(event.touches[0].clientX-startX)>dy||window.scrollY>0)return;event.preventDefault();distance=Math.min(dy,120);indicator.classList.add('active');indicator.textContent=distance>=90?'離して更新':'下に引いて更新'}},{{passive:false}});
function finishPull(){{if(distance>=90){{indicator.textContent='更新中…';location.reload()}}else{{indicator.classList.remove('active')}}startY=null;distance=0}}
document.addEventListener('touchend',finishPull,{{passive:true}});document.addEventListener('touchcancel',()=>{{indicator.classList.remove('active');startY=null;distance=0}},{{passive:true}});
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('site'))
    parser.add_argument('--snapshot', type=Path, default=Path('data/news.json'))
    parser.add_argument('--from-snapshot', action='store_true')
    args = parser.parse_args()
    prior = load_snapshot(args.snapshot)
    now = datetime.now(JST)
    snapshot = prior if args.from_snapshot else update(WebClient(), prior, now)
    args.output.mkdir(parents=True, exist_ok=True)
    # Include only currently referenced images in Pages and prune stale cache.
    image_cache = Path('data/images')
    referenced = {
        block.get('src', '').split('/')[-1]
        for feed_data in snapshot.get('feeds', {}).values()
        for item in feed_data.get('items', [])
        for block in item.get('blocks', [])
        if isinstance(block, dict) and block.get('type') == 'image'
        and re.fullmatch(r'images/[a-f0-9]{24}\.(?:jpg|png|webp|gif)', block.get('src', ''))
    }
    if image_cache.is_dir():
        output_images = args.output / 'images'
        for file in image_cache.iterdir():
            if not file.is_file():
                continue
            if file.name not in referenced:
                if not args.from_snapshot:
                    file.unlink()
                continue
            output_images.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, output_images / file.name)
    (args.output / 'index.html').write_text(render(snapshot), encoding='utf-8')
    (args.output / 'robots.txt').write_text('User-agent: *\nDisallow:\n', encoding='utf-8')
    (args.output / '.nojekyll').touch()
    assets = Path(__file__).resolve().parents[1]
    for icon in ('favicon.ico', 'apple-touch-icon.png'):
        if (assets / icon).is_file():
            (args.output / icon).write_bytes((assets / icon).read_bytes())
    if not args.from_snapshot:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    total = sum(len(feed.get('items', [])) for feed in snapshot.get('feeds', {}).values())
    print(f'INFO HTML生成完了: {args.output}/index.html, フィード合計{total}件', flush=True)


if __name__ == '__main__':
    main()
