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
def make_application(test_ca_file=None, proxy_options=None):
    proxy_options = proxy_options or {}
    if test_ca_file is None:
        test_ca_file = os.environ.get('CA_ROOT_FILE',
                                      os.path.join('.', 'wsgiprox-ca-test.pem'))

    proxy_options['ca_name'] = 'wsgiprox test ca'
    proxy_options['ca_file_cache'] = test_ca_file

    from wsgiprox.wsgiprox import WSGIProxMiddleware
    return WSGIProxMiddleware(TestWSGI(),
                              '/prefix/',
                              proxy_options=proxy_options,
                              proxy_apps={'proxy-alias': '',
                                          'proxy-app-1': CustomApp()
                                         }
                              )


# ============================================================================
try:
    import uwsgi
    application = make_application()
except:
    pass


# ============================================================================
if __name__ == "__main__":
    from gevent.pywsgi import WSGIServer
    from gevent.monkey import patch_all; patch_all()

    application = make_application()
    WSGIServer(('localhost', 8080), application).serve_forever()



