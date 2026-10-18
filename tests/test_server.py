import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import SOURCES, Store, collect_source, parse_page


def page(title='竞赛 &amp; 公示', date='2026-10-11', link='/2026/1012/c12370a1/page.htm', next_page=''):
    return f'''<div class="clearfix jzlb extra"><div class='btt3'><a target='_blank' href='{link}'><span>{title}</span></a></div><div class='fbsj4'>{date}</div></div>{next_page}'''


class NoticeTests(unittest.TestCase):
    def test_title_entities_and_displayed_date(self):
        rows, next_url = parse_page(page(), SOURCES[1], SOURCES[1]['url'])
        self.assertEqual(rows[0]['title'], '竞赛 & 公示')
        self.assertEqual(rows[0]['date'], '2026-10-11')
        self.assertEqual(rows[0]['url'], 'https://com.hdu.edu.cn/2026/1012/c12370a1/page.htm')
        self.assertIsNone(next_url)

    def test_pagination_and_duplicate_urls(self):
        html = page(next_page='<a class="next" href="/12370/list2.htm">下一页</a>')
        calls = []
        def fetch(url, timeout):
            calls.append(url)
            return html if len(calls) == 1 else page()
        result = collect_source(SOURCES[1], fetch)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['pages'], 2)
        self.assertEqual(calls[-1], 'https://com.hdu.edu.cn/12370/list2.htm')
        self.assertIsNone(result['error'])

    def test_same_title_different_links_retained(self):
        result = collect_source(SOURCES[1], lambda *a, **kw: page() + page(link='/other.htm'))
        self.assertEqual(len(result['items']), 2)

    def test_five_page_limit(self):
        calls = []
        def fetch(url, timeout):
            calls.append(url)
            return page(link=f'/article{len(calls)}.htm', next_page=f'<a class="next" href="/12370/list{len(calls)+1}.htm">下一页</a>')
        result = collect_source(SOURCES[1], fetch)
        self.assertEqual(result['pages'], 5)
        self.assertTrue(result['limited'])

    def test_invalid_html_and_unsafe_urls_are_failures(self):
        for html in ['<h1>验证页面</h1>', page(date='not a date'), page(link='javascript:alert(1)'), page(next_page='<a class="next" href="https://example.com/list2.htm">下一页</a>')]:
            with self.subTest(html=html):
                result = collect_source(SOURCES[1], lambda *a, **kw: html)
                self.assertTrue(result['error'])
                self.assertEqual(result['items'], [])

    def test_second_page_failure_preserves_first_page(self):
        def fetch(url, timeout):
            if 'list2' in url:
                raise TimeoutError()
            return page(next_page='<a class="next" href="/12370/list2.htm">下一页</a>')
        result = collect_source(SOURCES[1], fetch)
        self.assertEqual(result['pages'], 1)
        self.assertEqual(len(result['items']), 1)
        self.assertIn('超时', result['error'])

    def test_failure_keeps_previous_cache_and_partial_merges(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'cache.json')
            good = collect_source(SOURCES[1], lambda *a, **kw: page())
            store.apply_result('com', good)
            store.apply_result('com', {'items': [], 'pages': 0, 'limited': False, 'error': 'offline'})
            self.assertEqual(len(store.records['com']['items']), 1)
            self.assertEqual(store.records['com']['status'], 'error')
            partial = collect_source(SOURCES[1], lambda *a, **kw: page(link='/new.htm'))
            partial['error'] = 'page failed'
            store.apply_result('com', partial)
            self.assertEqual(len(store.records['com']['items']), 2)
            self.assertEqual(store.records['com']['status'], 'partial')

    def test_cache_survives_restart_and_corrupt_cache_warns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            store = Store(path)
            with patch('server.collect_source', side_effect=lambda source: collect_source(source, lambda *a, **kw: page())):
                store.refresh(['jwc', 'com'])
            restored = Store(path)
            self.assertEqual(len(restored.snapshot()['notices']), 2)
            self.assertTrue(all(s['status'] == 'ok' and s['from_cache'] for s in restored.snapshot()['sources'][:2]))
            path.write_text('{broken', encoding='utf-8')
            broken = Store(path)
            self.assertTrue(broken.warning)
            self.assertEqual(broken.snapshot()['notices'], [])

    def test_refresh_coalescing_and_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'cache.json')
            with patch('threading.Thread.start'):
                self.assertEqual(store.start_refresh(), 'started')
                self.assertEqual(store.start_refresh(), 'running')
                store.refreshing = False
                self.assertEqual(store.start_refresh(), 'cooldown')


if __name__ == '__main__':
    unittest.main()
