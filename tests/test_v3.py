import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
import zlib

from server import Store, SOURCES, parse_page, collect_source, Handler, ThreadingHTTPServer
from test_server import page

BY_ID = {s['id']: s for s in SOURCES}


class V3Tests(unittest.TestCase):
    def test_source_inventory(self):
        self.assertEqual(len(BY_ID), 25)
        self.assertEqual(len({s['url'] for s in SOURCES}), 25)
        self.assertEqual(sum(s['group'] == 'school' for s in SOURCES), 6)

    def test_school_date_and_exclude_footer(self):
        html = '''<div class="col_news_list"><li class="news"><div class="news_title"><a href="/a.htm">校园活动</a></div><div class="news_day">09</div><div class="news_month">2026.10</div></li><a class="next" href="/tzgg/list2.htm">下一页</a></div><li class="news">页脚导航</li>'''
        rows, next_url = parse_page(html, BY_ID['school'], BY_ID['school']['url'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'], '2026-10-09')
        self.assertTrue(next_url.endswith('/tzgg/list2.htm'))

    def test_school_recovers_full_title_from_matching_attribute(self):
        html = '''<div class="col_news_list"><li class="news"><div class="news_title"><a href="/2026/1013/c11821a301429/page.htm" title="活动预告|浙青年·爱运动“追风逐‘芯’,奔赴新程 ”开学第一跑（绍兴校区专场）">活动预告|浙青年·爱运动“追风逐‘芯’,奔赴新程 ”开学第一跑（...</a></div><div class="news_day">13</div><div class="news_month">2026.10</div></li></div>'''
        rows, _ = parse_page(html, BY_ID['school'], BY_ID['school']['url'])
        self.assertEqual(rows[0]['title'], '活动预告|浙青年·爱运动“追风逐‘芯’,奔赴新程 ”开学第一跑（绍兴校区专场）')

    def test_school_ignores_unreliable_title_attributes(self):
        for visible, attribute in [
            ('校园活动...', ''), ('校园活动...', '   '),
            ('校园活动...', '点击查看详情'), ('校园活动...', '校园活动...'),
            ('校园活动...', '另一场校园活动的完整标题'),
            ('校园活动', '校园活动的额外提示'),
        ]:
            with self.subTest(visible=visible, attribute=attribute):
                html = f'''<div class="col_news_list"><li class="news"><div class="news_title"><a href="/a.htm" title="{attribute}">{visible}</a></div><div class="news_day">13</div><div class="news_month">2026.10</div></li></div>'''
                rows, _ = parse_page(html, BY_ID['school'], BY_ID['school']['url'])
                self.assertEqual(rows[0]['title'], visible)

    def test_other_templates_keep_visible_title(self):
        html_cases = [
            ('com', '''<div class="jzlb"><div class="btt3"><a href="/a.htm" title="校园活动完整标题">校园活动...</a></div><div class="fbsj4">2026-10-13</div></div>'''),
            ('career', '''<ul class="newsList"><li class="span2">2026-10-13</li><li class="span1"><a href="/news/view/aid/1/tag/tzgg" title="校园活动完整标题">校园活动...</a></li></ul>'''),
        ]
        for source_id, html in html_cases:
            with self.subTest(source_id=source_id):
                rows, _ = parse_page(html, BY_ID[source_id], BY_ID[source_id]['url'])
                self.assertEqual(rows[0]['title'], '校园活动...')

    def test_career_public_encoded_list_and_pagination(self):
        content = '''<ul class="newsList"><li class="span2">2026-09-29</li><li class="span1"><b>置顶</b><a href="/news/view/aid/1/tag/tzgg">招聘公告</a></li></ul><li class="next"><a href="/news/index/do1/job.hdu.edu.cn/domain/hdu/tag/tzgg/keyword//page/2">下一页</a></li>'''
        encoded = base64.b64encode(zlib.compress(b' ' * 35 + base64.b64encode((' ' * 85 + content).encode()))).decode()
        html = f'Base64.decode(unzip("{encoded}").substr(35)).substr(85)'
        rows, url = parse_page(html, BY_ID['career'], BY_ID['career']['url'])
        self.assertEqual(rows[0]['title'], '招聘公告')
        self.assertEqual(url, BY_ID['career']['url'] + '?page=2')
        _, next_url = parse_page(content.replace('/news/index/do1/job.hdu.edu.cn/domain/hdu/tag/tzgg/keyword//page/2', '/news/index/page/3/do1/job.hdu.edu.cn/domain/hdu/tag/tzgg/keyword'), BY_ID['career'], url)
        self.assertEqual(next_url, BY_ID['career']['url'] + '?page=3')
        with self.assertRaises(ValueError):
            parse_page(content.replace('job.hdu.edu.cn/domain/hdu', 'evil.example/domain/hdu'), BY_ID['career'], BY_ID['career']['url'])

    def test_library_external_link_and_explicit_empty(self):
        rows, _ = parse_page(page(link='https://mp.weixin.qq.com/s/example'), BY_ID['library'], BY_ID['library']['url'])
        self.assertEqual(rows[0]['url'], 'https://mp.weixin.qq.com/s/example')
        self.assertEqual(parse_page('<em class="all_count">0</em>', BY_ID['student'], BY_ID['student']['url']), ([], None))
        with self.assertRaises(ValueError):
            parse_page('<p>暂无网络连接</p>', BY_ID['student'], BY_ID['student']['url'])

    def test_partial_restart_and_three_timestamps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            store = Store(path)
            good = collect_source(BY_ID['com'], lambda *a, **k: page())
            with patch('server.now', return_value='2026-10-15T10:00:00+08:00'):
                store.apply_result('com', good)
            partial = {**good, 'error': '第二页超时'}
            with patch('server.now', return_value='2026-10-16T10:00:00+08:00'), patch('server.collect_source', return_value=partial):
                store.refresh(['com'])
            restored = Store(path).records['com']
            self.assertEqual(restored['status'], 'partial')
            self.assertEqual(restored['last_result'], 'partial')
            self.assertTrue(restored['from_cache'])
            self.assertIn('2026-10-15', restored['last_complete_success'])
            self.assertIn('2026-10-16', restored['last_success'])
            self.assertEqual(restored['error'], '第二页超时')

    def test_v1_cached_result_unknown_and_known_failure_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            record = Store.empty_record()
            del record['last_result']
            record.update(status='cached')
            path.write_text(json.dumps({'version': 1, 'sources': {'com': record}}))
            self.assertEqual(Store(path).records['com']['status'], 'unknown')
            record.update(error='timeout')
            path.write_text(json.dumps({'version': 1, 'sources': {'com': record}}))
            self.assertEqual(Store(path).records['com']['status'], 'error')

    def test_empty_success_clears_stale_items_failure_retains(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'cache.json')
            good = collect_source(BY_ID['com'], lambda *a, **k: page())
            store.apply_result('com', good)
            store.apply_result('com', dict(items=[], pages=0, limited=False, error='offline'))
            self.assertEqual(len(store.records['com']['items']), 1)
            store.apply_result('com', dict(items=[], pages=1, limited=False, error=None))
            self.assertEqual(store.records['com']['items'], [])
            self.assertEqual(store.records['com']['status'], 'ok')

    def test_only_selected_sources_fetched_and_invalid_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'cache.json')
            with patch('server.collect_source', return_value=dict(items=[], pages=1, limited=False, error=None)) as fetch:
                store.refresh(['library'])
            self.assertEqual([c.args[0]['id'] for c in fetch.call_args_list], ['library'])
            self.assertEqual(store.records['com']['status'], 'never')
            restored = Store(Path(folder) / 'cache.json')
            self.assertEqual(restored.records['com']['status'], 'never')
            self.assertFalse(restored.records['com']['from_cache'])
            for ids in ([], ['bad'], 'com', [None], [{}]):
                with self.assertRaises(ValueError): store.start_refresh(ids)

    def test_refresh_api_payload_and_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            class TestHandler(Handler):
                store = Store(Path(folder) / 'cache.json')
                def log_message(self, *args): pass
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{httpd.server_port}'
            client = build_opener(ProxyHandler({}))
            def post(payload, origin=base):
                return client.open(Request(base + '/api/refresh', json.dumps(payload).encode(), {'Origin':origin, 'Content-Type':'application/json'}))
            try:
                for payload in ([], {'source_ids':['missing']}, {'source_ids':[]}):
                    with self.assertRaises(HTTPError) as error: post(payload)
                    self.assertEqual(error.exception.code, 400)
                with self.assertRaises(HTTPError) as error: post({}, 'https://example.com')
                self.assertEqual(error.exception.code, 403)
                with patch.object(TestHandler.store, 'start_refresh', return_value='started') as refresh:
                    with post({'source_ids':['library']}) as response: self.assertEqual(response.status, 202)
                    refresh.assert_called_once_with(['library'])
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
