from gevent.monkey import patch_all; patch_all()
from gevent.pywsgi import WSGIServer


class TestWSGI(object):
    def __call__(self, env, start_response):
        status = '200 OK'
        string = 'Some Data\n'.encode('iso-8859-1')
        headers = [('Content-Length', str(len(string)))]
        print(env)

        if env['REQUEST_METHOD'] == 'POST':
            print(env['wsgi.input'].read(int(env['CONTENT_LENGTH'])))

        start_response(status, headers)
        return [string]

#application = HttpsMiddleware(TestWSGI(), FixedResolver('/prefix/', ['webrecorder.io']))

if __name__ == '__main__':
    print('Serving on 8088...')
    WSGIServer(('', 8088), application).serve_forever()


