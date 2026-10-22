"""競輪・オート投票サイトの公式お知らせ一覧を静的なニュース一覧にする。

HTMLに公開されている記事リンクだけを扱う。認証・回避・非公開APIの解析は行わない。
"""
from __future__ import annotations

import argparse
import html
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

from bs4 import BeautifulSoup

JST = ZoneInfo('Asia/Tokyo')
VERSION = 1
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
        if len(title) < 5 or NOISE.fullmatch(title) or len(title) > 300:
            continue
        seen.add(url)
        items.append({'title': title, 'url': url, 'date': nearby_date(anchor),
                      'feed': feed.key, 'site': feed.site, 'section': feed.section})
        if len(items) >= feed.limit:
            break
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
        self._check_robots(feed.url)
        # Avoid bursts to sites; check for challenge page without bypassing it.
        time.sleep(max(0, 0.5 - (time.monotonic() - self.last)))
        host = urllib.parse.urlsplit(feed.url).hostname
        doc = self._read(feed.url, host)
        if any(marker in doc[:6000].lower() for marker in (
            'verify that you\'re not a robot', 'cf-chl-', 'enable javascript and reload the page',
            'javascript is disabled</h',
        )):
            raise RuntimeError('サイト側のアクセス確認画面が表示されました')
        return doc


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


def update(client: WebClient, old: dict, now: datetime) -> dict:
    results = {}
    for feed in FEEDS:
        prior = old.get('feeds', {}).get(feed.key, {})
        previous_items = prior.get('items') if isinstance(prior.get('items'), list) else []
        try:
            items = extract_articles(feed, client.fetch(feed))
            if not items:
                raise RuntimeError('記事リンクが見つかりません（動的表示・構造変更の可能性）')
            seen_old = {i['url'] for i in previous_items if 'url' in i}
            for item in items:
                item['new'] = bool(previous_items and item['url'] not in seen_old)
            results[feed.key] = {'status': 'ok', 'last_success': now.isoformat(), 'items': items}
            print(f'INFO {feed.site} / {feed.section}: {len(items)}件')
        except Exception as exc:  # each source is independently best effort
            # No ::warning:: annotations. Keep historic data and visibly mark it stale.
            reason = clean(str(exc))[:130] or type(exc).__name__
            preserved = [{**item, 'new': False} for item in previous_items]
            results[feed.key] = {'status': 'cached' if preserved else 'unavailable',
                                 'last_success': prior.get('last_success', ''),
                                 'items': preserved, 'message': reason}
            print(f'INFO {feed.site} / {feed.section}: 取得不可（{reason}）、前回{len(preserved)}件を保持')
    return {'format_version': VERSION, 'updated': now.isoformat(), 'feeds': results}


def e(value: str) -> str:
    return html.escape(str(value), quote=True)


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
    all_items = []
    statuses = []
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
    # Cross-feed de-duplication by canonical link; title is not a reliable identity.
    unique = {}
    for item in all_items:
        unique.setdefault(item['url'], item)
    all_items = list(unique.values())
    all_items.sort(key=lambda item: (item.get('date', ''), -item['_rank']), reverse=True)
    groups: dict[str, list[str]] = {site: [] for site in dict.fromkeys(f.site for f in FEEDS)}
    articles = []
    for idx, item in enumerate(all_items, 1):
        ident = f'news-{idx}'
        new = 'true' if item.get('new') and not item.get('stale') else 'false'
        title = e(item['title'])
        site = e(item['site'])
        section = e(item['section'])
        date = e(item.get('date', ''))
        stamp = f'<time datetime="{date}">{date}</time>' if date else '日付未取得'
        stale = '<span class="stale-label">前回取得分</span>' if item.get('stale') else ''
        new_icon = '<span class="new-label">NEW</span>' if new == 'true' else ''
        groups[item['site']].append(f'<li data-new="{new}"><a href="#{ident}">{title}</a></li>')
        articles.append(f'<article id="{ident}" data-new="{new}" data-site="{e(item["site"])}">'
                        f'<h2><a href="{e(item["url"])}" target="_blank" rel="noopener noreferrer nofollow">{title}</a></h2>'
                        f'<p class="meta">{site} <span class="separator">/</span> {section} <span class="separator">/</span> {stamp} {new_icon} {stale}</p>'
                        f'<p class="read-original"><a href="{e(item["url"])}" target="_blank" rel="noopener noreferrer nofollow">公式サイトで記事を読む <span aria-hidden="true">↗</span></a></p>'
                        '</article>')
    nav = ''.join(f'<section class="category" data-site="{e(site)}"><h2>{e(site)} <span class="count">{len(links)}</span></h2><ul>{"".join(links)}</ul></section>'
                  for site, links in groups.items() if links)
    options = ''.join(f'<option value="{e(site)}">{e(site)}</option>' for site in groups if groups[site])
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
h1{{font-size:23px;line-height:1.3;margin:0;flex:none}}h1 a{{color:inherit;text-decoration:none}}h2{{font-size:20px;line-height:1.55;margin:0 0 8px}}a{{color:#174c86;text-underline-offset:3px}}a:focus-visible,button:focus-visible,select:focus-visible{{outline:2px solid #14532d;outline-offset:3px}}
.update-controls{{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;line-height:1.25}}.new-filter{{border:0;background:transparent;color:#555;font:inherit;font-size:14px;padding:2px 0;cursor:pointer}}.updated{{font-size:14px;color:#555}}
[hidden]{{display:none!important}}.layout{{display:grid;grid-template-columns:minmax(230px,320px) minmax(0,1fr);gap:36px;align-items:start}}
nav{{font-size:14px;position:sticky;top:calc(var(--header-height,42px) + 8px);max-height:calc(100dvh - var(--header-height,42px) - 16px);overflow:auto;padding-top:12px}}
nav h2{{font-size:14px;line-height:1.4;margin:0 0 4px}}.category{{margin:0 0 18px}}.category ul{{list-style:none;margin:0;padding:0}}.category li{{padding:2px 0}}.count{{color:#777;font-weight:400}}.source-status{{margin-top:22px;border-top:1px solid #ddd;padding-top:12px}}.source-status summary{{font-weight:600;cursor:pointer}}.source-status ul{{list-style:none;padding:0;margin:8px 0 0}}.source-status li{{margin-bottom:7px;line-height:1.4}}.source-state{{display:block;color:#777;font-size:12px}}.source-summary{{font-size:13px;color:#666;margin:6px 0 0}}
.toolbar{{display:flex;align-items:center;gap:12px;margin:10px 0 0;flex-wrap:wrap;font-size:14px;color:#555}}.toolbar select{{min-width:180px;max-width:100%;font:inherit;color:#222;background:#fff;border:1px solid #aaa;border-radius:4px;padding:4px 8px}}.feed article h2 a{{color:#14532d;text-decoration:none}}.feed article h2 a:hover{{text-decoration:underline}}article{{border-top:1px solid #bbb;padding:24px 0;scroll-margin-top:calc(var(--header-height,42px) + 12px)}}.feed article:first-child,.feed article.visible-first{{border-top:0;padding-top:12px}}
.meta{{font-size:14px;color:#555;margin:4px 0 8px}}.separator{{color:#aaa}}.new-label{{font-weight:700;color:#b42318;margin-left:6px}}.stale-label{{color:#777;border:1px solid #aaa;border-radius:3px;font-size:12px;padding:0 4px;margin-left:7px}}.read-original{{font-size:14px;margin:0}}.empty{{margin:22px 0;color:#555}}.menu-toggle,.pull-refresh{{display:none}}
@media(max-width:700px){{body{{line-height:1.75}}main{{padding:0 16px 36px}}header{{padding:1px 0;min-height:42px}}.header-row{{gap:6px 10px}}h1{{font-size:20px}}.new-filter{{font-size:14px;padding:0}}.layout{{display:block}}.menu-toggle{{display:inline-flex;align-items:center;justify-content:center;flex:none;order:3;margin-left:auto;width:32px;height:34px;border:0;background:transparent;color:inherit;padding:4px}}.hamburger{{display:flex;flex-direction:column;gap:4px}}.hamburger span{{display:block;width:20px;height:2px;background:currentColor}}.layout nav{{display:none;position:fixed;top:var(--header-height,42px);left:0;right:0;z-index:19;max-height:calc(100dvh - var(--header-height,42px));overflow:auto;padding:12px 16px;background:#fff;border-bottom:1px solid #bbb;box-shadow:0 5px 10px #0002}}body.menu-open .layout nav{{display:block}}.pull-refresh{{display:block;position:fixed;top:0;left:50%;z-index:25;padding:3px 14px;background:#14532d;color:#fff;border-radius:0 0 12px 12px;font-size:14px;pointer-events:none;transform:translate(-50%,-110%);transition:transform .15s}}.pull-refresh.active{{transform:translate(-50%,0)}}article{{padding:18px 0}}h2{{font-size:18px}}}}
@media print{{header{{position:static}}.menu-toggle,nav,.pull-refresh,.toolbar{{display:none!important}}main{{max-width:none;padding:0}}.layout{{display:block}}}}
</style></head><body><div class="pull-refresh" role="status" aria-live="polite">下に引いて更新</div><main id="top"><header><div class="header-row">
<button class="menu-toggle" type="button" aria-label="タイトル一覧を開く" aria-controls="news-nav" aria-expanded="false"><span class="hamburger" aria-hidden="true"><span></span><span></span><span></span></span></button>
<h1><a href="#top">競輪オート投票ニュース</a></h1><div class="update-controls"><time class="updated">{('更新 ' + e(when)) if when else '未更新'}</time><button class="new-filter" type="button" aria-pressed="false">すべて表示中</button></div></div></header>
<div class="layout"><nav id="news-nav" aria-label="タイトル一覧">{nav}<details class="source-status"><summary>取得元・取得状況（{success}/{len(FEEDS)}系統）</summary><p class="source-summary">取得できないサイトは前回取得分を表示する場合があります。</p><ul>{status_html}</ul></details></nav><div class="feed">
<div class="toolbar"><label for="site-filter">サイト</label><select id="site-filter"><option value="">すべてのサイト</option>{options}</select><span id="visible-count" role="status"></span></div>
{articles_html}
</div></div></main><script>
const filterButton=document.querySelector('.new-filter');const siteFilter=document.getElementById('site-filter');
function applyFilters(){{
 const onlyNew=filterButton.getAttribute('aria-pressed')==='true';const site=siteFilter.value;
 let count=0;document.querySelectorAll('.feed article').forEach(node=>{{node.hidden=(onlyNew&&node.dataset.new!=='true')||(site&&node.dataset.site!==site);if(!node.hidden)count++;node.classList.remove('visible-first')}});
 document.querySelector('.feed article:not([hidden])')?.classList.add('visible-first');
 document.querySelectorAll('nav .category').forEach(category=>{{category.hidden=!!site&&category.dataset.site!==site;category.querySelectorAll('li').forEach(li=>{{li.hidden=onlyNew&&li.dataset.new!=='true'}});if(!category.querySelector('li:not([hidden])'))category.hidden=true;}});
 document.getElementById('visible-count').textContent=count+'件';
}}
filterButton.addEventListener('click',()=>{{const active=filterButton.getAttribute('aria-pressed')!=='true';filterButton.setAttribute('aria-pressed',String(active));filterButton.textContent=active?'NEWのみ表示中':'すべて表示中';applyFilters()}});siteFilter.addEventListener('change',applyFilters);applyFilters();
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
