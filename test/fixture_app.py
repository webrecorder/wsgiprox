from gevent.monkey import patch_all; patch_all()
from wsgiprox.wsgiprox import WSGIProxMiddleware
from six.moves.urllib.parse import parse_qsl
import os


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
def make_application(test_ca_dir):
    test_ca_dir = os.path.join(test_ca_dir, 'ca')
    return WSGIProxMiddleware(TestWSGI(),
                              '/prefix/',
                              proxy_options={'ca_root_dir': test_ca_dir},
                              proxy_apps={'proxy-alias': '',
                                          'proxy-app-1': CustomApp()
                                         }
                              )

application = make_application(os.environ.get('CA_ROOT_DIR', '.'))


# ============================================================================
if __name__ == "__main__":
    from gevent.pywsgi import WSGIServer

    WSGIServer(('localhost', 8080), application).serve_forever()


