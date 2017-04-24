from gevent.monkey import patch_all; patch_all()
from gevent.pywsgi import WSGIServer

import gevent

import requests
import websocket
import pytest

from wsgiprox.wsgiprox import WSGIProxMiddleware
from wsgiprox.resolvers import FixedResolver, ProxyAuthResolver

from mock import patch

import shutil
import six
import os
import tempfile
from six.moves.urllib.parse import parse_qsl

from io import BytesIO


# ============================================================================
class TestWSGIProx(object):
    @classmethod
    def setup_class(cls):
        cls.test_ca_dir = tempfile.mkdtemp()

        cls.app = WSGIProxMiddleware(TestWSGI(),
                                     '/prefix/',
                                     proxy_options={'ca_root_dir': cls.test_ca_dir},
                                     proxy_apps={'proxy-alias': '',
                                                 'proxy-app-1': CustomApp()
                                                }
                                    )

        cls.auth_resolver = ProxyAuthResolver()

        cls.server = WSGIServer(('localhost', 0), cls.app)
        cls.server.init_socket()
        cls.port = str(cls.server.address[1])

        gevent.spawn(cls.server.serve_forever)

        cls.proxies = cls.proxy_dict(cls.port)

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.test_ca_dir)

    @classmethod
    def proxy_dict(cls, port, host='localhost'):
        return {'http': 'http://{0}:{1}'.format(host, port),
                'https': 'https://{0}:{1}'.format(host, port)
               }

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_non_chunked(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&addproxyhost=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.headers['Content-Length'] != '')
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&addproxyhost=true Proxy Host: wsgiprox'.format(scheme))

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_chunked(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.headers['Transfer-Encoding'] == 'chunked')
        assert(res.headers.get('Content-Length') == None)
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme))

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    @patch('six.moves.http_client.HTTPConnection._http_vsn', 10)
    @patch('six.moves.http_client.HTTPConnection._http_vsn_str', 'HTTP/1.0')
    def test_chunked_force_http10_buffer(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.headers.get('Transfer-Encoding') == None)

        # https, must buffer and set content-length to avoid breaking CONNECT envelope
        # for http, up-to wsgi server if buffering
        if scheme == 'https':
            assert(res.headers['Content-Length'] != '')
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme))

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_write_callable(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&write=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&write=true'.format(scheme))

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_post(self, scheme):
        res = requests.post('{0}://example.com/path/post'.format(scheme), data=BytesIO(b'ABC=1&xyz=2'),
                            proxies=self.proxies,
                            verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/post Post Data: ABC=1&xyz=2'.format(scheme))

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_fixed_host(self, scheme):
        res = requests.get('{0}://wsgiprox/path/file?foo=bar'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_alt_host(self, scheme):
        res = requests.get('{0}://proxy-alias/path/file?foo=bar&addproxyhost=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar&addproxyhost=true Proxy Host: proxy-alias')

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_proxy_app(self, scheme):
        res = requests.get('{0}://proxy-app-1/path/file'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Custom App: proxy-app-1 req to /path/file')

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_download_pem(self, scheme):
        res = requests.get('{0}://wsgiprox/download/pem'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res.headers['content-type'] == 'application/x-x509-ca-cert'

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_download_pkcs12(self, scheme):
        res = requests.get('{0}://wsgiprox/download/p12'.format(scheme),
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res.headers['content-type'] == 'application/x-pkcs12'

    @pytest.mark.parametrize("scheme, ws_scheme", [('http', 'ws'), ('https', 'wss')])
    def test_websocket(self, scheme, ws_scheme):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.app.root_ca_file})
        ws.connect('{0}://example.com/websocket?a=b'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        msg = ws.recv()
        assert(msg == 'WS Request Url: /prefix/{0}://example.com/websocket?a=b Echo: {1} message'.format(scheme, ws_scheme))

    @pytest.mark.parametrize("scheme, ws_scheme", [('http', 'ws'), ('https', 'wss')])
    def test_websocket_fixed_host(self, scheme, ws_scheme):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.app.root_ca_file})
        ws.connect('{0}://wsgiprox/websocket?a=b'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        msg = ws.recv()
        assert(msg == 'WS Request Url: /websocket?a=b Echo: {1} message'.format(scheme, ws_scheme))

    @pytest.mark.parametrize("scheme, ws_scheme", [('http', 'ws'), ('https', 'wss')])
    def test_error_websocket_ignored(self, scheme, ws_scheme):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.app.root_ca_file})
        ws.connect('{0}://wsgiprox/websocket?ignore_ws=true'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        ws.settimeout(0.2)
        with pytest.raises(Exception):
            msg = ws.recv()

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_proxy_auth_required(self, scheme):
        self.app.prefix_resolver = self.auth_resolver

        with pytest.raises(requests.exceptions.RequestException) as err:
            res = requests.get('{0}://example.com/path/file?foo=bar'.format(scheme),
                               proxies=self.proxies)

            res.raise_for_status()

        assert '407 ' in str(err.value)

    @pytest.mark.parametrize("scheme", ['http', 'https'])
    def test_proxy_auth_success(self, scheme):
        self.app.prefix_resolver = self.auth_resolver

        proxies = self.proxy_dict(self.port, 'other-prefix:ignore@localhost')

        res = requests.get('{0}://example.com/path/file?foo=bar'.format(scheme),
                           proxies=proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /other-prefix/{0}://example.com/path/file?foo=bar'.format(scheme))

    def test_error_proxy_unsupported(self):
        from waitress.server import create_server
        server = create_server(self.app, host='127.0.0.1', port=0)

        port = server.effective_port

        gevent.spawn(server.run)

        proxies = self.proxy_dict(port)

        # http proxy not supported: just passes through
        res = requests.get('http://example.com/path/file?foo=bar',
                           proxies=proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

        # https proxy (via CONNECT) not supported
        with pytest.raises(requests.exceptions.ProxyError) as err:
            res = requests.get('https://example.com/path/file?foo=bar',
                               proxies=proxies,
                               verify=self.app.root_ca_file)

        assert '405 ' in str(err.value)

    def test_non_proxy_passthrough(self):
        res = requests.get('http://localhost:' + str(self.port) + '/path/file?foo=bar')
        assert(res.text == 'Requested Url: /path/file?foo=bar')


# ============================================================================
class CustomApp(object):
    def __call__(self, env, start_response):
        result = 'Custom App: ' + env['wsgiprox.proxy_host'] + ' req to ' + env['PATH_INFO']
        result = result.encode('iso-8859-1')

        headers = [('Content-Length', str(len(result)))]

        start_response('200 OK', headers=headers)

        return iter([result])


# ============================================================================
class TestWSGI(object):
    def __call__(self, env, start_response):
        status = '200 OK'

        params = dict(parse_qsl(env.get('QUERY_STRING')))

        ws = env.get('wsgi.websocket')
        if ws and not params.get('ignore_ws'):
            msg = 'WS Request Url: ' + env.get('REQUEST_URI', '')
            msg += ' Echo: ' + ws.receive()
            ws.send(msg)
            return []

        result = 'Requested Url: ' + env.get('REQUEST_URI', '')
        if env['REQUEST_METHOD'] == 'POST':
            result += ' Post Data: ' + env['wsgi.input'].read(int(env['CONTENT_LENGTH'])).decode('utf-8')

        if params.get('addproxyhost') == 'true':
            result += ' Proxy Host: ' + env.get('wsgiprox.proxy_host', '')

        result = result.encode('iso-8859-1')

        if params.get('chunked') == 'true':
            headers = []
        else:
            headers = [('Content-Length', str(len(result)))]

        write = start_response(status, headers)

        if params.get('write') == 'true':
            write(result)
            return iter([])
        else:
            return iter([result])


# ============================================================================
if __name__ == "__main__":
    app = WSGIProxMiddleware(TestWSGI(), FixedResolver('/prefix/'))
    WSGIServer(('localhost', 8080), app).serve_forever()
