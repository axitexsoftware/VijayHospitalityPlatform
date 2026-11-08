import datetime as dtm
from flask import Blueprint,render_template,request,redirect,url_for,session,abort,flash,Response,current_app
from ..db import get_db,setting
from ..security import rate_limit,rate_ok
from ..util import today,now_s,d
from ..services import booking as B,payments as P,notify,dynamic_pricing as DP
bp=Blueprint('public',__name__)
GALLERY=[('home','Vijay Utsav Hall hotel building with the paved lawn in front'),('hotel-night','Vijay Utsav Hall hotel lit up at night'),('hotel-decor','Hotel entrance decorated for an event'),('room-ac','Air-conditioned guest room with double bed'),('room-b','Guest room with double bed and cove lighting'),('room-c','Spacious guest room'),('room-d','Guest room with wardrobe'),('room-102','Doorway of room 102'),
 ('garden-gate','Vijay Garden entrance arch'),('lawn-stage','Banquet lawn with decorated stage'),('event-stage','Event stage and open lawn'),('garden-stage','Landscaped garden with stage'),('floral','Floral entrance arch'),('walkway','Garden walkway with arches')]
FACILITIES=[
    dict(id='comfort',eyebrow='Accommodation details',title='Room options and amenities',description='Room categories, guest limits, rates and amenities vary by listing. Check the current room details before choosing.',items=['Published room categories and rates','Amenities shown on each room listing','Guest limits shown in room details']),
    dict(id='arrival',eyebrow='Plan your arrival',title='Arrival and access arrangements',description='Confirm arrival times, parking and accessibility arrangements with the property before travelling.',items=['Check-in and check-out times','Parking availability and arrangements','Accessibility and luggage assistance']),
    dict(id='support',eyebrow='Guest support',title='Booking and guest assistance',description='The website shows current booking options where they have been configured. Contact the property about anything not listed.',items=['Contact details for each property','Payment options shown during booking','Changes, cancellations and refund terms']),
    dict(id='events',eyebrow='Weddings and celebrations',title='Plan an event enquiry',description='Share your event requirements with the property team. An enquiry is not a confirmed event booking.',items=['Weddings and marriage functions','Receptions, engagements and family celebrations','Dates, capacity, catering and prices confirmed directly'])]
FAQ=[('What are the check-in and check-out times?','Please use the times shown in your booking details or confirm them with the property before arrival.'),('Which room types are available?','Room categories and rates shown on the website come from the property listings. Check your dates for current availability; contact the team if the room type you need is not shown.'),('How do I book a room?','Search your dates and guest count, choose an available room, review the total and submit your details. A room is confirmed only when the website displays a booking confirmation.'),('Which payment methods are accepted?','The payment options currently available are shown during booking. Contact the property to confirm any other payment arrangements.'),('Can I change or cancel my booking?','Use Manage booking with your booking reference and mobile number. Review the terms shown with your booking, or contact the property if you need help.'),('Can I book a wedding or event online?','The website accepts event enquiries, not confirmed event bookings. Send your preferred date, event type and guest estimate; the property team will confirm availability, capacity, arrangements and prices.'),('Which facilities are available?','Facilities and amenities can vary by room and property. Review the specific room listing and confirm any important requirement directly with the property.'),('Where is Vijay Utsav Hall?','The property address and directions are listed on the Properties and Contact pages. Please confirm the best approach route with the property if needed.')]
def props():return get_db().execute('SELECT * FROM properties WHERE active=1 ORDER BY id').fetchall()
def cats():
    db=get_db();out=[]
    for row in db.execute("SELECT c.*,p.name pname,(SELECT COUNT(*) FROM rooms r WHERE r.category_id=c.id AND r.status='active') rooms FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 AND p.active=1 AND c.base_price_paise>0 ORDER BY p.id,c.ac DESC"):
        c=dict(row);c['display_price_paise']=DP.nightly(db,c['id'],today().isoformat())['price_paise'];out.append(c)
    return out
def category_previews():
    return [dict(r) for r in get_db().execute("SELECT c.id,c.name,c.ac,c.description,c.image,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 AND p.active=1 ORDER BY p.id,c.ac DESC,c.name")]
def category_media(db,category_id):
    return [dict(r) for r in db.execute("SELECT path,title,caption FROM site_media WHERE kind='room' AND category_id=? AND active=1 ORDER BY sort,id",(category_id,))]
def media_items(db,kind,property_id=None,category_id=None):
    sql='SELECT * FROM site_media WHERE kind=? AND active=1';args=[kind]
    if property_id is not None:sql+=' AND property_id=?';args.append(property_id)
    if category_id is not None:sql+=' AND category_id=?';args.append(category_id)
    return [dict(r) for r in db.execute(sql+' ORDER BY sort,id',args)]
def hotel_ld(ps):return {'@context':'https://schema.org','@graph':[{'@type':'Hotel','name':p['name'],'description':p['description'],'address':{'@type':'PostalAddress','streetAddress':p['address'],'addressLocality':'Manpur, Gaya','addressRegion':'Bihar','addressCountry':'IN'},'url':current_app.config['SITE_URL']} for p in ps]}
def can_view(ref):return ref in session.get('refs',[]) or session.get('manage')==ref
@bp.get('/')
def home():
    db=get_db();ps=props()
    categories=cats()
    return render_template('public/home.html',ld=hotel_ld(ps),props=ps,cats=categories,slides=media_items(db,'slide'),
        category_media={c['id']:category_media(db,c['id']) for c in categories},room_previews=category_previews(),today=today().isoformat(),tomorrow=(today()+dtm.timedelta(days=1)).isoformat())
@bp.get('/properties')
def properties():return render_template('public/properties.html',props=props())
@bp.get('/events')
def events():return render_template('public/events.html',props=props())
@bp.get('/offers')
def offers():
    db=get_db();rates=[dict(r) for r in db.execute("SELECT x.*,c.name cat,p.name pname FROM pricing_rules x JOIN room_categories c ON c.id=x.category_id JOIN properties p ON p.id=c.property_id WHERE c.active=1 AND p.active=1 AND x.end_date>=? ORDER BY x.start_date,p.id,c.name",(today().isoformat(),))]
    return render_template('public/offers.html',rates=rates,today=today().isoformat())
@bp.get('/rooms')
def rooms():
    db=get_db();rs=[dict(r) for r in db.execute(B.ROOM_SQL+' ORDER BY p.id,c.ac DESC,r.number')]
    for r in rs:
        r['images']=[dict(i) for i in db.execute('SELECT path,alt FROM room_images WHERE room_id=? ORDER BY sort,id',(r['id'],))]
        r['cat_images']=category_media(db,r['category_id']);r['display_price_paise']=B.price_per_night(db,r,today().isoformat())
    return render_template('public/rooms.html',cats=cats(),rooms=rs,room_previews=category_previews(),today=today().isoformat())
@bp.get('/rooms/<int:rid>')
def room(rid):
    db=get_db();r=db.execute(B.ROOM_SQL+' AND r.id=?',(rid,)).fetchone()
    if not r:abort(404)
    r=dict(r);r['images']=[dict(i) for i in db.execute('SELECT path,alt FROM room_images WHERE room_id=? ORDER BY sort,id',(rid,))];r['cat_images']=category_media(db,r['category_id']);r['amenities']=B.amenities(db,rid)
    return render_template('public/room.html',r=r,today=today().isoformat(),tomorrow=(today()+dtm.timedelta(days=1)).isoformat(),price=B.price_per_night(db,r,today().isoformat()))
@bp.get('/search')
def search():
    db=get_db();f={k:request.args.get(k,'') for k in('ci','co','adults','children','rooms','cat','budget')};res=None;err={};inventory_count=db.execute(f'SELECT COUNT(*) FROM ({B.ROOM_SQL})').fetchone()[0];categories=cats()
    active_room_count=db.execute("SELECT COUNT(*) FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id WHERE r.status='active' AND c.active=1 AND p.active=1").fetchone()[0]
    nights=0;search_properties=[];available_amenities=[];category_count=0
    if f['ci'] and f['co']:
        try:
            requested_rooms=int(f['rooms'] or 1)
            if not 1<=requested_rooms<=10:raise ValueError('Choose between 1 and 10 rooms.')
            bud=int(float(f['budget'])*100) if f['budget'] else None
            res=B.search(db,f['ci'],f['co'],f['adults'] or 1,f['children'] or 0,category_id=f['cat'] or None,max_price_paise=bud)
            stay_start=d(f['ci'])
            nights=(d(f['co'])-stay_start).days
            for room in res:
                room['cat_images']=category_media(db,room['category_id'])
                room['nightly_rates']=[{'date':(stay_start+dtm.timedelta(days=i)).isoformat(),'amount':amount} for i,amount in enumerate(room['quote']['per_night'])]
            category_count=len({room['category_id'] for room in res})
            available_amenities=sorted({amenity for room in res for amenity in room['amenities']},key=str.casefold)
        except B.Invalid as e:err=e.errors or {'dates':str(e)}
        except ValueError as e:err={'rooms' if 'room' in str(e) else 'budget':str(e) or 'Enter a valid value.'}
    property_ids={room['pid'] for room in res or []}
    search_properties=[dict(p) for p in props() if not property_ids or p['id'] in property_ids]
    return render_template('public/search.html',f=f,res=res,err=err,cats=categories,today=today().isoformat(),inventory_count=inventory_count,active_room_count=active_room_count,nights=nights,search_properties=search_properties,available_amenities=available_amenities,category_count=category_count,policy_hours=current_app.config['CANCEL_FREE_HOURS'],late_refund_percent=current_app.config['LATE_CANCEL_REFUND_PERCENT'],online_payment_ready=P.configured())
@bp.route('/book',methods=['GET','POST'])
@rate_limit('book',12,600)
def book():
    db=get_db();src=request.values;f={k:src.get(k,'') for k in('room_id','ci','co','adults','children')};errs={};vals={k:request.form.get(k,'') for k in('name','phone','email','notes')}
    try:rs=B.search(db,f['ci'],f['co'],f['adults'] or 1,f['children'] or 0);room=next((r for r in rs if str(r['id'])==f['room_id']),None)
    except B.Invalid as e:flash(str(e),'error');return redirect(url_for('public.search'))
    if not room:flash('That room is no longer available for those dates. Please search again.','error');return redirect(url_for('public.search',ci=f['ci'],co=f['co'],adults=f['adults'],children=f['children']))
    if request.method=='POST':
        if request.form.get('website'):abort(400)
        online=request.form.get('pay_mode')=='online' and P.configured()
        try:
            res=B.create_reservation(db,f['room_id'],f['ci'],f['co'],f['adults'],f['children'],vals['name'],vals['phone'],vals['email'],source='web',hold_minutes=current_app.config['HOLD_MINUTES'] if online else None,notes=vals['notes'][:300])
            session['refs']=(session.get('refs',[])+[res['ref']])[-10:]
            if not online:notify.send(db,res['id'],'booking_confirmed')
            return redirect(url_for('public.confirmation',ref=res['ref']))
        except B.Invalid as e:errs=e.errors or {'form':str(e)}
        except B.Unavailable as e:flash(str(e),'error');return redirect(url_for('public.search',ci=f['ci'],co=f['co'],adults=f['adults'],children=f['children']))
    room['cat_images']=category_media(db,room['category_id'])
    return render_template('public/book.html',f=f,room=room,errs=errs,vals=vals,hold=current_app.config['HOLD_MINUTES'])
@bp.get('/booking/<ref>')
def confirmation(ref):
    if not can_view(ref):return redirect(url_for('public.lookup'))
    db=get_db();res=B.get_res(db,ref=ref);paid=B.paid_total(db,res['id'])
    return render_template('public/confirmation.html',res=res,paid=paid,due=max(0,res['total_paise']-paid),policy_hours=current_app.config['CANCEL_FREE_HOURS'],pay_ready=P.configured())
@bp.route('/booking/lookup',methods=['GET','POST'])
@rate_limit('lookup',10,600)
def lookup():
    if request.method=='POST':
        try:
            res=B.lookup(get_db(),request.form.get('ref',''),request.form.get('phone',''));session['manage']=res['ref'];return redirect(url_for('public.confirmation',ref=res['ref']))
        except B.NotFound as e:flash(str(e),'error')
    return render_template('public/lookup.html')
@bp.post('/booking/<ref>/cancel')
def cancel(ref):
    if not can_view(ref):abort(403)
    db=get_db()
    try:
        res=B.get_res(db,ref=ref);res,due=B.cancel(db,res['id'],request.form.get('reason',''),by_customer=True);notify.send(db,res['id'],'booking_cancelled')
        flash('Your booking has been cancelled.'+(f' A refund of {due/100:,.0f} rupees will be processed by our team.' if due else ''),'ok')
    except B.BookingError as e:flash(str(e),'error')
    return redirect(url_for('public.confirmation',ref=ref))
@bp.post('/booking/<ref>/modify')
def modify(ref):
    if not can_view(ref):abort(403)
    db=get_db()
    try:
        res=B.get_res(db,ref=ref);res=B.modify(db,res['id'],request.form.get('ci',''),request.form.get('co',''),request.form.get('adults',res['adults']),request.form.get('children',res['children']));notify.send(db,res['id'],'booking_modified');flash('Your booking has been updated.','ok')
    except B.BookingError as e:flash(str(e),'error')
    return redirect(url_for('public.confirmation',ref=ref))
@bp.get('/booking/<ref>/receipt.pdf')
def receipt(ref):
    if not can_view(ref):abort(403)
    db=get_db();res=B.get_res(db,ref=ref);return Response(P.receipt_pdf(db,res['id']),mimetype='application/pdf',headers={'Content-Disposition':f'inline; filename=receipt-{ref}.pdf'})
@bp.get('/gallery')
def gallery():
    items=media_items(get_db(),'gallery')
    return render_template('public/gallery.html',items=items if items else GALLERY)
@bp.get('/facilities')
def facilities():return render_template('public/facilities.html',facilities=FACILITIES)
@bp.get('/about')
def about():return render_template('public/about.html')
@bp.route('/contact',methods=['GET','POST'])
def contact():
    db=get_db();active_props=props();vals={k:request.values.get(k,'') for k in('kind','property_id','name','phone','email','preferred_date','guest_count','message')};errors={}
    if request.method=='POST':
        if not rate_ok(f'contact:{request.remote_addr}',8,600):abort(429)
        f=request.form;kind=f.get('kind','general');property_id=None
        try:
            if kind not in('room','event','general'):raise ValueError('Choose a valid enquiry type.')
            if f.get('property_id'):
                property_id=int(f['property_id'])
                if not db.execute('SELECT 1 FROM properties WHERE id=? AND active=1',(property_id,)).fetchone():raise ValueError('Choose an active property.')
            preferred=d(f['preferred_date']).isoformat() if f.get('preferred_date') else None
            if preferred and preferred<today().isoformat():raise ValueError('Choose today or a future date.')
            guest_count=int(f['guest_count']) if f.get('guest_count') else None
            if guest_count is not None and not 1<=guest_count<=10000:raise ValueError('Enter a guest count between 1 and 10,000.')
            message=' '.join(f.get('message','').split())
            if not 10<=len(message)<=2000:raise ValueError('Message must be 10 to 2,000 characters.')
            name,phone,email=B.validate_customer(f.get('name',''),f.get('phone',''),f.get('email',''))
            db.execute('INSERT INTO guest_enquiries(property_id,kind,name,phone,email,preferred_date,guest_count,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(property_id,kind,name,phone,email,preferred,guest_count,message,now_s(),now_s()))
            flash('Thank you. Your enquiry has been recorded for the property team.','ok');return redirect(url_for('public.contact',_anchor='enquiry'))
        except B.BookingError as e:errors.update(e.errors or {'form':str(e)})
        except (ValueError,TypeError) as e:errors['form']=str(e) or 'Check the enquiry details and try again.'
        flash('Please check the highlighted enquiry details.','error')
    return render_template('public/contact.html',props=active_props,vals=vals,errors=errors)
@bp.get('/faq')
def faq():return render_template('public/faq.html',faq=FAQ,ld={'@context':'https://schema.org','@type':'FAQPage','mainEntity':[{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':a}} for q,a in FAQ]})
@bp.get('/privacy')
def privacy():return render_template('public/privacy.html')
@bp.get('/robots.txt')
def robots():return Response(f"User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /api/\nDisallow: /booking/\n\nSitemap: {current_app.config['SITE_URL']}/sitemap.xml\n",mimetype='text/plain')
@bp.get('/sitemap.xml')
def sitemap():
    u=current_app.config['SITE_URL'];paths=['/','/properties','/rooms','/events','/offers','/gallery','/facilities','/about','/contact','/faq','/privacy','/search','/booking/lookup']+[f'/rooms/{r[0]}' for r in get_db().execute(B.ROOM_SQL.replace('SELECT r.id','SELECT r.id',1))]
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+''.join(f'<url><loc>{u}{p}</loc></url>\n' for p in paths)+'</urlset>',mimetype='application/xml')
