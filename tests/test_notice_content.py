import base64
import email.message
import io
import socket
import socketserver
import ssl
import threading
import time
import unittest
import zlib
from unittest.mock import patch

from notice_content import ContentError, extract_content, read_notice, _Document


BODY = '请各学院组织学生报名参加本次实践活动。报名截止日期为十月二十日，提交材料后请留意学院后续通知。'
HTML = '<html><div class="wp_articlecontent"><p>' + BODY + '</p></div></html>'
NOTICE = {'url': 'https://com.hdu.edu.cn/2026/1012/c1a2/page.htm', 'title': '缓存标题不得代替正文'}
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


class Response:
    def __init__(self, html=HTML, status=200, headers=None):
        self.body = html.encode('utf-8') if isinstance(html, str) else html
        self.status = status
        self.closed = False
        self.position = 0
        self.headers = email.message.Message()
        self.headers['Content-Type'] = 'text/html; charset=utf-8'
        for key, value in (headers or {}).items():
            if key in self.headers:
                del self.headers[key]
            self.headers[key] = value

    def read(self, size=-1):
        data = self.body[self.position:] if size < 0 else self.body[self.position:self.position + size]
        self.position += len(data)
        return data

    read1 = read

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return next(self.responses)


class TransportSocket:
    """A real http.client wire exchange with only the OS socket replaced."""
    def __init__(self, response=HTML):
        body = response.encode('utf-8')
        self.wire = b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        self.addresses = []
        self.sent = []
        self.closed = False

    def connect(self, address):
        self.addresses.append(address)

    def settimeout(self, timeout):
        self.timeout = timeout

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def makefile(self, mode):
        return io.BytesIO(self.wire)

    def do_handshake(self):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True


class TLSContext:
    # Python 3.10 initializes these SSLContext properties in HTTPSConnection;
    # newer versions initialize ALPN in a separate default-context helper.
    post_handshake_auth = None
    verify_mode = ssl.CERT_REQUIRED
    check_hostname = True

    def __init__(self):
        self.calls = []

    def set_alpn_protocols(self, protocols):
        self.alpn_protocols = list(protocols)

    def wrap_socket(self, sock, server_hostname=None, do_handshake_on_connect=True):
        self.calls.append((server_hostname, do_handshake_on_connect))
        return sock


class ExtractContentTests(unittest.TestCase):
    def test_excessive_nesting_rejects_malformed_closures_before_quadratic_work(self):
        html = '<div>' * 3000 + '</mismatch>' * 3000
        started = time.perf_counter()
        with self.assertRaisesRegex(ContentError, '复杂'):
            extract_content(html)
        self.assertLess(time.perf_counter() - started, .5)

    def test_excessive_flat_nodes_are_rejected_even_with_readable_body(self):
        html = '<article>' + BODY + '<br>' * 50_001 + '</article>'
        with self.assertRaisesRegex(ContentError, '复杂'):
            extract_content(html)

    def test_webplus_excludes_site_chrome_and_scripts(self):
        html = ('<header>不应包含站点导航</header><div class="wp_articlecontent">'
                '<script>不要执行或送出脚本</script><style>.bad{}</style>'
                '<p>' + BODY + '</p><nav>不应包含正文导航</nav><footer>不应包含页脚</footer></div>')
        self.assertEqual(extract_content(html), BODY)

    def test_tables_and_breaks_preserve_readable_boundaries(self):
        html = '<div class="wp_articlecontent"><p>' + BODY + '</p><table><tr><th>项目</th><th>截止日期</th></tr><tr><td>报名</td><td>10月20日</td></tr></table><p>第一行<br>第二行</p></div>'
        text = extract_content(html)
        self.assertIn('项目\t截止日期\n报名\t10月20日', text)
        self.assertIn('第一行\n第二行', text)

    def test_known_article_containers(self):
        for attrs in ['class="v_news_content"', 'class="article-content"',
                      'class="arti_content"', 'id="newsContent"', 'class="newsContent"']:
            with self.subTest(attrs=attrs):
                self.assertEqual(extract_content('<div ' + attrs + '>' + BODY + '</div>'), BODY)

    def test_article_fallback_omits_title_metadata_and_attachment_labels(self):
        html = '<article><h1>缓存标题不得代替正文</h1><div class="publish_time">发布时间：2026-10-19</div><p>' + BODY + '</p><a href="/report.pdf">附件下载.pdf</a></article>'
        self.assertEqual(extract_content(html), BODY)

    def test_wechat_server_text_is_kept_despite_initial_visibility_style(self):
        html = '<div id="js_content" style="visibility: hidden; opacity:0"><section>' + BODY + '</section></div><div class="rich_media_tool">分享收藏</div>'
        self.assertEqual(extract_content(html, 'https://mp.weixin.qq.com/s/id'), BODY)

    def test_hidden_body_and_image_alt_are_not_evidence(self):
        html = '<div class="wp_articlecontent"><p hidden>' + BODY + '</p><p style="display:none">' + BODY + '</p><img alt="' + BODY + '" src="/notice.jpg"></div>'
        with self.assertRaises(ContentError):
            extract_content(html)

    def test_login_and_challenge_are_not_notice_content(self):
        cases = ['<form><input type="password"><div class="wp_articlecontent">' + BODY + '</div></form>',
                 '<title>安全验证</title><article>请完成验证码后访问。' + BODY + '</article>',
                 '<h1>访问验证</h1><article>请完成访问验证，验证通过后将自动跳转。' + BODY + '</article>',
                 '<title>统一身份认证</title><article>' + BODY + '</article>']
        for html in cases:
            with self.subTest(html=html):
                with self.assertRaises(ContentError):
                    extract_content(html)

    def test_malformed_boolean_attributes_do_not_crash_reader(self):
        self.assertEqual(extract_content('<div class id><article>' + BODY + '</article></div>'), BODY)

    def test_no_body_and_attachment_only_are_clear_failures(self):
        for html in ['<h1>通知标题</h1><footer>' + BODY + '</footer>',
                     '<div class="wp_articlecontent"><a href="/a.pdf">点击下载附件</a></div>',
                     '<div class="wp_articlecontent"><iframe src="/a.pdf"></iframe></div>',
                     '<div class="wp_articlecontent"><img src="/a.png"></div>']:
            with self.subTest(html=html):
                with self.assertRaises(ContentError):
                    extract_content(html)

    def test_entities_and_fullwidth_spaces_normalize_without_losing_text(self):
        text = extract_content('<div class="wp_articlecontent"><p>' + BODY + '</p><p>学生&nbsp;&amp;&nbsp;教师　均可参与。</p></div>')
        self.assertIn('学生 & 教师 均可参与。', text)

    def test_career_decodes_public_html_as_data_without_running_scripts(self):
        fragment = '<p>' + BODY + '</p>'
        encoded = base64.b64encode(zlib.compress(b' ' * 35 + base64.b64encode((' ' * 85 + fragment).encode()))).decode()
        html = '<div class="aTitle">标题不是正文</div><div class="aContent"><section id="content1"></section></div><script>Base64.decode(unzip("' + encoded + '").substr(35)).substr(85)</script>'
        self.assertEqual(extract_content(html, 'https://job.hdu.edu.cn/news/view/aid/1'), BODY)

    def test_career_compressed_html_is_bounded(self):
        encoded = base64.b64encode(zlib.compress(b'x' * 3_000_001)).decode()
        html = 'Base64.decode(unzip("' + encoded + '").substr(0)).substr(0)'
        with self.assertRaises(ContentError):
            extract_content(html, 'https://job.hdu.edu.cn/news/view/aid/1')


class ReadNoticeTests(unittest.TestCase):
    def fetch(self, responses, notice=NOTICE, dns=PUBLIC_DNS):
        opener = Opener(responses)
        with patch('notice_content.socket.getaddrinfo', return_value=dns), patch('notice_content.build_opener', return_value=opener):
            result = read_notice(notice, timeout=15)
        return result, opener.requests

    def test_public_html_returns_body_only_and_sends_no_credentials(self):
        response = Response()
        result, requests = self.fetch([response])
        self.assertEqual(result, {'text': BODY})
        headers = dict(requests[0].header_items())
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('Authorization', headers)
        self.assertTrue(response.closed)

    def test_rejects_credentials_and_non_web_schemes(self):
        for url in ['file:///etc/passwd', 'https://user:secret@com.hdu.edu.cn/a', 'http://localhost/a',
                    'http://127.0.0.1/a', 'http://[::1]/a', 'http://10.0.0.1/a', 'http://169.254.169.254/latest',
                    'http://2130706433/a', 'http://[::ffff:127.0.0.1]/a']:
            with self.subTest(url=url):
                with self.assertRaises(ContentError):
                    self.fetch([Response()], {'url': url}, dns=[(2, 1, 6, '', ('127.0.0.1', 80))])

    def test_private_dns_target_is_never_connected(self):
        dns = [(2, 1, 6, '', ('192.168.101.252', 443))]
        with patch('notice_content.socket.getaddrinfo', return_value=dns), patch('notice_content.build_opener') as connection:
            with self.assertRaises(ContentError):
                read_notice({'url': 'https://external.example/article'})
        connection.return_value.open.assert_not_called()

    def test_exact_configured_hdu_host_works_with_campus_dns(self):
        dns = [(2, 1, 6, '', ('192.168.101.252', 443)),
               (socket.AF_INET6, 1, 6, '', ('2001:4860:4860::8888', 443, 0, 0))]
        result, _ = self.fetch([Response()], dns=dns)
        self.assertEqual(result, {'text': BODY})

    def test_unconfigured_hdu_hostname_does_not_get_private_dns_exception(self):
        with self.assertRaises(ContentError):
            self.fetch([Response()], {'url': 'https://other.hdu.edu.cn/article'}, dns=[(2, 1, 6, '', ('10.0.0.1', 443))])

    def test_same_host_redirect_succeeds_without_cookies(self):
        result, requests = self.fetch([Response(status=302, headers={'Location': '/new.htm', 'Set-Cookie': 'private=x'}), Response()])
        self.assertEqual(result['text'], BODY)
        self.assertEqual(requests[1].full_url, 'https://com.hdu.edu.cn/new.htm')
        self.assertNotIn('Cookie', dict(requests[1].header_items()))

    def test_redirect_to_other_host_or_private_ip_is_rejected(self):
        for target in ['https://example.org/a', 'http://127.0.0.1/a', 'https://com.hdu.edu.cn.evil.test/a', 'http://com.hdu.edu.cn/a']:
            with self.subTest(target=target):
                with self.assertRaises(ContentError):
                    self.fetch([Response(status=302, headers={'Location': target})])

    def test_hdu_apex_to_www_redirect_is_supported(self):
        result, _ = self.fetch([Response(status=301, headers={'Location': 'https://www.hdu.edu.cn/a'}), Response()], {'url': 'https://hdu.edu.cn/a'})
        self.assertEqual(result['text'], BODY)

    def test_redirect_rechecks_dns_for_same_host(self):
        dns = [PUBLIC_DNS, [(2, 1, 6, '', ('10.0.0.1', 443))]]
        opener = Opener([Response(status=302, headers={'Location': '/next'})])
        with patch('notice_content.socket.getaddrinfo', side_effect=dns), patch('notice_content.build_opener', return_value=opener):
            with self.assertRaises(ContentError):
                read_notice({'url': 'https://external.example/article'})
        self.assertEqual(len(opener.requests), 1)

    def test_non_html_and_binary_documents_are_rejected(self):
        for response in [Response(b'%PDF-1.7', headers={'Content-Type': 'application/pdf'}),
                         Response(b'%PDF-1.7', headers={'Content-Type': 'text/html'}),
                         Response(headers={'Content-Type': 'application/octet-stream'})]:
            with self.subTest(response=response):
                with self.assertRaises(ContentError):
                    self.fetch([response])

    def test_charset_can_come_from_meta_tag(self):
        html = '<meta charset="gb2312"><div class="wp_articlecontent">' + BODY + '</div>'
        result, _ = self.fetch([Response(html.encode('gb18030'), headers={'Content-Type': 'text/html'})])
        self.assertEqual(result['text'], BODY)

    def test_body_size_limit_is_enforced(self):
        for response in [Response(b'x' * 3_000_001), Response(headers={'Content-Length': '3000001'})]:
            with self.subTest(response=response):
                with self.assertRaises(ContentError):
                    self.fetch([response])

    def test_fetch_errors_are_safe_chinese_messages(self):
        with patch('notice_content.socket.getaddrinfo', side_effect=OSError('secret=user:password')):
            with self.assertRaises(ContentError) as raised:
                read_notice({'url': 'https://external.example/article'})
        self.assertNotIn('secret', str(raised.exception))
        self.assertRegex(str(raised.exception), '[\u4e00-\u9fff]')

    def test_unauthorized_status_never_becomes_model_input(self):
        with self.assertRaises(ContentError):
            self.fetch([Response(status=403)])

    def test_timeout_is_total_across_redirects(self):
        opener = Opener([Response(status=302, headers={'Location': '/next'}), Response()])
        clock = [100]
        original_open = opener.open
        def open_and_advance(request, timeout):
            result = original_open(request, timeout)
            clock[0] += 10
            return result
        opener.open = open_and_advance
        with patch('notice_content.socket.getaddrinfo', return_value=PUBLIC_DNS), patch('notice_content.build_opener', return_value=opener), patch('notice_content.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(ContentError):
                read_notice(NOTICE, timeout=15)
        self.assertLessEqual(len(opener.requests), 2)

    def test_html_parsing_obeys_the_same_total_deadline_as_download(self):
        clock = [100.0]
        original = _Document.handle_data
        def advance_during_parsing(parser, text):
            clock[0] += 16
            return original(parser, text)
        with patch('notice_content.build_opener', return_value=Opener([Response()])), patch(
                'notice_content.time.monotonic', side_effect=lambda: clock[0]), patch.object(
                _Document, 'handle_data', advance_during_parsing):
            with self.assertRaisesRegex(ContentError, '超时'):
                read_notice(NOTICE, timeout=15)

    def test_article_rendering_obeys_the_same_total_deadline(self):
        import notice_content
        clock = [100.0]
        original = notice_content._render
        def advance_during_render(*args, **kwargs):
            clock[0] += 16
            return original(*args, **kwargs)
        with patch('notice_content.build_opener', return_value=Opener([Response()])), patch(
                'notice_content.time.monotonic', side_effect=lambda: clock[0]), patch(
                'notice_content._render', side_effect=advance_during_render):
            with self.assertRaisesRegex(ContentError, '超时'):
                read_notice(NOTICE, timeout=15)

    def test_external_dns_rebinding_cannot_change_the_connected_address(self):
        sock = TransportSocket()
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))]
        rebound = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with patch('urllib.request.getproxies', return_value={}), patch('notice_content.socket.getaddrinfo', side_effect=[public, rebound]) as resolve, patch('notice_content.socket.socket', return_value=sock):
            self.assertEqual(read_notice({'url': 'http://external.example/article'}), {'text': BODY})
        self.assertEqual(sock.addresses, [('93.184.216.34', 80)])
        self.assertEqual(resolve.call_count, 1)
        self.assertIn(b'Host: external.example\r\n', b''.join(sock.sent))

    def test_external_https_pinning_keeps_original_host_and_tls_sni(self):
        sock, context = TransportSocket(), TLSContext()
        with patch('urllib.request.getproxies', return_value={}), patch('notice_content.socket.getaddrinfo', return_value=PUBLIC_DNS), patch('notice_content.socket.socket', return_value=sock), patch('http.client._create_https_context', return_value=context, create=True), patch('ssl._create_default_https_context', return_value=context):
            self.assertEqual(read_notice({'url': 'https://external.example/article'}), {'text': BODY})
        self.assertEqual(sock.addresses, [('93.184.216.34', 443)])
        self.assertEqual(context.calls, [('external.example', False)])
        self.assertIn(b'Host: external.example\r\n', b''.join(sock.sent))

    def test_total_deadline_interrupts_slow_response_headers(self):
        stopped = threading.Event()
        class DripHandler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.request.recv(65536)
                    self.request.sendall(b'HTTP/1.1 200 OK\r\nX-Drip: ')
                    for _ in range(15):
                        if stopped.wait(.04):
                            return
                        self.request.sendall(b'x')
                    self.request.sendall(b'\r\nContent-Type: text/html\r\nContent-Length: 0\r\n\r\n')
                except OSError:
                    pass
        class TestServer(socketserver.ThreadingTCPServer):
            daemon_threads = True
        with TestServer(('127.0.0.1', 0), DripHandler) as server:
            worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
            worker.start()
            local = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', server.server_address)]
            try:
                with patch('urllib.request.getproxies', return_value={}), patch('notice_content.socket.getaddrinfo', return_value=local):
                    started = time.perf_counter()
                    with self.assertRaisesRegex(ContentError, '超时'):
                        read_notice({'url': 'http://com.hdu.edu.cn/article'}, timeout=.12)
                    elapsed = time.perf_counter() - started
                self.assertLess(elapsed, .38, 'slow headers escaped the total deadline')
            finally:
                stopped.set()
                server.shutdown()
                worker.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
