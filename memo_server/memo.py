from http import HTTPStatus
import random
import requests
import json
import urllib
import secrets
import redis

from flask import abort, Flask, render_template, redirect, request

app = Flask(__name__)

naver_client_id = 'orE2599XmxmLhfGdTk3F'
naver_client_secret = '3IV1B5VDUf' 
naver_redirect_uri = 'http://memo-lb-1076248375.ap-northeast-2.elb.amazonaws.com/memo/auth'  # 또는 AWS 로드밸런서 DNS 주소

# Redis 클라이언트 초기화
redis_client = redis.StrictRedis(host='172.31.0.252', port=6379, db=0, decode_responses=True)

# 사용자 세션 관리를 위한 딕셔너리 (Redis에 저장)
# 세션 관련 키를 관리하기 위한
SESSION_PREFIX = 'session:'
USER_PREFIX = 'user:'
MEMO_PREFIX = 'memo:'

@app.route('/')
def home():
    # 세션 쿠키를 통해 로그인 여부 확인
    userId = request.cookies.get('userId', default=None)
    name = None

    ####################################################
    # userId로부터 Redis에서 사용자 이름을 얻어오는 코드
    if userId:
        real_user_id = redis_client.get(f"{SESSION_PREFIX}{userId}")
        if real_user_id:
            name = redis_client.hget(f"{USER_PREFIX}{real_user_id}", 'name')
    ####################################################

    # index.html을 렌더링하면서 사용자 이름 전달
    return render_template('index.html', name=name)

@app.route('/login')
def onLogin():
    # 로그인 요청 시 네이버 로그인 페이지로 리다이렉트
    state = secrets.token_urlsafe(16)  # CSRF 방지를 위한 state 값 생성
    params = {
        'response_type': 'code',
        'client_id': naver_client_id,
        'redirect_uri': naver_redirect_uri,
        'state': state
    }
    urlencoded = urllib.parse.urlencode(params)
    url = f'https://nid.naver.com/oauth2.0/authorize?{urlencoded}'
    return redirect(url)

@app.route('/auth')
def onOAuthAuthorizationCodeRedirected():
    authorization_code = request.args.get('code')
    state = request.args.get('state')
    if not authorization_code:
        return 'Authorization code not found', HTTPStatus.BAD_REQUEST

    # 2. authorization code로부터 access token을 얻어낸다.
    token_url = 'https://nid.naver.com/oauth2.0/token'
    params = {
        'grant_type': 'authorization_code',
        'client_id': naver_client_id,
        'client_secret': naver_client_secret,
        'code': authorization_code,
        'state': state
    }
    token_response = requests.post(token_url, params=params)
    if token_response.status_code != 200:
        return 'Failed to get access token', HTTPStatus.BAD_REQUEST
    token_data = token_response.json()
    access_token = token_data.get('access_token')
    if not access_token:
        return 'Access token not found', HTTPStatus.BAD_REQUEST

    # 3. access token을 이용하여 프로필 정보를 얻는다.
    profile_url = 'https://openapi.naver.com/v1/nid/me'
    headers = {'Authorization': f'Bearer {access_token}'}
    profile_response = requests.get(profile_url, headers=headers)
    if profile_response.status_code != 200:
        return 'Failed to get user profile', HTTPStatus.BAD_REQUEST
    profile_data = profile_response.json()
    if profile_data.get('resultcode') != '00':
        return 'Failed to get valid user profile', HTTPStatus.BAD_REQUEST
    response_data = profile_data.get('response', {})
    user_id = response_data.get('id')
    user_name = response_data.get('name')
    if not user_id or not user_name:
        return 'User ID or name not found', HTTPStatus.BAD_REQUEST

    # 4. user_id와 name을 Redis에 저장한다.
    # 사용자 정보가 이미 존재하는지 확인
    user_key = f"{USER_PREFIX}{user_id}"
    if not redis_client.exists(user_key):
        # 새로운 사용자이면 Redis에 추가
        redis_client.hset(user_key, mapping={'name': user_name})

    # 5. 첫 페이지로 redirect하면서 로그인 쿠키 설정
    userId_cookie_value = secrets.token_urlsafe(16)
    session_key = f"{SESSION_PREFIX}{userId_cookie_value}"
    redis_client.set(session_key, user_id)
    # 세션의 유효기간 설정 (예: 1시간)
    redis_client.expire(session_key, 3600)

    response = redirect('/memo')
    response.set_cookie('userId', userId_cookie_value)
    return response

@app.route('/memo', methods=['GET'])
def get_memos():
    # 로그인이 안되어 있다면 첫 페이지로 redirect
    userId = request.cookies.get('userId', default=None)
    if not userId:
        return redirect('/')

    session_key = f"{SESSION_PREFIX}{userId}"
    real_user_id = redis_client.get(session_key)
    if not real_user_id:
        return redirect('/')

    # Redis에서 해당 userId의 메모를 가져온다.
    memo_key = f"{MEMO_PREFIX}{real_user_id}"
    memos = redis_client.lrange(memo_key, 0, -1)
    result = [{'text': memo} for memo in memos]  # 리스트를 객체로 변환

    # 메모 목록을 JSON 형태로 반환
    return {'memos': result}

@app.route('/memo', methods=['POST'])
def post_new_memo():
    # 로그인이 안되어 있다면 첫 페이지로 redirect
    userId = request.cookies.get('userId', default=None)
    if not userId:
        return redirect('/')

    session_key = f"{SESSION_PREFIX}{userId}"
    real_user_id = redis_client.get(session_key)
    if not real_user_id:
        return redirect('/')

    # 요청이 JSON인지 확인
    if not request.is_json:
        abort(HTTPStatus.BAD_REQUEST)

    # 메모 내용 추출
    data = request.get_json()
    memo_text = data.get('text')
    if not memo_text:
        abort(HTTPStatus.BAD_REQUEST)

    # Redis에 메모 저장
    memo_key = f"{MEMO_PREFIX}{real_user_id}"
    redis_client.rpush(memo_key, memo_text)

    return '', HTTPStatus.OK

if __name__ == '__main__':
    app.run('0.0.0.0', port=8000, debug=True)
