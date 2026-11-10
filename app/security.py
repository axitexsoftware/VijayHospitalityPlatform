import secrets,time,hmac,functools,collections
from datetime import timedelta
from flask import session,request,abort,g,jsonify,redirect,url_for,current_app,make_response
from werkzeug.security import generate_password_hash,check_password_hash
from .db import get_db
from .util import now,now_s
PERMS={'owner':{'*'},'admin':{'*'},'receptionist':{'dashboard','bookings','reception','payments','conversations'}}
def csrf_token():
    if 'csrf' not in session:session['csrf']=secrets.token_urlsafe(32)
    return session['csrf']
def check_csrf():
    if request.method in('POST','PUT','PATCH','DELETE') and request.endpoint not in current_app.config.get('CSRF_EXEMPT',()):
        t=request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not t or not hmac.compare_digest(t,session.get('csrf','')):abort(400,'Invalid or missing security token. Reload the page and try again.')
_hits=collections.defaultdict(collections.deque)
def rate_ok(key,n,window):
    if not current_app.config['RATELIMIT_ENABLED']:return True
    t=time.time();q=_hits[key]
    while q and q[0]<t-window:q.popleft()
    if len(q)>=n:return False
    q.append(t);return True
def rate_limit(name,n,window):
    def deco(f):
        @functools.wraps(f)
        def w(*a,**k):
            if not rate_ok(f'{name}:{request.remote_addr}',n,window):
                if request.path.startswith('/api/'):return jsonify(error='Too many requests. Please wait a moment.'),429
                abort(429)
            return f(*a,**k)
        return w
    return deco
def hash_password(p):return generate_password_hash(p)
def password_problem(p):
    if len(p)<10:return 'Use at least 10 characters.'
    if p.lower()==p or not any(c.isdigit() for c in p):return 'Use upper and lower case letters and at least one number.'
def load_user():
    g.user=None;uid=session.get('uid')
    if uid:
        u=get_db().execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone()
        if u:g.user=dict(u)
        else:session.clear()
def has_perm(role,perm):p=PERMS.get(role,set());return '*' in p or perm in p
def login(email,password):
    db=get_db();u=db.execute('SELECT * FROM users WHERE email=? AND active=1',((email or '').strip().lower(),)).fetchone()
    if u and u['locked_until'] and u['locked_until']>now_s():return None,'Too many failed attempts. Try again in 15 minutes.'
    if not u or not check_password_hash(u['password_hash'],password or ''):
        if u:
            n=u['failed_logins']+1;lock=(now()+timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S') if n>=5 else None
            db.execute('UPDATE users SET failed_logins=?,locked_until=? WHERE id=?',(0 if lock else n,lock,u['id']))
        return None,'Incorrect email or password.'
    db.execute('UPDATE users SET failed_logins=0,locked_until=NULL WHERE id=?',(u['id'],));session.clear();session['uid']=u['id'];session.permanent=True;csrf_token();return dict(u),None
def require(perm=None):
    def deco(f):
        @functools.wraps(f)
        def w(*a,**k):
            if not g.user:
                if request.path.startswith('/api/'):return jsonify(error='Sign in required.'),401
                return redirect(url_for('admin.login',next=request.path))
            if perm and not has_perm(g.user['role'],perm):abort(403)
            return f(*a,**k)
        return w
    return deco
def add_headers(r):
    r.headers.setdefault('X-Content-Type-Options','nosniff');r.headers.setdefault('X-Frame-Options','DENY');r.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
    r.headers.setdefault('Permissions-Policy','geolocation=(),camera=(),microphone=()')
    r.headers.setdefault('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' https://checkout.razorpay.com; frame-src https://api.razorpay.com; connect-src 'self' https://lumberjack.razorpay.com; form-action 'self'; base-uri 'self'")
    if current_app.config['ENV']=='production':r.headers.setdefault('Strict-Transport-Security','max-age=31536000')
    if request.path.startswith(('/admin','/api')):r.headers['Cache-Control']='no-store'
    return r
