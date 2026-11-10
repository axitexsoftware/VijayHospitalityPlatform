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
@bp.get('/rooms/availability')
@rate_limit('avail',60,60)
def availability():
    a=request.args;db=get_db()
    try:rooms_required=int(a.get('rooms',1))
    except (ValueError,TypeError):return err('Enter a valid number of rooms.',400,{'rooms':'Enter a number from 1 to 10.'})
    if not 1<=rooms_required<=10:return err('Choose between 1 and 10 rooms.',400,{'rooms':'Choose between 1 and 10 rooms.'})
    publication=B.inventory_configuration(db)
    if not publication['rates_confirmed'] or not publication['occupancy_confirmed'] or not publication['rooms_confirmed']:
        return jsonify(count=0,rooms_required=rooms_required,can_accommodate=False,inventory_count=0,inventory_status='management_confirmation_pending',rates_confirmed=publication['rates_confirmed'],occupancy_confirmed=publication['occupancy_confirmed'],rooms_confirmed=publication['rooms_confirmed'],tax_configured=B.tax_configuration(db)[1],message='Room numbers, rates and occupancy details are subject to management confirmation. Please contact the property team.',rooms=[])
    if B.inventory_publication_error(db):
        active=db.execute("SELECT COUNT(*) FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id WHERE r.status='active' AND c.active=1 AND p.active=1").fetchone()[0]
        status='not_configured' if not active else 'management_confirmation_pending'
        message='No bookable rooms have been configured yet. Contact the property for availability.' if not active else 'Room details, rates, occupancy and inventory are subject to management confirmation. Contact the property team.'
        return jsonify(count=0,rooms_required=rooms_required,can_accommodate=False,inventory_count=0,inventory_status=status,tax_configured=B.tax_configuration(db)[1],message=message,rooms=[])
    try:rs=B.search(db,a.get('check_in',''),a.get('check_out',''),a.get('adults',1),a.get('children',0),category_id=a.get('category_id') or None,property_id=a.get('property_id') or None,rooms=rooms_required)
    except B.Invalid as e:return err(e,400,e.errors)
    inventory=db.execute(f'SELECT COUNT(*) FROM ({B.ROOM_SQL})').fetchone()[0]
    tax_configured=all(r['quote']['tax_configured'] for r in rs) if rs else B.tax_configuration(db)[1]
    can_accommodate=len(rs)>=rooms_required and tax_configured
    message='' if can_accommodate else 'No bookable rooms have been configured yet. Contact the property for availability.' if not inventory else 'Tax settings need admin confirmation before online booking can be enabled.' if not tax_configured else f'Only {len(rs)} room(s) are available for these dates; {rooms_required} requested.' if rs else 'No rooms are available for those dates.'
    status='not_configured' if not inventory else 'tax_unconfirmed' if not tax_configured else 'published'
    return jsonify(count=len(rs),rooms_required=rooms_required,can_accommodate=can_accommodate,inventory_count=inventory,inventory_status=status,tax_configured=tax_configured,message=message,rooms=[dict(room_id=r['id'],number=r['number'],property=r['pname'],category_id=r['category_id'],category=r['cat_name'],ac=bool(r['ac']),max_adults=r['max_adults'],max_children=r['max_children'],max_occupancy=r['max_occupancy'],nights=r['quote']['nights'],price_per_night_paise=r['quote']['avg'],tax_paise=r['quote']['tax'],total_paise=r['quote']['total'],tax_configured=r['quote']['tax_configured'],amenities=r['amenities']) for r in rs])

@bp.get('/rooms/search')
@rate_limit('avail',60,60)
def room_search():
    a=request.args;db=get_db()
    try:
        rooms_required=int(a.get('rooms',1))
        if not 1<=rooms_required<=10:return err('Choose between 1 and 10 rooms.',400,{'rooms':'Choose between 1 and 10 rooms.'})
        results=B.search(db,a.get('check_in',''),a.get('check_out',''),a.get('adults',1),a.get('children',0),category_id=a.get('category_id') or None,property_id=a.get('property_id') or None,rooms=rooms_required)
    except B.Invalid as e:return err(e,409 if 'subject to management confirmation' in str(e) else 400,e.errors)
    groups={}
    for room in results:
        group=groups.setdefault(room['category_id'],dict(category_id=room['category_id'],name=room['cat_name'],room_type=room['room_type'],property=room['pname'],currency=room['currency'],available_count=0,max_adults=room['max_adults'],max_children=room['max_children'],max_occupancy=room['max_occupancy'],rooms=[]))
        group['available_count']+=1
        group['rooms'].append(dict(room_id=room['id'],number=room['number'],price_per_night_paise=room['quote']['avg'],room_total_paise=room['quote']['room_total'],tax_paise=room['quote']['tax'],total_paise=room['quote']['total'],tax_configured=room['quote']['tax_configured'],nights=room['quote']['nights'],amenities=room['amenities'],photos=room['images']))
    can_accommodate=len(results)>=rooms_required
    return jsonify(check_in=a.get('check_in'),check_out=a.get('check_out'),rooms_required=rooms_required,adults=int(a.get('adults',1)),children=int(a.get('children',0)),available_count=len(results),can_accommodate=can_accommodate,categories=list(groups.values()),message='' if can_accommodate else f'Only {len(results)} room(s) are available for these dates; {rooms_required} requested.')

@bp.get('/room-categories')
def room_categories():
    if B.inventory_publication_error(get_db()):return jsonify(items=[],message='Room details are pending management confirmation.')
    rows=get_db().execute("""SELECT c.id,c.property_id,p.name property,c.name,c.room_type,c.ac,c.description,c.max_adults_approved max_adults,
        c.max_children_approved max_children,c.max_occupancy,c.nightly_rate_paise,c.currency,c.tax_percent,c.photos_status,
        (SELECT COUNT(*) FROM rooms r WHERE r.category_id=c.id AND r.status='active') inventory_count
        FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 AND p.active=1
        AND c.publication_status='published' AND c.details_status='approved' AND c.inventory_status='approved'
        AND c.occupancy_status='approved' AND c.rates_status='approved' AND c.tax_status='approved' ORDER BY p.id,c.name""").fetchall()
    return jsonify(items=[dict(row) for row in rows])

@bp.get('/rooms/<int:category_id>')
def room_category(category_id):
    if B.inventory_publication_error(get_db()):return err('Room details are pending management confirmation.',409)
    row=get_db().execute("""SELECT c.id,c.property_id,p.name property,c.name,c.room_type,c.ac,c.description,c.max_adults_approved max_adults,
        c.max_children_approved max_children,c.max_occupancy,c.nightly_rate_paise,c.currency,c.tax_percent,c.photos_status
        FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.id=? AND c.active=1 AND p.active=1
        AND c.publication_status='published' AND c.details_status='approved' AND c.inventory_status='approved'
        AND c.occupancy_status='approved' AND c.rates_status='approved' AND c.tax_status='approved'""",(category_id,)).fetchone()
    if not row:return err('Room category not found.',404)
    return jsonify(category=dict(row))

@bp.get('/price-calendar')
@rate_limit('pricecalendar',60,60)
def price_calendar():
    try:
        db=get_db()
        if B.inventory_publication_error(db):return err('Room rates are subject to management confirmation.',409,{'rates':'Contact the property team for current rates.'})
        category=db.execute("SELECT id FROM room_categories WHERE id=? AND rates_status='approved' AND publication_status='published'",(request.args.get('category_id',''),)).fetchone()
        if not category:return err('Room rates are subject to management confirmation.',409,{'rates':'Contact the property team for current rates.'})
        days=DP.calendar(db,request.args.get('category_id',''),request.args.get('start',''),request.args.get('months','1'))
        return jsonify(days=days)
    except (ValueError,TypeError) as e:return err(e)
@bp.get('/price-quote')
@bp.get('/rooms/rates')
@rate_limit('pricequote',60,60)
def price_quote():
    try:
        ci,co,_,_=B.validate_stay(request.args.get('check_in',''),request.args.get('check_out',''),1,0)
        db=get_db()
        if B.inventory_publication_error(db):return err('Room rates are subject to management confirmation.',409,{'rates':'Contact the property team for current rates.'})
        category_id=int(request.args.get('category_id',''));category=db.execute("SELECT id FROM room_categories WHERE id=? AND rates_status='approved' AND publication_status='published'",(category_id,)).fetchone()
        if not category:return err('Room rates are subject to management confirmation.',409,{'rates':'Contact the property team for current rates.'})
        q=DP.quote(db,category_id,ci,co)
        tax_percent,tax_configured=B.tax_configuration(db,category_id)
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
    db=get_db()
    sql='SELECT id,kind,property_id,category_id,title,caption,link_url,path,sort FROM site_media WHERE kind=? AND active=1'
    args=[kind]
    if kind in('gallery','slide'):sql+=' AND category_id IS NULL'
    for field in ('property_id','category_id'):
        raw=request.args.get(field)
        if raw:
            try:ident=int(raw)
            except ValueError:return err(f'Invalid {field}.')
            sql+=f' AND {field}=?';args.append(ident)
    if kind=='room':sql+=" AND category_id IN (SELECT id FROM room_categories WHERE photos_status='approved' AND publication_status='published')"
    rows=[dict(r) for r in db.execute(sql+' ORDER BY sort,id',args)]
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
    out=ai.reply(db,session.get('chat_token'),j.get('message',''),j.get('language','auto'));session['chat_token']=out['token']
    if out.get('booking'):session['refs']=(session.get('refs',[])+[out['booking']])[-10:]
    return jsonify(reply=out['reply'],status=out.get('status'),booking=out.get('booking'),link=out.get('link'),unavailable=out.get('unavailable',False))
@bp.get('/chat')
@rate_limit('chat-history',60,60)
def chat_history():
    token=session.get('chat_token')
    if not token:return jsonify(messages=[],status='open')
    db=get_db();conv=db.execute('SELECT id,status FROM ai_conversations WHERE token=?',(token,)).fetchone()
    if not conv:return jsonify(messages=[],status='open')
    rows=db.execute("SELECT role,content,created_at FROM ai_messages WHERE conversation_id=? AND role IN('user','assistant') AND content IS NOT NULL AND content!='' ORDER BY id DESC LIMIT 40",(conv['id'],)).fetchall()
    return jsonify(messages=[dict(role=r['role'],content=r['content'],created_at=r['created_at']) for r in reversed(rows)],status=conv['status'])
