from __future__ import absolute_import

import socket
import ssl

from six.moves.urllib.parse import quote, urlsplit
from tempfile import SpooledTemporaryFile

import six
import os
import time
import io
import logging

from certauth.certauth import CertificateAuthority

from OpenSSL import SSL

from wsgiprox.resolvers import FixedResolver


try:
    from geventwebsocket.handler import WebSocketHandler
except:  #pragma: no cover
    WebSocketHandler = object

BUFF_SIZE = 16384

logger = logging.getLogger(__file__)


# ============================================================================
class WrappedWebSockHandler(WebSocketHandler):
    def __init__(self, connect_handler):
        self.environ = connect_handler.environ
        self.start_response = connect_handler.start_response
        self.request_version = 'HTTP/1.1'

        self.socket = connect_handler.curr_sock
        self.rfile = connect_handler.reader

        class FakeServer(object):
            def __init__(self):
                self.application = {}

        self.server = FakeServer()

    @property
    def logger(self):
        return logger


# ============================================================================
class BaseHandler(object):
    FILTER_REQ_HEADERS = ('HTTP_PROXY_CONNECTION',
                          'HTTP_PROXY_AUTHORIZATION')

    @classmethod
    def chunk_encode(cls, orig_iter):
        for chunk in orig_iter:
            chunk_len = len(chunk)
            if chunk_len:
                yield ('%X\r\n' % chunk_len).encode()
                yield chunk
                yield b'\r\n'

        yield b'0\r\n\r\n'

    @classmethod
    def buffer_iter(cls, orig_iter, buff_size=65536):
        out = SpooledTemporaryFile(buff_size)
        size = 0

        for buff in orig_iter:
            size += len(buff)
            out.write(buff)

        content_length_str = str(size)
        out.seek(0)

        def read_iter():
            while True:
                buff = out.read(buff_size)
                if not buff:
                    break
                yield buff

        return content_length_str, read_iter()


# ============================================================================
class SocketReader(io.BufferedIOBase):
    def __init__(self, socket):
        self.socket = socket

    def readable(self):
        return True

    def read(self, size):
        return self.socket.recv(size)


# ============================================================================
class SocketWriter(object):
    def __init__(self, socket):
        self.socket = socket

    def write(self, buff):
        return self.socket.sendall(buff)


# ============================================================================
class ConnectHandler(BaseHandler):
    def __init__(self, curr_sock, scheme, wsgi, resolve):
        self.curr_sock = curr_sock
        self.scheme = scheme

        self.wsgi = wsgi
        self.resolve = resolve

        reader = SocketReader(curr_sock)
        self.reader = io.BufferedReader(reader, BUFF_SIZE)
        self.writer = SocketWriter(curr_sock)

        self.is_keepalive = True

    def __call__(self, environ, enable_ws):
        self._chunk = False
        self._buffer = False
        self.headers_finished = False

        self.convert_environ(environ)

        # check for websocket upgrade, if enabled
        if enable_ws and self.environ.get('HTTP_UPGRADE', '') == 'websocket':
            self.handle_ws()
        else:
            self.finish_response()

        self.is_keepalive = self.environ.get('HTTP_CONNECTION', '') == 'keep-alive'

    def write(self, data):
        self.finish_headers()
        self.writer.write(data)

    def finish_headers(self):
        if not self.headers_finished:
            self.writer.write(b'\r\n')
            self.headers_finished = True

    def start_response(self, statusline, headers, exc_info=None):
        protocol = self.environ.get('SERVER_PROTOCOL', 'HTTP/1.0')
        status_line = protocol + ' ' + statusline + '\r\n'
        self.writer.write(status_line.encode('iso-8859-1'))

        found_cl = False

        for name, value in headers:
            if not found_cl and name.lower() == 'content-length':
                found_cl = True

            line = name + ': ' + value + '\r\n'
            self.writer.write(line.encode('iso-8859-1'))

        if not found_cl:
            if protocol == 'HTTP/1.1':
                self.writer.write(b'Transfer-Encoding: chunked\r\n')
                self._chunk = True
            else:
                self._buffer = True

        return self.write

    def finish_response(self):
        resp_iter = self.wsgi(self.environ, self.start_response)

        if self._chunk:
            resp_iter = self.chunk_encode(resp_iter)

        elif self._buffer and not self.headers_finished:
            cl, resp_iter = self.buffer_iter(resp_iter)
            self.writer.write(b'Content-Length: ' + cl.encode() + b'\r\n')

        # finish headers after wsgi call
        self.finish_headers()

        for obj in resp_iter:
            self.writer.write(obj)

    def close(self):
        self.reader.close()

    def handle_ws(self):
        ws = WrappedWebSockHandler(self)
        result = ws.upgrade_websocket()

        # start_response() already called in upgrade_websocket()
        # flush headers before starting wsgi
        self.finish_headers()

        # wsgi expected to access established 'wsgi.websocket'

        # do-nothing start-response
        def ignore_sr(s, h, e=None):
            return []

        self.wsgi(self.environ, ignore_sr)

    def convert_environ(self, environ):
        self.environ = environ.copy()

        statusline = self.reader.readline().rstrip()

        if six.PY3:  #pragma: no cover
            statusline = statusline.decode('iso-8859-1')

        statusparts = statusline.split(' ', 2)
        hostname = self.environ['wsgiprox.connect_host']

        if len(statusparts) < 3:
            raise Exception('Invalid Proxy Request Line: length={0} from='.format(len(statusline), hostname))

        self.environ['wsgi.url_scheme'] = self.scheme

        self.environ['REQUEST_METHOD'] = statusparts[0]

        self.environ['SERVER_PROTOCOL'] = statusparts[2].strip()

        full_uri = self.scheme + '://' + hostname
        port = self.environ.get('wsgiprox.connect_port', '')
        if port:
            full_uri += ':' + port

        full_uri += statusparts[1]

        self.resolve(full_uri, self.environ, hostname)

        while True:
            line = self.reader.readline()
            if line:
                line = line.rstrip()
                if six.PY3:  #pragma: no cover
                    line = line.decode('iso-8859-1')

            if not line:
                break

            parts = line.split(':', 1)
            if len(parts) < 2:
                continue

            name = parts[0].strip()
            value = parts[1].strip()

            name = name.replace('-', '_').upper()

            if name not in ('CONTENT_LENGTH', 'CONTENT_TYPE'):
                name = 'HTTP_' + name

            if name not in self.FILTER_REQ_HEADERS:
                self.environ[name] = value

        self.environ['wsgi.input'] = self.reader


# ============================================================================
class HttpProxyHandler(BaseHandler):
    PROXY_CONN_CLOSE = ('Proxy-Connection', 'close')

    def __init__(self, start_response, wsgi, resolve):
        self.real_start_response = start_response

        self.wsgi = wsgi
        self.resolve = resolve

    def convert_environ(self, environ):
        self.environ = environ

        full_uri = self.environ['REQUEST_URI']

        parts = urlsplit(full_uri)

        self.resolve(full_uri, self.environ, parts.netloc.split(':')[0])

        for header in list(self.environ.keys()):
            if header in self.FILTER_REQ_HEADERS:
                self.environ.pop(header, '')

    def start_response(self, statusline, headers, exc_info=None):
        headers.append(self.PROXY_CONN_CLOSE)

        return self.real_start_response(statusline, headers, exc_info)

    def __call__(self, environ):
        self.convert_environ(environ)
        return self.wsgi(self.environ, self.start_response)


# ============================================================================
class WSGIProxMiddleware(object):
    DEFAULT_HOST = 'wsgiprox'

    CA_ROOT_NAME = 'wsgiprox https proxy CA'

    CA_ROOT_FILE = os.path.join('.', 'ca', 'wsgiprox-ca.pem')

    SSL_BASIC_OPTIONS = (
        SSL.OP_CIPHER_SERVER_PREFERENCE
    )

    SSL_DEFAULT_METHOD = SSL.SSLv23_METHOD
    SSL_DEFAULT_OPTIONS = (
        SSL.OP_NO_TICKET |
        SSL.OP_NO_SSLv2 |
        SSL.OP_NO_SSLv3 |
        SSL_BASIC_OPTIONS
    )

    CONNECT_RESPONSE_1_1 = b'HTTP/1.1 200 Connection Established\r\n\r\n'

    CONNECT_RESPONSE_1_0 = b'HTTP/1.0 200 Connection Established\r\n\r\n'

    DEFAULT_MAX_TUNNELS = 50

    @classmethod
    def set_connection_class(cls):
        try:
            import gevent.socket
            assert(gevent.socket.socket == socket.socket)
            from wsgiprox.gevent_ssl import SSLConnection as SSLConnection
            cls.is_gevent_ssl = True
        except Exception as e:  #pragma: no cover
            logger.debug(str(e))
            from OpenSSL.SSL import Connection as SSLConnection
            cls.is_gevent_ssl = False
        finally:
            cls.SSLConnection = SSLConnection

    def __init__(self, wsgi,
                 prefix_resolver=None,
                 download_host=None,
                 proxy_host=None,
                 proxy_options=None,
                 proxy_apps=None):

        self._wsgi = wsgi
        self.set_connection_class()

        if isinstance(prefix_resolver, str):
            prefix_resolver = FixedResolver(prefix_resolver)

        self.prefix_resolver = prefix_resolver or FixedResolver()

        self.proxy_apps = proxy_apps or {}

        self.proxy_host = proxy_host or self.DEFAULT_HOST

        if self.proxy_host not in self.proxy_apps:
            self.proxy_apps[self.proxy_host] = None

        # HTTPS Only Options
        proxy_options = proxy_options or {}

        ca_name = proxy_options.get('ca_name', self.CA_ROOT_NAME)

        ca_file_cache = proxy_options.get('ca_file_cache', self.CA_ROOT_FILE)

        self.ca = CertificateAuthority(ca_name=ca_name,
                                       ca_file_cache=ca_file_cache,
                                       cert_cache=None,
                                       cert_not_before=-3600)

        self.keepalive_max = proxy_options.get('keepalive_max', self.DEFAULT_MAX_TUNNELS)
        self.keepalive_opts = hasattr(socket, 'TCP_KEEPIDLE')

        self._tcp_keepidle = proxy_options.get('tcp_keepidle', 60)
        self._tcp_keepintvl = proxy_options.get('tcp_keepintvl', 5)
        self._tcp_keepcnt = proxy_options.get('tcp_keepcnt', 3)

        self.num_open_tunnels = 0

        try:
            self.root_ca_file = self.ca.get_root_pem_filename()
        except Exception as e:
            self.root_ca_file = None

        self.use_wildcard = proxy_options.get('use_wildcard_certs', True)

        if proxy_options.get('enable_cert_download', True):
            download_host = download_host or self.DEFAULT_HOST
            self.proxy_apps[download_host] = CertDownloader(self.ca)

        self.enable_ws = proxy_options.get('enable_websockets', True)
        if WebSocketHandler == object:
            self.enable_ws = None

    def wsgi(self, env, start_response):
        # see if the host matches one of the proxy app hosts
        # if so, try to see if there is an wsgi app set
        # and if it returns something
        hostname = env.get('wsgiprox.matched_proxy_host')
        if hostname:
            app = self.proxy_apps.get(hostname)
            if app:
                res = app(env, start_response)
                if res is not None:
                    return res

        # call upstream wsgi app
        return self._wsgi(env, start_response)

    def __call__(self, env, start_response):
        if env['REQUEST_METHOD'] == 'CONNECT':
            return self.handle_connect(env, start_response)
        else:
            self.ensure_request_uri(env)

            if env['REQUEST_URI'].startswith('http://'):
                return self.handle_http_proxy(env, start_response)
            else:
                return self.wsgi(env, start_response)

    def handle_http_proxy(self, env, start_response):
        res = self.require_auth(env, start_response)
        if res is not None:
            return res

        handler = HttpProxyHandler(start_response, self.wsgi, self.resolve)
        return handler(env)

    def handle_connect(self, env, start_response):
        raw_sock = self.get_raw_socket(env)
        if not raw_sock:
            start_response('405 HTTPS Proxy Not Supported',
                           [('Content-Length', '0')])
            return []

        res = self.require_auth(env, start_response)
        if res is not None:
            return res

        connect_handler = None
        curr_sock = None

        try:
            scheme, curr_sock = self.wrap_socket(env, raw_sock)

            connect_handler = ConnectHandler(curr_sock, scheme,
                                             self.wsgi, self.resolve)

            self.num_open_tunnels += 1

            connect_handler(env, self.enable_ws)

            while self.keep_alive(connect_handler):
                connect_handler(env, self.enable_ws)

        except Exception as e:
            logger.debug(str(e))
            start_response('500 Unexpected Error',
                           [('Content-Length', '0')])


        finally:
            if connect_handler:
                self.num_open_tunnels -= 1
                connect_handler.close()

            if curr_sock and curr_sock != raw_sock:
                # this seems to necessary to avoid tls data read later
                # in the same gevent
                if self.is_gevent_ssl:
                    curr_sock.recv(0)

                curr_sock.shutdown()
                curr_sock.close()

            start_response('200 OK', [])

        return []

    def keep_alive(self, connect_handler):
        # keepalive disabled
        if self.keepalive_max < 0:
            return False

        if not connect_handler.is_keepalive:
            return False

        # no max
        if self.keepalive_max == 0:
            return True

        return (self.num_open_tunnels <= self.keepalive_max)

    def _new_context(self):
        context = SSL.Context(self.SSL_DEFAULT_METHOD)
        context.set_options(self.SSL_DEFAULT_OPTIONS)
        context.set_session_cache_mode(SSL.SESS_CACHE_OFF)
        return context

    def create_ssl_context(self, hostname):
        cert, key = self.ca.load_cert(hostname,
                                      wildcard=self.use_wildcard,
                                      wildcard_use_parent=True)

        context = self._new_context()
        context.use_privatekey(key)
        context.use_certificate(cert)
        return context

    def _get_connect_response(self, env):
        if env.get('SERVER_PROTOCOL', 'HTTP/1.0') == 'HTTP/1.1':
            return self.CONNECT_RESPONSE_1_1
        else:
            return self.CONNECT_RESPONSE_1_0

    def wrap_socket(self, env, sock):
        if self.keepalive_max >= 0:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            if self.keepalive_opts:  #pragma: no cover
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, self._tcp_keepidle)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, self._tcp_keepintvl)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, self._tcp_keepcnt)

        host_port = env['PATH_INFO']
        hostname, port = host_port.split(':', 1)
        env['wsgiprox.connect_host'] = hostname

        sock.sendall(self._get_connect_response(env))

        if port == '80':
            return 'http', sock

        if port != '443':
            env['wsgiprox.connect_port'] = port
            peek_buff = sock.recv(16, socket.MSG_PEEK)
            # http websocket traffic would start with a GET
            if peek_buff.startswith(b'GET '):
                return 'http', sock

        def sni_callback(connection):
            sni_hostname = connection.get_servername()

            # curl -k (unverified) mode results in empty hostname here
            # requests unverified mode still includes an sni hostname
            if not sni_hostname:
                return

            if six.PY3:
                sni_hostname = sni_hostname.decode('iso-8859-1')

            # if same host as CONNECT header, then just keep current context
            if sni_hostname == hostname:
                return

            connection.set_context(self.create_ssl_context(sni_hostname))
            env['wsgiprox.connect_host'] = sni_hostname

        context = self.create_ssl_context(hostname)
        context.set_tlsext_servername_callback(sni_callback)

        ssl_sock = self.SSLConnection(context, sock)
        ssl_sock.set_accept_state()
        ssl_sock.do_handshake()

        return 'https', ssl_sock

    def require_auth(self, env, start_response):
        if not hasattr(self.prefix_resolver, 'require_auth'):
            return

        auth_req = self.prefix_resolver.require_auth(env)

        if not auth_req:
            return

        auth_req = 'Basic realm="{0}"'.format(auth_req)
        headers = [('Proxy-Authenticate', auth_req),
                   ('Proxy-Connection', 'close'),
                   ('Content-Length', '0')]

        start_response('407 Proxy Authentication', headers)
        return []

    def resolve(self, url, env, hostname):
        if hostname in self.proxy_apps.keys():
            parts = urlsplit(url)
            full = parts.path
            if parts.query:
                full += '?' + parts.query

            env['REQUEST_URI'] = full
            env['wsgiprox.matched_proxy_host'] = hostname
            env['wsgiprox.proxy_host'] = hostname
        else:
            env['REQUEST_URI'] = self.prefix_resolver(url, env)
            env['wsgiprox.proxy_host'] = self.proxy_host

        queryparts = env['REQUEST_URI'].split('?', 1)

        env['PATH_INFO'] = queryparts[0]

        env['QUERY_STRING'] = queryparts[1] if len(queryparts) > 1 else ''

    def ensure_request_uri(self, env):
        if 'REQUEST_URI' in env:
            return

        full_uri = env['PATH_INFO']
        if env.get('QUERY_STRING'):
            full_uri += '?' + env['QUERY_STRING']

        env['REQUEST_URI'] = full_uri

    @classmethod
    def get_raw_socket(cls, env):  #pragma: no cover
        sock = None

        if env.get('uwsgi.version'):
            try:
                import uwsgi
                fd = uwsgi.connection_fd()
                sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
            except Exception as e:
                pass
        elif env.get('gunicorn.socket'):
            sock = env['gunicorn.socket']

        if not sock:
            # attempt to find socket from wsgi.input
            input_ = env.get('wsgi.input')
            if input_:
                if hasattr(input_, '_sock'):
                    raw = input_._sock
                    sock = socket.socket(_sock=raw)
                elif hasattr(input_, 'raw'):
                    sock = input_.raw._sock
                elif hasattr(input_, 'rfile'):
                    # PY3
                    if hasattr(input_.rfile, 'raw'):
                        sock = input_.rfile.raw._sock
                    # PY2
                    else:
                        sock = input_.rfile._sock

        return sock


# ============================================================================
class CertDownloader(object):
    DL_PEM = '/download/pem'
    DL_P12 = '/download/p12'

    def __init__(self, ca):
        self.ca = ca

    def __call__(self, env, start_response):
        path = env.get('PATH_INFO')

        if path == self.DL_PEM:
            buff = self.ca.get_root_pem()

            content_type = 'application/x-x509-ca-cert'

        elif path == self.DL_P12:
            buff = self.ca.get_root_PKCS12()

            content_type = 'application/x-pkcs12'

        else:
            return None

        headers = [('Content-Length', str(len(buff))),
                   ('Content-Type', content_type)]

        start_response('200 OK', headers)
        return [buff]


