from gevent.monkey import patch_all; patch_all()
from gevent.pywsgi import WSGIServer

import gevent

import ssl
import sys

import requests
import websocket
import pytest
import subprocess

from wsgiprox.wsgiprox import WSGIProxMiddleware
from wsgiprox.resolvers import FixedResolver, ProxyAuthResolver

from .fixture_app import make_application

from mock import patch

import shutil
import six
import os
import tempfile
import re

from six.moves.http_client import HTTPSConnection, HTTPConnection

from io import BytesIO


# ============================================================================
@pytest.fixture(params=['http', 'https'])
def scheme(request):
    return request.param


@pytest.fixture(params=['ws', 'wss'])
def ws_scheme(request):
    return request.param


# ============================================================================
class BaseWSGIProx(object):
    @classmethod
    def setup_class(cls):
        cls.test_ca_dir = tempfile.mkdtemp()

        cls.app = make_application(cls.test_ca_dir)
        cls.root_ca_file = cls.app.root_ca_file

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.test_ca_dir)

    @classmethod
    def proxy_dict(cls, port, host='localhost'):
        return {'http': 'http://{0}:{1}'.format(host, port),
                'https': 'https://{0}:{1}'.format(host, port)
               }

    def test_non_chunked(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&addproxyhost=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.headers['Content-Length'] != '')
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&addproxyhost=true Proxy Host: wsgiprox'.format(scheme))

    def test_non_chunked_custom_port(self, scheme):
        res = requests.get('{0}://example.com:123/path/file?foo=bar&addproxyhost=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.headers['Content-Length'] != '')
        assert(res.text == 'Requested Url: /prefix/{0}://example.com:123/path/file?foo=bar&addproxyhost=true Proxy Host: wsgiprox'.format(scheme))

    @pytest.mark.skipif(sys.version_info >= (3,0) and sys.version_info < (3,4),
                        reason='Not supported in py3.3')
    def test_with_sni(self):
        conn = SNIHTTPSConnection('localhost', self.port, context=ssl.create_default_context(cafile=self.root_ca_file))
        # set CONNECT host:port
        conn.set_tunnel('93.184.216.34', 443)
        # set actual hostname
        conn._server_hostname = 'example.com'
        conn.request('GET', '/path/file?foo=bar&addproxyhost=true')
        res = conn.getresponse()
        text = res.read().decode('utf-8')
        conn.close()

        assert(res.getheader('Content-Length') != '')
        assert(text == 'Requested Url: /prefix/https://example.com/path/file?foo=bar&addproxyhost=true Proxy Host: wsgiprox')


        conn = SNIHTTPSConnection('localhost', self.port,
                                  context=ssl.create_default_context(cafile=self.root_ca_file))
        # set CONNECT host:port
        conn.set_tunnel('93.184.216.34', 443)
        # set actual hostname
        conn._server_hostname = 'example.com'
        conn.request('GET', '/path/file?foo=bar&addproxyhost=true')
        res = conn.getresponse()
        text = res.read().decode('utf-8')
        conn.close()

        assert(res.getheader('Content-Length') != '')
        assert(text == 'Requested Url: /prefix/https://example.com/path/file?foo=bar&addproxyhost=true Proxy Host: wsgiprox')

    def test_chunked(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        if not (self.server_type == 'uwsgi' and scheme == 'http'):
            assert(res.headers['Transfer-Encoding'] == 'chunked')
        assert(res.headers.get('Content-Length') == None)
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme))

    @patch('six.moves.http_client.HTTPConnection._http_vsn', 10)
    @patch('six.moves.http_client.HTTPConnection._http_vsn_str', 'HTTP/1.0')
    def test_chunked_force_http10_buffer(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.headers.get('Transfer-Encoding') == None)

        # https, must buffer and set content-length to avoid breaking CONNECT envelope
        # for http, up-to wsgi server if buffering
        if scheme == 'https':
            assert(res.headers['Content-Length'] != '')
        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&chunked=true'.format(scheme))

    def test_write_callable(self, scheme):
        res = requests.get('{0}://example.com/path/file?foo=bar&write=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/file?foo=bar&write=true'.format(scheme))

    def test_post(self, scheme):
        res = requests.post('{0}://example.com/path/post'.format(scheme), data=BytesIO(b'ABC=1&xyz=2'),
                            proxies=self.proxies,
                            verify=self.root_ca_file)

        assert(res.text == 'Requested Url: /prefix/{0}://example.com/path/post Post Data: ABC=1&xyz=2'.format(scheme))

    def test_fixed_host(self, scheme):
        res = requests.get('{0}://wsgiprox/path/file?foo=bar'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

    def test_alt_host(self, scheme):
        res = requests.get('{0}://proxy-alias/path/file?foo=bar&addproxyhost=true'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar&addproxyhost=true Proxy Host: proxy-alias')

    def test_proxy_app(self, scheme):
        res = requests.get('{0}://proxy-app-1/path/file'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert(res.text == 'Custom App: proxy-app-1 req to /path/file')

    def test_download_pem(self, scheme):
        res = requests.get('{0}://wsgiprox/download/pem'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert res.headers['content-type'] == 'application/x-x509-ca-cert'

    def test_download_pkcs12(self, scheme):
        res = requests.get('{0}://wsgiprox/download/p12'.format(scheme),
                           proxies=self.proxies,
                           verify=self.root_ca_file)

        assert res.headers['content-type'] == 'application/x-pkcs12'

    def test_websocket(self, ws_scheme):
        scheme = ws_scheme.replace('ws', 'http')
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.root_ca_file})
        ws.connect('{0}://example.com/websocket?a=b'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        msg = ws.recv()
        assert(msg == 'WS Request Url: /prefix/{0}://example.com/websocket?a=b Echo: {1} message'.format(scheme, ws_scheme))

    def test_websocket_custom_port(self, ws_scheme):
        scheme = ws_scheme.replace('ws', 'http')
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.root_ca_file})
        ws.connect('{0}://example.com:456/websocket?a=b'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        msg = ws.recv()
        assert(msg == 'WS Request Url: /prefix/{0}://example.com:456/websocket?a=b Echo: {1} message'.format(scheme, ws_scheme))

    def test_websocket_fixed_host(self, ws_scheme):
        scheme = ws_scheme.replace('ws', 'http')
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.root_ca_file})
        ws.connect('{0}://wsgiprox/websocket?a=b'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        msg = ws.recv()
        assert(msg == 'WS Request Url: /websocket?a=b Echo: {1} message'.format(scheme, ws_scheme))

    def test_error_websocket_ignored(self, ws_scheme):
        scheme = ws_scheme.replace('ws', 'http')
        pytest.importorskip('geventwebsocket.handler')

        ws = websocket.WebSocket(sslopt={'ca_certs': self.root_ca_file})
        ws.connect('{0}://wsgiprox/websocket?ignore_ws=true'.format(ws_scheme),
                   http_proxy_host='localhost',
                   http_proxy_port=self.port)

        ws.send('{0} message'.format(ws_scheme))
        ws.settimeout(0.2)
        with pytest.raises(Exception):
            msg = ws.recv()

    def test_non_proxy_passthrough(self):
        res = requests.get('http://localhost:' + str(self.port) + '/path/file?foo=bar')
        assert(res.text == 'Requested Url: /path/file?foo=bar')


# ============================================================================
class Test_gevent_WSGIProx(BaseWSGIProx):
    @classmethod
    def setup_class(cls):
        super(Test_gevent_WSGIProx, cls).setup_class()
        cls.server = WSGIServer(('localhost', 0), cls.app)
        cls.server.init_socket()
        cls.port = str(cls.server.address[1])

        gevent.spawn(cls.server.serve_forever)

        cls.proxies = cls.proxy_dict(cls.port)

        cls.auth_resolver = ProxyAuthResolver()

        cls.server_type = 'gevent'

    def test_proxy_auth_required(self, scheme):
        self.app.prefix_resolver = self.auth_resolver

        with pytest.raises(requests.exceptions.RequestException) as err:
            res = requests.get('{0}://example.com/path/file?foo=bar'.format(scheme),
                               proxies=self.proxies)

            res.raise_for_status()

        assert '407 ' in str(err.value)

    def test_proxy_auth_success(self, scheme):
        self.app.prefix_resolver = self.auth_resolver

        proxies = self.proxy_dict(self.port, 'other-prefix:ignore@localhost')

        res = requests.get('{0}://example.com/path/file?foo=bar'.format(scheme),
                           proxies=proxies,
                           verify=self.root_ca_file)

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
                           verify=self.root_ca_file)

        assert(res.text == 'Requested Url: /path/file?foo=bar')

        # https proxy (via CONNECT) not supported
        with pytest.raises(requests.exceptions.ProxyError) as err:
            res = requests.get('https://example.com/path/file?foo=bar',
                               proxies=proxies,
                               verify=self.root_ca_file)

        assert '405 ' in str(err.value)


# ============================================================================
@pytest.mark.skipif(sys.platform == 'win32', reason='no uwsgi on windows')
class Test_uwsgi_WSGIProx(BaseWSGIProx):
    @classmethod
    def setup_class(cls):
        super(Test_uwsgi_WSGIProx, cls).setup_class()

        cls.root_ca_file = os.path.join(cls.test_ca_dir, 'ca', 'wsgiprox-ca.pem')

        env = os.environ.copy()
        env['CA_ROOT_DIR'] = cls.test_ca_dir

        curr_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)))

        try:
            cls.uwsgi = subprocess.Popen(['uwsgi', 'uwsgi.ini'], env=env, cwd=curr_dir,
                                         stderr=subprocess.PIPE)

        except Exception as e:
            pytest.skip('uwsgi not found, skipping uwsgi tests')

        port_rx = re.compile('uwsgi socket 0 bound to TCP address :([\d]+)')

        while True:
            line = cls.uwsgi.stderr.readline().decode('utf-8')
            m = port_rx.search(line)
            if m:
                cls.port = int(m.group(1))
                break

        cls.proxies = cls.proxy_dict(cls.port)

        cls.server_type = 'uwsgi'

    @classmethod
    def teardown_class(cls):
        cls.uwsgi.terminate()
        super(Test_uwsgi_WSGIProx, cls).teardown_class()



# ============================================================================
class SNIHTTPSConnection(HTTPSConnection):
    def connect(self):
        HTTPConnection.connect(self)

        server_hostname = self._server_hostname

        self.sock = self._context.wrap_socket(self.sock,
                                              server_hostname=self._server_hostname)


