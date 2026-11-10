"""Availability and reservation engine. Double booking is prevented twice: by an explicit check and by the
room_nights PRIMARY KEY(room_id,night), inside a BEGIN IMMEDIATE transaction."""
import secrets,sqlite3
from datetime import timedelta
from flask import current_app
from ..db import tx,setting
from ..util import now,now_s,today,d,daterange,norm_phone,EM
from . import dynamic_pricing as DP
class BookingError(Exception):
    def __init__(s,msg,errors=None):super().__init__(msg);s.errors=errors or {}
class Unavailable(BookingError):pass
class Invalid(BookingError):pass
class NotFound(BookingError):pass
def cfg(k):return current_app.config[k]
def tax_configuration(db,category_id=None):
    if category_id is not None:
        category=db.execute('SELECT tax_percent,tax_status FROM room_categories WHERE id=?',(category_id,)).fetchone()
        if category and category['tax_percent'] is not None:
            if category['tax_status']!='approved':return 0.0,False
            return float(category['tax_percent']),True
    confirmed=setting(db,'tax_percent_confirmed')
    value=setting(db,'tax_percent')
    if confirmed is not None:
        try:percent=float(value)
        except (TypeError,ValueError):return 0.0,False
        return (percent,True) if confirmed=='1' and 0<=percent<=100 else (0.0,False)
    return 0.0,False
def inventory_configuration(db):
    return dict(
        rates_confirmed=setting(db,'room_rates_confirmed')=='1',
        occupancy_confirmed=setting(db,'room_occupancy_confirmed')=='1',
        rooms_confirmed=setting(db,'room_inventory_confirmed')=='1',
        photos_confirmed=setting(db,'room_photos_confirmed')=='1')
def inventory_publication_error(db):
    config=inventory_configuration(db)
    if not config['rates_confirmed'] or not config['occupancy_confirmed'] or not config['rooms_confirmed']:
        return Invalid('Room details are subject to management confirmation.',{
            'availability':'Room details, rates, occupancy and inventory are not yet approved for online booking. Please contact the property team.'})
    ready=db.execute('''SELECT 1 FROM room_categories c JOIN properties p ON p.id=c.property_id
        WHERE c.active=1 AND p.active=1 AND c.publication_status='published'
        AND c.details_status='approved' AND c.inventory_status='approved'
        AND c.occupancy_status='approved' AND c.rates_status='approved' AND c.tax_status='approved'
        AND c.nightly_rate_paise>0 AND c.max_adults_approved>0
        AND c.max_children_approved>=0 AND c.max_occupancy>=c.max_adults_approved
        AND c.max_occupancy>=c.max_children_approved
        LIMIT 1''').fetchone()
    if ready:return None
    return Invalid('Room rates, occupancy details and room inventory are subject to management confirmation.',{
        'availability':'Room details, rates, occupancy and inventory are not yet approved for online booking. Please contact the property team.'})
def audit(db,user,action,entity,eid,detail=''):
    db.execute('INSERT INTO audit_logs(at,user_id,actor,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?,?)',(now_s(),user['id'] if user else None,user['email'] if user else 'system',action,entity,str(eid),detail[:500]))
def expire_holds(db):
    rows=db.execute("SELECT id FROM reservations WHERE status='pending' AND hold_expires_at IS NOT NULL AND hold_expires_at<?",(now_s(),)).fetchall()
    for r in rows:
        db.execute("UPDATE reservations SET status='expired',updated_at=? WHERE id=?",(now_s(),r[0]));db.execute('DELETE FROM room_nights WHERE reservation_id=?',(r[0],))
        audit(db,None,'hold_expired','reservation',r[0])
def validate_stay(ci,co,adults,children,allow_past=False):
    e={}
    try:a=d(ci);b=d(co)
    except Exception:raise Invalid('Please choose valid dates.',{'dates':'Choose valid check-in and check-out dates.'})
    if not allow_past and a<today():e['check_in']='Check-in cannot be in the past.'
    if b<=a:e['check_out']='Check-out must be after check-in.'
    elif (b-a).days>cfg('MAX_STAY_NIGHTS'):e['check_out']=f"Stays are limited to {cfg('MAX_STAY_NIGHTS')} nights online. Please contact us."
    try:adults=int(adults);children=int(children or 0)
    except Exception:e['guests']='Enter valid guest numbers.'
    else:
        if adults<1:e['adults']='At least one adult is required.'
        if children<0 or adults>12 or children>12:e['guests']='Enter valid guest numbers.'
    if e:raise Invalid('Please fix the highlighted fields.',e)
    return ci,co,adults,children
def validate_customer(name,phone,email):
    e={};name=' '.join((name or '').split());p=norm_phone(phone);email=(email or '').strip()
    if not 2<=len(name)<=80 or any(ord(c)<32 for c in name):e['name']='Enter the guest name.'
    if not p:e['phone']='Enter a valid 10-digit Indian mobile number.'
    if email and not EM.match(email):e['email']='Enter a valid email address.'
    if e:raise Invalid('Please fix the highlighted fields.',e)
    return name,p,email or None
def price_per_night(db,room,night,nights=1,check_in=None):
    r=db.execute('SELECT price_paise FROM pricing_rules WHERE category_id=? AND approved=1 AND ?>=start_date AND ?<=end_date ORDER BY id DESC LIMIT 1',(room['category_id'],night,night)).fetchone()
    base=room['price_override_paise'] or (r[0] if r else room['nightly_rate_paise'])
    return DP.nightly(db,room['category_id'],night,base,nights,check_in)['price_paise']
def quote(db,room,ci,co):
    nights=(d(co)-d(ci)).days;per=[price_per_night(db,room,n,nights,ci) for n in daterange(ci,co)];sub=sum(per);tax_percent,tax_configured=tax_configuration(db,room['category_id']);tax=round(sub*tax_percent/100) if tax_configured else 0
    return dict(nights=len(per),per_night=per,room_total=sub,tax=tax,total=sub+tax,avg=round(sub/len(per)),tax_configured=tax_configured,tax_percent=tax_percent if tax_configured else None)
ROOM_SQL='''SELECT r.id,r.number,r.floor,r.description,r.category_id,r.price_override_paise,c.name cat_name,c.room_type,c.ac,c.image,c.description cat_desc,c.max_adults_approved max_adults,c.max_children_approved max_children,c.max_occupancy,c.nightly_rate_paise,c.currency,c.tax_percent,c.tax_status,c.photos_status,
 p.id pid,p.name pname,p.address paddress FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id
 WHERE r.status='active' AND c.active=1 AND p.active=1 AND c.publication_status='published'
 AND c.details_status='approved' AND c.inventory_status='approved' AND c.occupancy_status='approved' AND c.rates_status='approved' AND c.tax_status='approved'
 AND c.nightly_rate_paise>0 AND c.max_adults_approved>0 AND c.max_children_approved>=0
 AND c.max_occupancy>=c.max_adults_approved AND c.max_occupancy>=c.max_children_approved
 AND c.nightly_rate_paise>0'''
def free_sql():
    return """ AND NOT EXISTS(SELECT 1 FROM room_nights n WHERE n.room_id=r.id AND n.night>=:ci AND n.night<:co)
        AND NOT EXISTS(SELECT 1 FROM reservations s WHERE s.room_id=r.id AND s.status IN('pending','confirmed','checked_in')
        AND s.check_in<:co AND s.check_out>:ci)"""
def search(db,ci,co,adults=1,children=0,category_id=None,property_id=None,max_price_paise=None,allow_past=False,rooms=1):
    ci,co,adults,children=validate_stay(ci,co,adults,children,allow_past)
    publication_error=inventory_publication_error(db)
    if publication_error:raise publication_error
    try:rooms=int(rooms)
    except (TypeError,ValueError):raise Invalid('Choose between 1 and 10 rooms.',{'rooms':'Choose between 1 and 10 rooms.'})
    if not 1<=rooms<=10:raise Invalid('Choose between 1 and 10 rooms.',{'rooms':'Choose between 1 and 10 rooms.'})
    adults_per_room=(adults+rooms-1)//rooms;children_per_room=(children+rooms-1)//rooms
    expire_holds(db)
    sql=ROOM_SQL+' AND c.max_adults_approved>=:a AND c.max_children_approved>=:ch AND c.max_occupancy>=:total'+free_sql();args=dict(ci=ci,co=co,a=adults_per_room,ch=children_per_room,total=adults_per_room+children_per_room)
    if category_id:
        try:args['cat']=int(category_id)
        except (TypeError,ValueError):raise Invalid('Choose a valid room category.',{'category_id':'Choose a valid room category.'})
        sql+=' AND c.id=:cat'
    if property_id:
        try:args['pid']=int(property_id)
        except (TypeError,ValueError):raise Invalid('Choose a valid property.',{'property_id':'Choose a valid property.'})
        sql+=' AND p.id=:pid'
    out=[]
    for r in db.execute(sql+' ORDER BY c.ac DESC,r.number',args):
        r=dict(r);q=quote(db,r,ci,co)
        if max_price_paise and q['avg']>max_price_paise:continue
        r['quote']=q;r['images']=[dict(i) for i in db.execute('SELECT path,alt FROM room_images WHERE room_id=? ORDER BY sort,id',(r['id'],))] if r['photos_status']=='approved' else [];r['amenities']=amenities(db,r['id']);out.append(r)
    return out
def amenities(db,room_id):return [x[0] for x in db.execute('SELECT a.name FROM amenities a JOIN room_amenities ra ON ra.amenity_id=a.id WHERE ra.room_id=? ORDER BY a.name',(room_id,))]
def get_res(db,rid=None,ref=None):
    sql='''SELECT s.*,c.name cname,c.phone cphone,c.email cemail,r.number rnumber,r.category_id,k.name cat_name,k.ac,p.name pname,p.id pid FROM reservations s JOIN customers c ON c.id=s.customer_id
     JOIN rooms r ON r.id=s.room_id JOIN room_categories k ON k.id=r.category_id JOIN properties p ON p.id=r.property_id WHERE '''
    row=db.execute(sql+('s.id=?' if rid else 's.ref=?'),(rid or (ref or '').strip().upper(),)).fetchone()
    if not row:raise NotFound('Booking not found.')
    return dict(row)
def lookup(db,ref,phone):
    p=norm_phone(phone)
    try:r=get_res(db,ref=ref)
    except NotFound:raise NotFound('We could not find a booking with those details.')
    if not p or r['cphone']!=p:raise NotFound('We could not find a booking with those details.')
    return r
def paid_total(db,rid):
    return db.execute("SELECT COALESCE(SUM(amount_paise-refunded_paise),0) FROM payments WHERE reservation_id=? AND status IN('paid','partially_refunded','refunded')",(rid,)).fetchone()[0]
def outstanding(db,rid):return max(0,db.execute('SELECT total_paise FROM reservations WHERE id=?',(rid,)).fetchone()[0]-paid_total(db,rid))
def new_ref(db):
    while True:
        ref='VH'+''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
        if not db.execute('SELECT 1 FROM reservations WHERE ref=?',(ref,)).fetchone():return ref
def _insert_nights(db,room_id,rid,ci,co):
    try:
        for n in daterange(ci,co):db.execute('INSERT INTO room_nights(room_id,night,reservation_id) VALUES(?,?,?)',(room_id,n,rid))
    except sqlite3.IntegrityError:raise Unavailable('Sorry, that room was just booked for some of those dates. Please search again.')
def create_reservation(db,room_id,ci,co,adults,children,name,phone,email,source='web',user=None,hold_minutes=None,notes=None,allow_past=False):
    ci,co,adults,children=validate_stay(ci,co,adults,children,allow_past);name,phone,email=validate_customer(name,phone,email)
    if source not in('web','ai','walk-in','phone'):raise Invalid('Invalid booking source.')
    publication_error=inventory_publication_error(db)
    if publication_error:raise publication_error
    with tx(db):
        expire_holds(db)
        room=db.execute(ROOM_SQL+' AND r.id=?',(int(room_id),)).fetchone()
        if not room:raise Unavailable('That room is not available for booking.')
        room=dict(room)
        if adults>room['max_adults'] or children>room['max_children'] or adults+children>room['max_occupancy']:raise Invalid(f"This room fits up to {room['max_adults']} adults and {room['max_children']} children, with a maximum total occupancy of {room['max_occupancy']}.",{'guests':'Too many guests for this room.'})
        if not tax_configuration(db,room['category_id'])[1]:raise Invalid('Tax settings must be confirmed in Admin before accepting bookings.',{'tax':'Confirm the applicable tax percentage in Categories and rates.'})
        if db.execute('''SELECT 1 FROM room_nights WHERE room_id=? AND night>=? AND night<?
            UNION ALL SELECT 1 FROM reservations WHERE room_id=? AND status IN('pending','confirmed','checked_in')
            AND check_in<? AND check_out>? LIMIT 1''',(room['id'],ci,co,room['id'],co,ci)).fetchone():raise Unavailable('Sorry, that room is not available for those dates.')
        q=quote(db,room,ci,co);t=now_s()
        c=db.execute('SELECT id FROM customers WHERE phone=?',(phone,)).fetchone()
        if c:cid=c[0];db.execute('UPDATE customers SET name=?,email=COALESCE(?,email) WHERE id=?',(name,email,cid))
        else:cid=db.execute('INSERT INTO customers(name,phone,email) VALUES(?,?,?)',(name,phone,email)).lastrowid
        dup=db.execute("SELECT ref FROM reservations WHERE customer_id=? AND room_id=? AND check_in=? AND check_out=? AND status IN('pending','confirmed')",(cid,room['id'],ci,co)).fetchone()
        if dup:raise Unavailable(f'You already have this booking ({dup[0]}).')
        ref=new_ref(db);hold=(now()+timedelta(minutes=hold_minutes)).strftime('%Y-%m-%d %H:%M:%S') if hold_minutes else None
        rid=db.execute('''INSERT INTO reservations(ref,customer_id,room_id,check_in,check_out,adults,children,nights,room_total_paise,tax_paise,total_paise,status,source,notes,hold_expires_at,created_by,created_at,updated_at)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(ref,cid,room['id'],ci,co,adults,children,q['nights'],q['room_total'],q['tax'],q['total'],'pending' if hold_minutes else 'confirmed',source,notes,hold,user['id'] if user else None,t,t)).lastrowid
        _insert_nights(db,room['id'],rid,ci,co);audit(db,user,'booking_created','reservation',rid,f'{ref} via {source}')
    return get_res(db,rid)
def _policy_refund(db,res,by_customer):
    paid=paid_total(db,res['id']);deadline=d(res['check_in']);import datetime as dtm
    cutoff=dtm.datetime.combine(deadline,dtm.time(12,0),tzinfo=now().tzinfo)-timedelta(hours=cfg('CANCEL_FREE_HOURS'))
    pct=100 if (not by_customer or now()<=cutoff) else cfg('LATE_CANCEL_REFUND_PERCENT');return round(paid*pct/100)
def cancel(db,rid,reason='',user=None,by_customer=False):
    with tx(db):
        res=get_res(db,rid)
        if res['status'] not in('pending','confirmed'):raise Invalid('This booking can no longer be cancelled.')
        due=_policy_refund(db,res,by_customer);t=now_s()
        db.execute("UPDATE reservations SET status='cancelled',updated_at=? WHERE id=?",(t,rid));db.execute('DELETE FROM room_nights WHERE reservation_id=?',(rid,))
        db.execute('INSERT INTO cancellations(reservation_id,reason,refund_due_paise,by_user,by_customer,at) VALUES(?,?,?,?,?,?)',(rid,(reason or '')[:300],due,user['id'] if user else None,1 if by_customer else 0,t))
        audit(db,user,'booking_cancelled','reservation',rid,f'refund due {due}')
    return get_res(db,rid),due
def modify(db,rid,ci,co,adults,children,user=None):
    ci,co,adults,children=validate_stay(ci,co,adults,children,allow_past=bool(user))
    publication_error=inventory_publication_error(db)
    if publication_error:raise publication_error
    with tx(db):
        res=get_res(db,rid)
        if res['status'] not in('pending','confirmed'):raise Invalid('Only upcoming bookings can be modified.')
        room=dict(db.execute(ROOM_SQL.replace("AND c.active=1 AND p.active=1","")+' AND r.id=?',(res['room_id'],)).fetchone() or {})
        if not room:raise Unavailable('Room is not available.')
        if adults>room['max_adults'] or children>room['max_children'] or adults+children>room['max_occupancy']:raise Invalid('Too many guests for this room.',{'guests':'Too many guests for this room.'})
        if not tax_configuration(db,room['category_id'])[1]:raise Invalid('Tax settings must be confirmed in Admin before changing booking totals.',{'tax':'Confirm the applicable tax percentage in Categories and rates.'})
        if db.execute('SELECT 1 FROM reservations WHERE room_id=? AND id<>? AND status IN(\'pending\',\'confirmed\',\'checked_in\') AND check_in<? AND check_out>? LIMIT 1',(room['id'],rid,co,ci)).fetchone():raise Unavailable('Sorry, that room is not available for those dates.')
        db.execute('DELETE FROM room_nights WHERE reservation_id=?',(rid,));_insert_nights(db,room['id'],rid,ci,co)
        q=quote(db,room,ci,co);db.execute('UPDATE reservations SET check_in=?,check_out=?,adults=?,children=?,nights=?,room_total_paise=?,tax_paise=?,total_paise=?,updated_at=? WHERE id=?',(ci,co,adults,children,q['nights'],q['room_total'],q['tax'],q['total'],now_s(),rid))
        audit(db,user,'booking_modified','reservation',rid,f'{ci} to {co}')
    return get_res(db,rid)
def change_room(db,rid,new_room_id,user=None):
    with tx(db):
        res=get_res(db,rid)
        if res['status'] not in('pending','confirmed','checked_in'):raise Invalid('This booking cannot be reassigned.')
        room=db.execute("SELECT r.*,c.max_adults_approved max_adults,c.max_children_approved max_children,c.max_occupancy FROM rooms r JOIN room_categories c ON c.id=r.category_id WHERE r.id=? AND r.status='active'",(int(new_room_id),)).fetchone()
        if not room:raise Unavailable('That room is not available.')
        if res['adults']>room['max_adults'] or res['children']>room['max_children'] or res['adults']+res['children']>room['max_occupancy']:raise Invalid('That room is too small for this party.')
        if db.execute('SELECT 1 FROM reservations WHERE room_id=? AND id<>? AND status IN(\'pending\',\'confirmed\',\'checked_in\') AND check_in<? AND check_out>? LIMIT 1',(room['id'],rid,res['check_out'],res['check_in'])).fetchone():raise Unavailable('That room is not available for those dates.')
        db.execute('DELETE FROM room_nights WHERE reservation_id=?',(rid,));_insert_nights(db,room['id'],rid,res['check_in'],res['check_out'])
        db.execute('UPDATE reservations SET room_id=?,updated_at=? WHERE id=?',(room['id'],now_s(),rid));audit(db,user,'room_reassigned','reservation',rid,f"to room {room['number']}")
    return get_res(db,rid)
def check_in(db,rid,user,room_id=None,id_type='',id_last4='',note=''):
    with tx(db):
        res=get_res(db,rid)
        if res['status']!='confirmed':raise Invalid('Only confirmed bookings can be checked in.' if res['status']!='pending' else 'This booking is awaiting payment confirmation.')
        if res['check_in']>today().isoformat():raise Invalid('Check-in date has not arrived yet.')
        if room_id and int(room_id)!=res['room_id']:
            room=db.execute("SELECT id FROM rooms WHERE id=? AND status='active'",(int(room_id),)).fetchone()
            if not room:raise Unavailable('That room is not available.')
            db.execute('DELETE FROM room_nights WHERE reservation_id=?',(rid,));_insert_nights(db,room['id'],rid,res['check_in'],res['check_out']);db.execute('UPDATE reservations SET room_id=? WHERE id=?',(room['id'],rid))
        t=now_s();db.execute("UPDATE reservations SET status='checked_in',updated_at=? WHERE id=?",(t,rid))
        rr=db.execute('SELECT room_id FROM reservations WHERE id=?',(rid,)).fetchone()[0]
        db.execute('INSERT INTO checkins(reservation_id,room_id,at,by_user,id_proof_type,id_proof_last4,note) VALUES(?,?,?,?,?,?,?)',(rid,rr,t,user['id'],id_type[:30],(id_last4 or '')[-4:],note[:200]))
        audit(db,user,'checked_in','reservation',rid)
    return get_res(db,rid)
def check_out(db,rid,user):
    with tx(db):
        res=get_res(db,rid)
        if res['status']!='checked_in':raise Invalid('Only checked-in guests can be checked out.')
        bal=outstanding(db,rid);t=now_s();db.execute("UPDATE reservations SET status='checked_out',updated_at=? WHERE id=?",(t,rid))
        db.execute('INSERT INTO checkouts(reservation_id,at,by_user,balance_paise) VALUES(?,?,?,?)',(rid,t,user['id'],bal))
        db.execute('DELETE FROM room_nights WHERE reservation_id=? AND night>?',(rid,today().isoformat()))
        audit(db,user,'checked_out','reservation',rid,f'balance {bal}')
    return get_res(db,rid),bal
def block_room(db,room_id,start,end,reason,user):
    validate_stay(start,end,1,0,allow_past=True)
    with tx(db):
        for n in daterange(start,end):
            try:db.execute('INSERT INTO room_nights(room_id,night,reservation_id,reason) VALUES(?,?,NULL,?)',(int(room_id),n,(reason or 'Blocked')[:100]))
            except sqlite3.IntegrityError:raise Unavailable(f'{n} already has a booking or block for this room.')
        audit(db,user,'room_blocked','room',room_id,f'{start} to {end}: {reason}')
def unblock(db,room_id,night,user):
    with tx(db):db.execute('DELETE FROM room_nights WHERE room_id=? AND night=? AND reservation_id IS NULL',(int(room_id),night));audit(db,user,'room_unblocked','room',room_id,night)
def room_status_board(db,on=None):
    on=on or today().isoformat();out=[]
    for r in db.execute("SELECT r.id,r.number,r.status,c.name cat,c.ac,p.name pname FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id ORDER BY p.id,r.number"):
        r=dict(r);n=db.execute('SELECT n.reservation_id,n.reason,s.status,s.ref FROM room_nights n LEFT JOIN reservations s ON s.id=n.reservation_id WHERE n.room_id=? AND n.night=?',(r['id'],on)).fetchone()
        r['reservation_id']=n['reservation_id'] if n else None
        r['state']='inactive' if r['status']=='inactive' else 'available' if not n else 'blocked' if n['reservation_id'] is None else 'occupied' if n['status']=='checked_in' else 'reserved'
        r['detail']=(n['reason'] or n['ref']) if n else '';out.append(r)
    return out
