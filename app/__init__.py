import os,logging
from logging.handlers import RotatingFileHandler
from flask import Flask,Request,render_template,request,g,session,current_app
from . import config as cfgmod,db as dbm,security
from . import configuration as hotel_config_module
from .util import inr,d,today
class HospitalityRequest(Request):
    @property
    def max_content_length(self):
        if self.path=='/admin/media/upload':return 65*1024*1024
        if self.path.startswith('/admin/rooms/') and self.path.endswith('/panorama'):return 9*1024*1024
        return current_app.config.get('MAX_CONTENT_LENGTH')
def create_app(**over):
    app=Flask(__name__,instance_relative_config=False);app.request_class=HospitalityRequest;app.config.update(cfgmod.load(**over));app.config['CSRF_EXEMPT']={'api.payment_webhook'}
    app.permanent_session_lifetime=__import__('datetime').timedelta(hours=8)
    if os.environ.get('TRUSTED_PROXY')=='1':
        from werkzeug.middleware.proxy_fix import ProxyFix;app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
    os.makedirs(app.config['UPLOAD_DIR'],exist_ok=True);dbm.migrate(app.config['DATABASE'])
    if app.config['ENV']=='production' or os.environ.get('LOG_FILE'):
        os.makedirs('instance',exist_ok=True);h=RotatingFileHandler(os.environ.get('LOG_FILE','instance/app.log'),maxBytes=2_000_000,backupCount=5);h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));app.logger.addHandler(h);app.logger.setLevel(logging.INFO)
    app.teardown_appcontext(dbm.close_db);app.before_request(security.load_user);app.before_request(security.check_csrf);app.after_request(security.add_headers)
    import json;mf=os.path.join(app.static_folder,'img','manifest.json');app.jinja_env.globals['IMG']=(json.load(open(mf,encoding='utf-8')) if os.path.exists(mf) else {})
    app.jinja_env.filters['inr']=inr;app.jinja_env.filters['dmy']=lambda s:d(s[:10]).strftime('%d %b %Y') if s else ''
    app.jinja_env.filters['label']=lambda s:(s or '').replace('_',' ').title()
    app.jinja_env.globals.update(csrf_token=security.csrf_token,has_perm=security.has_perm,site_url=app.config['SITE_URL'])
    from .blueprints import public,admin,api
    app.register_blueprint(public.bp);app.register_blueprint(admin.bp,url_prefix='/admin');app.register_blueprint(api.bp,url_prefix='/api')
    @app.context_processor
    def inject():
        from .payments_ui import razorpay_ready
        db=dbm.get_db();hotel_config=hotel_config_module.load(db,current_app.config)
        site_property=db.execute('SELECT * FROM properties WHERE active=1 ORDER BY id LIMIT 1').fetchone()
        page_key={
            'public.properties':'properties','public.rooms':'rooms','public.room':'room','public.search':'search',
            'public.book':'booking','public.booking':'booking','public.lookup':'lookup','public.events':'events',
            'public.offers':'offers','public.gallery':'gallery','public.facilities':'facilities','public.about':'about',
            'public.contact':'contact','public.faq':'faq','public.privacy':'privacy',
        }.get(request.endpoint)
        page_metadata=hotel_config['pages']['metadata'].get(page_key,{}) if page_key else {}
        visible_offers=[offer for offer in hotel_config['pages']['offers']
            if offer.get('active') and (not offer.get('end_date') or offer['end_date']>=today().isoformat())]
        return dict(user=getattr(g,'user',None),razorpay_ready=razorpay_ready(),today=today().isoformat(),
            tomorrow=(today()+__import__('datetime').timedelta(days=1)).isoformat(),hotel_config=hotel_config,
            site_url=hotel_config['website']['site_url'],site_property=site_property,page_metadata=page_metadata,
            visible_offers=visible_offers)
    @app.errorhandler(404)
    def e404(e):return render_template('public/error.html',code=404,msg='Page not found'),404
    @app.errorhandler(403)
    def e403(e):return render_template('public/error.html',code=403,msg='You do not have permission to view this page'),403
    @app.errorhandler(429)
    def e429(e):return render_template('public/error.html',code=429,msg='Too many requests. Please wait a moment and try again.'),429
    @app.errorhandler(400)
    def e400(e):return render_template('public/error.html',code=400,msg=getattr(e,'description','Bad request')),400
    @app.errorhandler(500)
    def e500(e):app.logger.exception('Unhandled error');return render_template('public/error.html',code=500,msg='Something went wrong on our side. Please try again.'),500
    return app
