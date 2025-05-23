from __future__ import with_statement
import os

import src.LitPyWeb as LitPyWeb
import sys
import unittest
import wsgiref
import wsgiref.util
import wsgiref.validate
import warnings

import mimetypes
import uuid

from src.LitPyWeb import tob, BytesIO


def warn(msg):
    sys.stderr.write('WARNING: %s\n' % msg.strip())


def tobs(data):
    ''' Transforms bytes or unicode into a byte stream. '''
    return BytesIO(tob(data))


class chdir(object):
    def __init__(self, dir):
        if os.path.isfile(dir):
            dir = os.path.dirname(dir)
        self.wd = os.path.abspath(dir)
        self.old = os.path.abspath('.')

    def __enter__(self):
        os.chdir(self.wd)

    def __exit__(self, exc_type, exc_val, tb):
        os.chdir(self.old)


class assertWarn(object):
    def __init__(self, text):
        self.searchtext = text

    def __call__(self, func):
        def wrapper(*a, **ka):
            with warnings.catch_warnings(record=True) as wr:
                warnings.simplefilter("always")
                out = func(*a, **ka)
            messages = [repr(w.message) for w in wr]
            for msg in messages:
                if self.searchtext in msg:
                    return out
            raise AssertionError("Could not find phrase %r in any warning messaged: %r" % (self.searchtext, messages))
        return wrapper

def api(introduced, deprecated=None, removed=None):
    current    = tuple(map(int, LitPyWeb.__version__.split('-')[0].split('.')))
    introduced = tuple(map(int, introduced.split('.')))
    deprecated = tuple(map(int, deprecated.split('.'))) if deprecated else (99,99)
    removed    = tuple(map(int, removed.split('.')))    if removed    else (99,100)
    assert introduced < deprecated < removed

    def decorator(func):
        if   current < introduced:
            return None
        elif current < deprecated:
            return func
        elif current < removed:
            func.__doc__ = '(deprecated) ' + (func.__doc__ or '')
            return assertWarn('DeprecationWarning')(func)
        else:
            return None
    return decorator


def wsgistr(s):
    return s.encode('utf8').decode('latin1')

class ServerTestBase(unittest.TestCase):
    def setUp(self):
        ''' Create a new LitPyWeb app set it as default_app '''
        self.port = 8080
        self.host = 'localhost'
        self.app = LitPyWeb.app.push()
        self.wsgiapp = wsgiref.validate.validator(self.app)

    def urlopen(self, path, method='GET', post='', env=None, crash=None):
        result = {'code':0, 'status':'error', 'header':{}, 'body':tob('')}
        def start_response(status, header, exc_info=None):
            if crash == "start_response":
                raise RuntimeError("Unittest requested crash in start_response")
            result['code'] = int(status.split()[0])
            result['status'] = status.split(None, 1)[-1]
            for name, value in header:
                name = name.title()
                if name in result['header']:
                    result['header'][name] += ', ' + value
                else:
                    result['header'][name] = value
        env = env if env else {}
        wsgiref.util.setup_testing_defaults(env)
        env['REQUEST_METHOD'] = wsgistr(method.upper().strip())
        env['PATH_INFO'] = wsgistr(path)
        env['QUERY_STRING'] = wsgistr('')
        if post:
            env['REQUEST_METHOD'] = 'POST'
            env['CONTENT_LENGTH'] = str(len(tob(post)))
            env['wsgi.input'].write(tob(post))
            env['wsgi.input'].seek(0)
        response = self.wsgiapp(env, start_response)
        try:
            for part in response:
                try:
                    result['body'] += part
                except TypeError:
                    raise TypeError('WSGI app yielded non-byte object %s', type(part))
        finally:
            LitPyWeb._try_close(response)
        return result

    def postmultipart(self, path, fields, files):
        env = multipart_environ(fields, files)
        return self.urlopen(path, method='POST', env=env)

    def tearDown(self):
        LitPyWeb.app.pop()

    def assertStatus(self, code, route='/', **kargs):
        self.assertEqual(code, self.urlopen(route, **kargs)['code'])

    def assertBody(self, body, route='/', **kargs):
        self.assertEqual(tob(body), self.urlopen(route, **kargs)['body'])

    def assertInBody(self, body, route='/', **kargs):
        result = self.urlopen(route, **kargs)['body']
        if tob(body) not in result:
            self.fail('The search pattern "%s" is not included in body:\n%s' % (body, result))

    def assertHeader(self, name, value, route='/', **kargs):
        self.assertEqual(value, self.urlopen(route, **kargs)['header'].get(name))

    def assertHeaderAny(self, name, route='/', **kargs):
        self.assertTrue(self.urlopen(route, **kargs)['header'].get(name, None))

    def assertInError(self, search, route='/', **kargs):
        LitPyWeb.request.environ['wsgi.errors'].errors.seek(0)
        err = LitPyWeb.request.environ['wsgi.errors'].errors.read()
        if search not in err:
            self.fail('The search pattern "%s" is not included in wsgi.error: %s' % (search, err))

def multipart_environ(fields, files):
    boundary = 'lowerUPPER-1234'
    env = {'REQUEST_METHOD':'POST',
           'CONTENT_TYPE':  'multipart/form-data; boundary='+boundary}
    wsgiref.util.setup_testing_defaults(env)
    boundary = '--' + boundary 
    body = ''
    for name, value in fields:
        body += boundary + '\r\n'
        body += 'Content-Disposition: form-data; name="%s"\r\n\r\n' % name
        body += value + '\r\n'
    for name, filename, content in files:
        mimetype = str(mimetypes.guess_type(filename)[0]) or 'application/octet-stream'
        body += boundary + '\r\n'
        body += 'Content-Disposition: file; name="%s"; filename="%s"\r\n' % \
             (name, filename)
        body += 'Content-Type: %s\r\n\r\n' % mimetype
        body += content + '\r\n'
    body += boundary + '--\r\n'
    if isinstance(body, str):
        body = body.encode('utf8')
    env['CONTENT_LENGTH'] = str(len(body))
    env['wsgi.input'].write(body)
    env['wsgi.input'].seek(0)
    return env