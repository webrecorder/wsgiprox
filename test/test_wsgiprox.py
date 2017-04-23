from gevent.monkey import patch_all; patch_all()
from gevent.pywsgi import WSGIServer

import gevent

import requests
import websocket
import pytest
import base64

from wsgiprox.wsgiprox import WSGIProxMiddleware
from wsgiprox.resolvers import FixedResolver, ProxyAuthResolver

import shutil
import six
import os
import tempfile

from io import BytesIO


# ============================================================================
class TestWSGIProx(object):
    @classmethod
    def setup_class(cls):
        cls.test_ca_dir = tempfile.mkdtemp()

        cls.app = WSGIProxMiddleware(TestWSGI(),
                                     '/prefix/',
                                     proxy_options={'ca_root_dir': cls.test_ca_dir})

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

    def b64encode(self, string):
        string = base64.b64encode(string.encode('utf-8'))
        if six.PY3:
            string = string.decode('utf-8')

        return string

    def test_http(self):
        res = requests.get('http://example.com/path/file?foo=bar',
                           proxies=self.proxies)

        assert(res.text == 'Requested Url: /prefix/http://example.com/path/file?foo=bar')

    def test_https(self):
        res = requests.get('https://example.com/path/file?foo=bar',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/https://example.com/path/file?foo=bar')

    def test_http_post(self):
        res = requests.post('http://example.com/path/post', data=BytesIO(b'ABC=1&xyz=2'),
                            proxies=self.proxies,
                            verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/http://example.com/path/post Post Data: ABC=1&xyz=2')

    def test_https_post(self):
        res = requests.post('https://example.com/path/post', data=BytesIO(b'ABC=1&xyz=2'),
                            proxies=self.proxies,
                            verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/https://example.com/path/post Post Data: ABC=1&xyz=2')

    def test_http_fixed_host(self):
        res = requests.get('http://wsgiprox/path/file?foo=bar',
                           proxies=self.proxies)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

    def test_https_fixed_host(self):
        res = requests.get('https://wsgiprox/path/file?foo=bar',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

    def test_download_pem(self):
        res = requests.get('http://wsgiprox/download/pem',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res.headers['content-type'] == 'application/x-x509-ca-cert'

        res2 = requests.get('https://wsgiprox/download/pem',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res2.headers['content-type'] == 'application/x-x509-ca-cert'

        assert res.content == res2.content

    def test_download_pkcs12(self):
        res = requests.get('http://wsgiprox/download/p12',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res.headers['content-type'] == 'application/x-pkcs12'

        res2 = requests.get('https://wsgiprox/download/p12',
                           proxies=self.proxies,
                           verify=self.app.root_ca_file)

        assert res2.headers['content-type'] == 'application/x-pkcs12'

        #assert res.content == res2.content

    def test_non_proxy(self):
        res = requests.get('http://localhost:' + str(self.port) + '/path/file?foo=bar')
        assert(res.text == 'Requested Url: /path/file?foo=bar')

    def test_http_websocket(self):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket()
        ws.connect('ws://example.com/websocket',
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('plain message')
        msg = ws.recv()
        assert(msg == 'WS Request Url: /prefix/http://example.com/websocket Echo: plain message')

    def test_https_websocket(self):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.app.root_ca_file})
        ws.connect('wss://example.com/websocket?type=ws',
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('ssl message')
        msg = ws.recv()
        assert(msg == 'WS Request Url: /prefix/https://example.com/websocket?type=ws Echo: ssl message')

    def test_https_websocket_fixed_host(self):
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.app.root_ca_file})
        ws.connect('wss://wsgiprox/websocket?type=ws',
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('ssl message')
        msg = ws.recv()
        assert(msg == 'WS Request Url: /websocket?type=ws Echo: ssl message')

    def test_http_proxy_auth(self):
        self.app.prefix_resolver = self.auth_resolver

        with pytest.raises(requests.exceptions.HTTPError) as err:
            res = requests.get('http://example.com/path/file?foo=bar',
                               proxies=self.proxies)

            res.raise_for_status()

        assert '407 ' in str(err.value)

        proxies = self.proxy_dict(self.port, 'other-prefix:ignore@localhost')

        res = requests.get('http://example.com/path/file?foo=bar',
                           proxies=proxies)

        assert(res.text == 'Requested Url: /other-prefix/http://example.com/path/file?foo=bar')

    def test_https_proxy_auth(self):
        self.app.prefix_resolver = self.auth_resolver

        with pytest.raises(requests.exceptions.ProxyError) as err:
            res = requests.get('https://example.com/path/file?foo=bar',
                               proxies=self.proxies)

        assert '407 ' in str(err.value)

        proxies = self.proxy_dict(self.port, 'other-prefix:ignore@localhost')

        res = requests.get('https://example.com/path/file?foo=bar',
                           proxies=proxies,
                           verify=self.app.root_ca_file)

        assert(res.text == 'Requested Url: /other-prefix/https://example.com/path/file?foo=bar')

    def test_unsupported_https_proxy(self):
        from waitress.server import create_server
        server = create_server(self.app, host='127.0.0.1', port=0)

        port = server.effective_port

        gevent.spawn(server.run)

        proxies = self.proxy_dict(port)

        with pytest.raises(requests.exceptions.ProxyError) as err:
            res = requests.get('https://example.com/path/file?foo=bar',
                               proxies=proxies,
                               verify=self.app.root_ca_file)

        assert '405 ' in str(err.value)

# ============================================================================
class TestWSGI(object):
    def __call__(self, env, start_response):
        status = '200 OK'

        ws = env.get('wsgi.websocket')
        if ws:
            msg = 'WS Request Url: ' + env.get('REQUEST_URI', '')
            msg += ' Echo: ' + ws.receive()
            ws.send(msg)
            return []

        result = 'Requested Url: ' + env.get('REQUEST_URI', '')
        if env['REQUEST_METHOD'] == 'POST':
            result += ' Post Data: ' + env['wsgi.input'].read(int(env['CONTENT_LENGTH'])).decode('utf-8')

        result = result.encode('iso-8859-1')
        headers = [('Content-Length', str(len(result)))]

        start_response(status, headers)
        return [result]


# ============================================================================
if __name__ == "__main__":
    app = WSGIProxMiddleware(TestWSGI(), FixedResolver('/prefix/'))
    WSGIServer(('localhost', 8080), app).serve_forever()
