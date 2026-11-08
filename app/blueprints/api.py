from flask import Blueprint,request,jsonify,session,current_app
from ..db import get_db
from ..security import rate_limit
from ..services import booking as B,payments as P,notify,ai,dynamic_pricing as DP
bp=Blueprint('api',__name__)
def err(msg,code=400,fields=None):return jsonify(error=str(msg),fields=fields or {}),code
@bp.get('/health')
def health():
    try:get_db().execute('SELECT 1');return jsonify(status='ok')
    except Exception:return jsonify(status='error'),500
@bp.get('/availability')
@rate_limit('avail',60,60)
def availability():
    a=request.args;db=get_db()
    try:rooms_required=int(a.get('rooms',1))
    except (ValueError,TypeError):return err('Enter a valid number of rooms.',400,{'rooms':'Enter a number from 1 to 10.'})
    if not 1<=rooms_required<=10:return err('Choose between 1 and 10 rooms.',400,{'rooms':'Choose between 1 and 10 rooms.'})
    try:rs=B.search(db,a.get('check_in',''),a.get('check_out',''),a.get('adults',1),a.get('children',0),category_id=a.get('category_id') or None,property_id=a.get('property_id') or None)
    except B.Invalid as e:return err(e,400,e.errors)
    inventory=db.execute(f'SELECT COUNT(*) FROM ({B.ROOM_SQL})').fetchone()[0]
    _,tax_configured=B.tax_configuration(db)
    can_accommodate=len(rs)>=rooms_required and tax_configured
    message='' if can_accommodate else 'No bookable rooms have been configured yet. Contact the property for availability.' if not inventory else 'Tax settings need admin confirmation before online booking can be enabled.' if not tax_configured else f'Only {len(rs)} room(s) are available for these dates; {rooms_required} requested.' if rs else 'No rooms are available for those dates.'
    status='not_configured' if not inventory else 'tax_unconfirmed' if not tax_configured else 'published'
    return jsonify(count=len(rs),rooms_required=rooms_required,can_accommodate=can_accommodate,inventory_count=inventory,inventory_status=status,tax_configured=tax_configured,message=message,rooms=[dict(room_id=r['id'],number=r['number'],property=r['pname'],category=r['cat_name'],ac=bool(r['ac']),nights=r['quote']['nights'],price_per_night_paise=r['quote']['avg'],tax_paise=r['quote']['tax'],total_paise=r['quote']['total'],tax_configured=r['quote']['tax_configured'],amenities=r['amenities']) for r in rs])
@bp.get('/price-calendar')
@rate_limit('pricecalendar',60,60)
def price_calendar():
    try:
        days=DP.calendar(get_db(),request.args.get('category_id',''),request.args.get('start',''),request.args.get('months','1'))
        return jsonify(days=days)
    except (ValueError,TypeError) as e:return err(e)
@bp.get('/price-quote')
@rate_limit('pricequote',60,60)
def price_quote():
    try:
        ci,co,_,_=B.validate_stay(request.args.get('check_in',''),request.args.get('check_out',''),1,0)
        db=get_db();q=DP.quote(db,request.args.get('category_id',''),ci,co)
        tax_percent,tax_configured=B.tax_configuration(db)
        q['tax_configured']=tax_configured
        q['tax_paise']=round(q['room_total_paise']*tax_percent/100) if tax_configured else 0
        q['total_paise']=q['room_total_paise']+q['tax_paise']
        return jsonify(q)
    except B.Invalid as e:return err(e,400,e.errors)
    except (ValueError,TypeError) as e:return err(e)
@bp.get('/media')
@rate_limit('media',60,60)
def media_list():
    kind=request.args.get('kind','')
    if kind not in('slide','gallery','room'):return err('Choose a valid media kind.')
    sql='SELECT id,kind,property_id,category_id,title,caption,link_url,path,sort FROM site_media WHERE kind=? AND active=1'
    args=[kind]
    for field in ('property_id','category_id'):
        raw=request.args.get(field)
        if raw:
            try:ident=int(raw)
            except ValueError:return err(f'Invalid {field}.')
            sql+=f' AND {field}=?';args.append(ident)
    rows=[dict(r) for r in get_db().execute(sql+' ORDER BY sort,id',args)]
    return jsonify(items=rows)
@bp.post('/bookings')
@rate_limit('apibook',10,600)
def create_booking():
    j=request.get_json(silent=True) or {};db=get_db()
    if j.get('website'):return err('Rejected',400)
    try:res=B.create_reservation(db,j.get('room_id'),j.get('check_in',''),j.get('check_out',''),j.get('adults',1),j.get('children',0),j.get('name'),j.get('phone'),j.get('email'),source='web',notes=(j.get('notes') or '')[:300])
    except B.Unavailable as e:return err(e,409)
    except B.BookingError as e:return err(e,400,e.errors)
    session['refs']=(session.get('refs',[])+[res['ref']])[-10:];notify.send(db,res['id'],'booking_confirmed')
    return jsonify(reference=res['ref'],status=res['status'],total_paise=res['total_paise']),201
@bp.post('/payments/order')
@rate_limit('payorder',10,600)
def payment_order():
    ref=(request.get_json(silent=True) or {}).get('reference','');db=get_db()
    if ref not in session.get('refs',[]) and session.get('manage')!=ref:return err('Not allowed',403)
    try:return jsonify(P.create_order(db,B.get_res(db,ref=ref)['id']))
    except P.NotConfigured:return err('Online payment is not available right now.',503)
    except (P.PaymentError,B.BookingError) as e:return err(e,400)
@bp.post('/payments/verify')
@rate_limit('payverify',20,600)
def payment_verify():
    j=request.get_json(silent=True) or {};db=get_db()
    try:
        res=P.verify(db,j.get('razorpay_order_id'),j.get('razorpay_payment_id'),j.get('razorpay_signature'));notify.send(db,res['id'],'payment_received')
        return jsonify(status=res['status'],reference=res['ref'])
    except P.PaymentError as e:return err(e,400)
@bp.post('/payments/failed')
@rate_limit('payfail',20,600)
def payment_failed():
    j=request.get_json(silent=True) or {};P.mark_failed(get_db(),j.get('order_id'),str(j.get('reason',''))[:100]);return jsonify(ok=True)
@bp.post('/payments/webhook')
def payment_webhook():
    try:t=P.webhook(get_db(),request.get_data(),request.headers.get('X-Razorpay-Signature',''));return jsonify(ok=True,event=t)
    except P.NotConfigured:return err('Webhook not configured',503)
    except Exception as e:current_app.logger.warning('webhook rejected: %s',type(e).__name__);return err('Rejected',400)
@bp.post('/chat')
@rate_limit('chat',25,60)
def chat():
    j=request.get_json(silent=True) or {};db=get_db()
    out=ai.reply(db,session.get('chat_token') or j.get('token'),j.get('message',''),j.get('language','auto'));session['chat_token']=out['token']
    if out.get('booking'):session['refs']=(session.get('refs',[])+[out['booking']])[-10:]
    return jsonify(reply=out['reply'],status=out.get('status'),booking=out.get('booking'),link=out.get('link'),unavailable=out.get('unavailable',False))
