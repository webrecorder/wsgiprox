wsgiprox
========

``wsgiprox`` is a Python WSGI middleware for adding HTTP and HTTPS proxy support to a WSGI application.

The library accepts HTTP and HTTPS proxy connections, and routes them to a designated prefix.

Usage
~~~~~

For example, given a `WSGI <http://wsgi.readthedocs.io/en/latest/>`_ callable ``application``, the middleware could be defined as follows:

.. code:: python

    from wsgiprox.wsgiprox import WSGIProxMiddleware, FixedResolver

    application = WSGIProxMiddleware(application, FixedResolver('/prefix/', ['wsgiprox']))


With the above configuration and a server running on port ``8080``,
the middleware would translate HTTP/S proxy connections to a non-proxy WSGI request, and pass to the wrapped application:

*  Proxy Request: ``curl -x "localhost:8080" "http://example.com/path/file.html?A=B"``

   Translated to: ``curl "http://localhost:8080/prefix/http://example.com/path/file.html?A=B"``
   
   
*  Proxy Request: ``curl -k -x "localhost:8080" "https://example.com/path/file.html?A=B"``

   Translated to: ``curl "http://localhost:8080/prefix/https://example.com/path/file.html?A=B"``
   


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

Websockets
==========

``wsgiprox`` optionally also supports proxying websockets, both unencryped ``ws://`` and via TLS ``wss://``. The websockets proxy functionality has primarily been tested with and requires the `gevent-websocket <https://github.com/jgelens/gevent-websocket>`_ library, and assumes that the wrapped WSGI application is also using this library for websocket support. Other implementations are not yet supported.

To enable websocket proxying, install with ``pip install wsgiprox[gevent-websocket]`` which will install ``gevent-websocket``.
To disable websocket proxying even with ``gevent-websocket`` installed, add ``proxy_options={'enable_websockets': False}``

See the `test suite <test/test_wsgiprox.py>`_ for additional details.


How it Works / A note about WSGI
=================================

``wsgiprox`` works by wrapping the HTTP ``CONNECT`` verb and explicitly establishing a tunnel using the underlying socket. The system thus relies on being able to access the underyling socket for the connection.
As WSGI spec does not provide a way to do this, ``wsgiprox`` is not guaranteed to work under any WSGI server. The CONNECT verb creates a tunnel, and the tunneled connection is what is passed to the wrapped WSGI application. This is non-standard behavior and may not work on all WSGI servers.

This middleware has been tested primarily with gevent WSGI server and uWSGI.

There is also support for gunicorn and wsgiref, as they provide a way to access the underlying success. If the underlying socket can not be accessed, the ``CONNECT`` verb will fail with a 405.

It may be possible to extend support to additional WSGI servers by extending ``WSGIProxMiddleware.get_raw_socket()`` to be able to find the underlying socket.
