# -*- coding: utf-8 -*-
import unittest
from tests import TestMixin

import time
import hmac

try:
  import json
except ImportError:
  import simplejson as json

from webapp2 import WSGIApplication, Route, RequestHandler, cached_property
from webob import Request
import httplib2

from simpleauth import SimpleAuthHandler

#
# test subjects
#

class NotSupportedException(Exception):
  """Provider not supported"""
  pass

class DummyAuthError(Exception):
  """Generic auth error for Dummy handler"""
  pass

class OAuth1ClientMock(object):
  def __init__(self, **kwargs):
    super(OAuth1ClientMock, self).__init__()
    self._response_content = kwargs.pop('content', '')
    self._response_dict = kwargs
    
  def request(self, url, method):
    return (httplib2.Response(self._response_dict), self._response_content)
  
class DummyAuthHandler(RequestHandler, SimpleAuthHandler):
  SESSION_MOCK = {}

  def __init__(self, *args, **kwargs):
    super(DummyAuthHandler, self).__init__(*args, **kwargs)
    self.PROVIDERS.update({
      'dummy_oauth1': ('oauth1', {
        'request': 'https://dummy/oauth1_rtoken',
        'auth'  : 'https://dummy/oauth1_auth?{0}'
      }, 'https://dummy/oauth1_atoken'),
      'dummy_oauth2': ('oauth2', 'https://dummy/oauth2?{0}', 
                                 'https://dummy/oauth2_token'),
    })
    
    self.TOKEN_RESPONSE_PARSERS.update({
      'dummy_oauth1': '_json_parser',
      'dummy_oauth2': '_json_parser'
    })

    self.session = self.SESSION_MOCK.copy()
    
  def dispatch(self):
    RequestHandler.dispatch(self)
    self.response.headers['SessionMock'] = json.dumps(self.session)

  def _on_signin(self, user_data, auth_info, provider):
    self.redirect('/logged_in?provider=%s' % provider)
    
  def _callback_uri_for(self, provider):
    return '/auth/%s/callback' % provider
    
  def _get_consumer_info_for(self, provider):
    return {
      'dummy_oauth1': ('cons_key', 'cons_secret'),
      'dummy_oauth2': ('cl_id', 'cl_secret', 'a_scope'),
    }.get(provider, (None, None))
    
  def _provider_not_supported(self, provider):
    raise NotSupportedException(provider)

  def _auth_error(self, provider, msg=None):
    raise DummyAuthError(
      "Couldn't authenticate against %s: %s" % (provider, msg))

  # Mocks

  def _oauth1_client(self, token=None, 
                           consumer_key=None, consumer_secret=None):
    """OAuth1 client mock"""
    return OAuth1ClientMock(
      content='{"oauth_token": "some oauth1 request token"}')
    
  def _get_dummy_oauth1_user_info(self, auth_info, key=None, secret=None):
    return 'an oauth1 user info'

  def _get_dummy_oauth2_user_info(self, auth_info, key=None, secret=None):
    return 'oauth2 mock user info'

  def _generate_csrf_token(self, secret):
    # We set provided secret as a session token
    # and 'csrf-token-digest' as the state param during tests
    return (secret, 'csrf-token-digest')

  def _validate_csrf_token(self, secret, token, digest):
    # During the tests digest should always be 'csrf-token-digest'
    # and token == secret (see _generate_csrf_token() above)
    return token == secret and digest == 'csrf-token-digest'


#
# test suite
#

class SimpleAuthHandlerTestCase(TestMixin, unittest.TestCase):
  def setUp(self):
    super(SimpleAuthHandlerTestCase, self).setUp()
    # set back to default value
    DummyAuthHandler.OAUTH2_CSRF_STATE = SimpleAuthHandler.OAUTH2_CSRF_STATE
    DummyAuthHandler.SESSION_MOCK = {
      'req_token': {
        'oauth_token':'oauth1 token', 
        'oauth_token_secret':'a secret' 
      }
    }

    # handler instance for some of the tests
    self.handler = DummyAuthHandler()

    # Dummy app to run the tests against
    routes = [
      Route('/auth/<provider>', handler=DummyAuthHandler, 
        handler_method='_simple_auth'),
      Route('/auth/<provider>/callback', handler=DummyAuthHandler, 
        handler_method='_auth_callback') ]
    self.app = WSGIApplication(routes, debug=True)
    
  def test_providers_dict(self):
    for p in ('google', 'twitter', 'linkedin', 'openid', 
              'facebook', 'windows_live'):
      self.assertIn(self.handler.PROVIDERS[p][0], 
                   ['oauth2', 'oauth1', 'openid'])
    
  def test_token_parsers_dict(self):
    for p in ('google', 'windows_live', 'facebook', 'linkedin', 'twitter'):
      parser = self.handler.TOKEN_RESPONSE_PARSERS['google']
      self.assertIsNotNone(parser)
      self.assertTrue(hasattr(self.handler, parser))

  def test_not_supported_provider(self):
    self.expectErrors()
    with self.assertRaises(NotSupportedException):
      self.handler._simple_auth()
      
    with self.assertRaises(NotSupportedException):
      self.handler._simple_auth('whatever')

    resp = self.app.get_response('/auth/xxx')
    self.assertEqual(resp.status_int, 500)
    self.assertRegexpMatches(resp.body, 'NotSupportedException: xxx')

  def test_openid_init(self):
    resp = self.app.get_response('/auth/openid?identity_url=some.oid.provider.com')
    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 
      'https://www.google.com/accounts/Login?'
      'continue=http%3A//testbed.example.com/auth/openid/callback')
        
  def test_openid_callback_success(self):
    self.login_user('dude@example.org', 123, 
      federated_identity='http://dude.example.org', 
      federated_provider='example.org')

    resp = self.app.get_response('/auth/openid/callback')
    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 
      'http://localhost/logged_in?provider=openid')
    
    uinfo, auth = self.handler._openid_callback()
    self.assertEqual(auth, {'provider': 'example.org'})
    self.assertEqual(uinfo, {
      'id': 'http://dude.example.org', 
      'nickname': 'http://dude.example.org',
      'email': 'dude@example.org'
    })
  
  def test_openid_callback_failure(self):
    self.expectErrors()
    resp = self.app.get_response('/auth/openid/callback')
    self.assertEqual(resp.status_int, 500)
    self.assertRegexpMatches(resp.body, 'DummyAuthError')

  def test_oauth1_init(self):
    resp = self.app.get_response('/auth/dummy_oauth1')
    
    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 
      'https://dummy/oauth1_auth?'
      'oauth_token=some+oauth1+request+token&'
      'oauth_callback=%2Fauth%2Fdummy_oauth1%2Fcallback')

  def test_oauth1_callback_success(self):
    url = '/auth/dummy_oauth1/callback?oauth_verifier=a-verifier-token'
    resp = self.app.get_response(url)
    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 
      'http://localhost/logged_in?provider=dummy_oauth1')
        
  def test_oauth1_callback_failure(self):
    self.expectErrors()
    resp = self.app.get_response('/auth/dummy_oauth1/callback')
    self.assertEqual(resp.status_int, 500)
    self.assertRegexpMatches(resp.body, 'No OAuth verifier was provided')
      
  def test_query_string_parser(self):
    parsed = self.handler._query_string_parser('param1=val1&param2=val2')
    self.assertEqual(parsed, {'param1':'val1', 'param2':'val2'})

  #
  # CSRF tests
  # 
  
  def test_csrf_default(self):
    self.assertFalse(SimpleAuthHandler.OAUTH2_CSRF_STATE)

  def test_csrf_oauth2_init(self):
    DummyAuthHandler.OAUTH2_CSRF_STATE = True
    resp = self.app.get_response('/auth/dummy_oauth2')

    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 'https://dummy/oauth2?'
      'scope=a_scope&'
      'state=csrf-token-digest&'
      'redirect_uri=%2Fauth%2Fdummy_oauth2%2Fcallback&'
      'response_type=code&'
      'client_id=cl_id')

    session = json.loads(resp.headers['SessionMock'])
    session_token = session.get(DummyAuthHandler.OAUTH2_CSRF_SESSION_PARAM, '')
    self.assertEqual(session_token, 'cl_secret')

  def test_csrf_oauth2_callback_success(self):
    DummyAuthHandler.OAUTH2_CSRF_STATE = True
    DummyAuthHandler.SESSION_MOCK = {
      DummyAuthHandler.OAUTH2_CSRF_SESSION_PARAM: 'cl_secret'
    }

    fetch_resp = json.dumps({
      "access_token":"1/fFAGRNJru1FTz70BzhT3Zg",
      "expires_in": 3600,
      "token_type":"Bearer"
      })
    self.set_urlfetch_response('https://dummy/oauth2_token', 
      content=fetch_resp)

    resp = self.app.get_response('/auth/dummy_oauth2/callback?'
      'code=auth-code&state=csrf-token-digest')

    self.assertEqual(resp.status_int, 302)
    self.assertEqual(resp.headers['Location'], 
      'http://localhost/logged_in?provider=dummy_oauth2')

    session = json.loads(resp.headers['SessionMock'])
    self.assertFalse(DummyAuthHandler.OAUTH2_CSRF_SESSION_PARAM in session)

  def test_csrf_oauth2_invalid_session_token(self):
    self.expectErrors()
    DummyAuthHandler.OAUTH2_CSRF_STATE = True
    DummyAuthHandler.SESSION_MOCK = {
      DummyAuthHandler.OAUTH2_CSRF_SESSION_PARAM: 'an-invalid-token'
    }

    resp = self.app.get_response('/auth/dummy_oauth2/callback?'
      'code=auth-code&state=csrf-token-digest')

    self.assertEqual(resp.status_int, 500)
    self.assertRegexpMatches(resp.body, 'State parameter is not valid')

  def test_csrf_oauth2_invalid_state(self):
    self.expectErrors()
    DummyAuthHandler.OAUTH2_CSRF_STATE = True
    DummyAuthHandler.SESSION_MOCK = {
      DummyAuthHandler.OAUTH2_CSRF_SESSION_PARAM: 'cl_secret'
    }

    resp = self.app.get_response('/auth/dummy_oauth2/callback?'
      'code=auth-code&state=invalid-state')

    self.assertEqual(resp.status_int, 500)
    self.assertRegexpMatches(resp.body, 'State parameter is not valid')

  def test_csrf_token_generation(self):
    handler = SimpleAuthHandler()
    token, digest = handler._generate_csrf_token('a-secret')
    self.assertNotEqual(token, digest) # :)

    timestamp = long(token.split(DummyAuthHandler._CSRF_DELIMITER)[-1])
    # token generation can't really take more than 1 sec
    self.assertFalse(long(time.time()) - timestamp > 1)

  def test_csrf_validation(self):
    self.expectErrors()
    h = SimpleAuthHandler()
    token, digest = h._generate_csrf_token('a-secret')

    self.assertTrue(h._validate_csrf_token('a-secret', token, digest))
    self.assertFalse(h._validate_csrf_token('invalid-secret', token, digest))
    self.assertFalse(h._validate_csrf_token('a-secret', 'invalid', digest))
    self.assertFalse(h._validate_csrf_token('a-secret', token, 'invalid'))

    timeout = long(time.time()) - h._CSRF_TOKEN_TIMEOUT - 1
    token, digest = h._generate_csrf_token('a-secret', _time=timeout)
    self.assertFalse(h._validate_csrf_token('a-secret', token, digest))


if __name__ == '__main__':
  unittest.main()
