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

To support HTTPS proxy, ``wsgiprox`` creates a custom CA, which must be accepted by the client (or it must ignore cert verification as with the ``-k`` option in CURL)

   
