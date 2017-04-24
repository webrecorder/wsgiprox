wsgiprox
========

.. image:: https://travis-ci.org/webrecorder/wsgiprox.svg?branch=master
    :target: https://travis-ci.org/webrecorder/wsgiprox

``wsgiprox`` is a Python WSGI middleware for adding HTTP and HTTPS proxy support to a WSGI application.

The library accepts HTTP and HTTPS proxy connections, and routes them to a designated prefix.

Usage
~~~~~

For example, given a `WSGI <http://wsgi.readthedocs.io/en/latest/>`_ callable ``application``, the middleware could be defined as follows:

.. code:: python

    from wsgiprox.wsgiprox import WSGIProxMiddleware

    application = WSGIProxMiddleware(application, '/prefix/', 'wsgiprox')


With the above configuration, the middleware is configured to add a prefix of ``/prefix/`` to any url, unless it is to the proxy host ``wsgiprox``.  Assuming a WSGI server running on port 8080, the middleware would translate HTTP/S proxy connections to a non-proxy WSGI request, and pass to the wrapped application:

*  Proxy Request: ``curl -x "localhost:8080" "http://example.com/path/file.html?A=B"``

   Translated to: ``curl "http://localhost:8080/prefix/http://example.com/path/file.html?A=B"``
   
   
*  Proxy Request: ``curl -k -x "localhost:8080" "https://example.com/path/file.html?A=B"``

   Translated to: ``curl "http://localhost:8080/prefix/https://example.com/path/file.html?A=B"``
   
*  Proxy Request to proxy host: ``curl -k -x "localhost:8080" "https://wsgiprox/path/file.html?A=B"``

   Ignoring prefix for proxy host: ``curl "http://localhost:8080/path/file.html?A=B"``
   

All standard WSGI ``environ`` fields are set to the expected values for the translated url.

When a request passes through wsgiprox middleware, ``environ['wsgiprox.proxy_host']`` is set to the proxy host.
In this example, the WSGI app could check that ``environ.get('wsgiprox.proxy_host') == 'wsgiprox'`` to ensure that it was a proxy request.


Custom Resolvers
================

The provided ``FixedResolver`` simply prepends a fixed prefix to each url. A custom resolver could compute the final url in a different way. The resolver instance is called with the full url, and the original WSGI ``environ``. The result is the translated ``REQUEST_URI`` that is passed to the WSGI applictaion.

See `resolvers.py <wsgiprox/resolvers.py>`_ for all available resolvers.

For example, the following Resolver translates the url to a custom prefix based on the remote IP of the original request.

.. code:: python

  class Resolver(object):
      def __call__(self, url, environ):
          return '/' + environ['REMOTE_ADDR'] + url
       
  application = WSGIProxMiddleware(application, Resolver())
      

HTTPS CA
========

To support HTTPS proxy, ``wsgiprox`` creates a custom CA (Certificate Authority), which must be accepted by the client (or it must ignore cert verification as with the ``-k`` option in CURL)

By default, ``wsgiprox`` looks for CA .pem at: ``<working dir>/ca/wsgiprox-ca.pem`` and auto-creates this bundle using the `certauth <https://github.com/ikreymer/certauth>`_ library.

The CA file can also be specified explicitly via ``proxy_options`` dict, along with default dir to store certs.

The default settings are equivalent to the following:

.. code:: python

  WSGIProxMiddleware(..., proxy_options={ca_root_dir='./ca',
                                         ca_file='wsgiprox-ca.pem',
                                         ca_certs_dir='certs'})
                                         
The generated ``wsgiprox-ca.pem`` can be imported directly into most browsers directly as a trusted certificate authority, allowing the browser to accept HTTPS content proxied through ``wsgiprox``

Downloading Certs
=================

The CA cert can be downloaded directly from the proxy directly. This allows for quick installation into a client/browser.

 - ``curl -x "localhost:8080" http://wsgiprox/download/pem`` will download in PEM format (for most platforms)
 - ``curl -x "localhost:8080" http://wsgiprox/download/p12`` will download in PKCS12 format (for Windows)

The download host is the same as proxy main host, though can be changed via ``download_host`` param to WSGIProxMiddleware constructor.

Custom Proxy Host Apps
======================

It's is also possible to configure a custom WSGI app per proxy host, eg:

 - ``curl -x "localhost:8080" https://proxy-app-1/path/`` is passed to ``proxy-app-1``
 - ``curl -x "localhost:8080" https://proxy-app-2/foo`` is passed to ``proxy-app-2``
 
This can be done via:

.. code:: python

    from wsgiprox.wsgiprox import WSGIProxMiddleware
    
    proxy_apps = {"proxy-app-1": ProxyApp1WSGI(),
                  "proxy-app-2": ProxyApp2WSGI(),
                  "proxy-alias": None,
                 }

    application = WSGIProxMiddleware(application, proxy_apps=apps)

All other requests, or any requests not handled by the proxy app, are passed to the main ``application``.

In the last case, since there is no proxy app, the request is passed directly to wrapped application.
The ``wsgiprox.proxy_host`` would be set to ``'proxy-alias'`` instead of the default ``'wsgiprox'``, allowing the application to differentiate handling based on the value of ``wsgiprox.proxy_host``.

Internally, the ``proxy_apps`` dict is used to configure the cert downloader app and default proxy host:

.. code:: python

  proxy_apps['proxy_host'] = None
  proxy_apps['download_host'] = CertDownloader(self.ca)


Websockets
==========

``wsgiprox`` optionally also supports proxying websockets, both unencryped ``ws://`` and via TLS ``wss://``. The websockets proxy functionality has primarily been tested with and requires the `gevent-websocket <https://github.com/jgelens/gevent-websocket>`_ library, and assumes that the wrapped WSGI application is also using this library for websocket support. Other implementations are not yet supported.

To enable websocket proxying, install with ``pip install wsgiprox[gevent-websocket]`` which will install ``gevent-websocket``.
To disable websocket proxying even with ``gevent-websocket`` installed, add ``proxy_options={'enable_websockets': False}``

See the `test suite <test/test_wsgiprox.py>`_ for additional details.


How it Works / A note about WSGI
=================================

``wsgiprox`` supports several different proxying methods:
  - HTTP direct proxy, no tunnel
  - HTTP CONNECT tunnel for websockets, no SSL
  - HTTP CONNECT tunnel with SSL (also supports websockets)
  
For regular HTTP proxy, wsgiprox simply rewrites a host-qualifed request such as ``GET http://example.com/``, and passes it along to underlying WSGI app.

The other proxy methods involve the HTTP ``CONNECT`` verb and explicitly establishing a tunnel using the underlying socket. For HTTPS/SSL proxying, an SSL socket is established over the tunnel, while HTTP websocket proxy uses the underlying socket directly.

The system thus relies on being able to access the underyling socket for the connection. As WSGI spec does not provide a way to do this, ``wsgiprox`` is not guaranteed to work under any WSGI server. The CONNECT verb creates a tunnel, and the tunneled connection is what is passed to the wrapped WSGI application. This is non-standard behavior and may not work on all WSGI servers.

This middleware has been tested primarily with gevent WSGI server and uWSGI.

There is also support for gunicorn and wsgiref, as they provide a way to access the underlying success. If the underlying socket can not be accessed, the ``CONNECT`` verb will fail with a 405.

It may be possible to extend support to additional WSGI servers by extending ``WSGIProxMiddleware.get_raw_socket()`` to be able to find the underlying socket.

Inspiration
===========

This project draws on a lot of previous attempts.

Much of the functionality is a refactoring and cleanup of the proxy handling in `pywb <https://github.com/ikreymer/pywb>`_, which is built on top of standalone CA handling library `certauth <https://github.com/ikreymer/certauth>`_.

certauth was refactored from an earlier implementation in `warcprox <https://github.com/internetarchive/warcprox>`_ (which also inspired this name!).

The certificate download feature was inspired by a similar feature available in `mitmprox <https://github.com/mitmproxy/mitmproxy>`_

License
~~~~~~~

``wsgiprox`` is licensed under the Apache 2.0 License and is part of the
Webrecorder project.

See `NOTICE <NOTICE>`__ and `LICENSE <LICENSE>`__ for details.
