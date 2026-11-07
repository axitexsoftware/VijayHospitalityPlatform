import os,secrets,io,datetime as dtm
from flask import Blueprint,render_template,request,redirect,url_for,flash,session,abort,Response,current_app,g
from PIL import Image
from ..db import get_db,tx,setting,set_setting
from ..security import require,login,rate_limit,hash_password,password_problem
from ..util import today,now_s,d
from ..services import booking as B,payments as P,notify,reports as R,dynamic_pricing as DP,media as M
bp=Blueprint('admin',__name__)
def back(default='admin.dashboard',**k):return redirect(request.referrer or url_for(default,**k))
def guard(fn,*a,ok=None,**k):
    try:
        r=fn(*a,**k)
        if ok:flash(ok,'ok')
        return r
    except (B.BookingError,P.PaymentError) as e:flash(str(e),'error')
@bp.route('/login',methods=['GET','POST'])
@rate_limit('login',15,600)
def login_page():
    if request.method=='POST':
        u,e=login(request.form.get('email'),request.form.get('password'))
        if u:
            B.audit(get_db(),u,'login','user',u['id']);n=request.args.get('next','');return redirect(n if n.startswith('/admin') else url_for('admin.dashboard'))
        flash(e,'error')
    return render_template('admin/login.html')
bp.add_url_rule('/login',endpoint='login',view_func=login_page,methods=['GET','POST'])
@bp.post('/logout')
def logout():session.clear();return redirect(url_for('admin.login'))
@bp.get('/')
@require('dashboard')
def dashboard():
    db=get_db();B.expire_holds(db);t=today().isoformat()
    ar=R.booking_rows(db,status='confirmed',ci_from=t,ci_to=t);dp=R.booking_rows(db,status='checked_in',limit=200);dp=[r for r in dp if r['check_out']==t]
    start=(today()-dtm.timedelta(days=13)).isoformat();ser=R.series(db,start,t,'day');occ=R.occupancy_series(db,start,t)
    k=R.dashboard(db);k['new_enquiries']=db.execute("SELECT COUNT(*) FROM guest_enquiries WHERE status='new'").fetchone()[0];demand_max=max((x['nights'] for x in k['demand']),default=0)
    try:map_date=dtm.date.fromisoformat(request.args.get('date',''))
    except ValueError:map_date=today()
    if map_date<today():map_date=today()
    map_on=map_date.isoformat();month_on=map_date.replace(day=1).isoformat();year_on=map_date.replace(month=1,day=1).isoformat()
    finance={'day':R.financial_period(db,map_on,map_on),'month':R.financial_period(db,month_on,map_on),'year':R.financial_period(db,year_on,map_on)}
    return render_template('admin/dashboard.html',k=k,arr=ar,dep=dp,ser=ser,occ=occ,demand_max=demand_max,svg=R.svg_bars,finance=finance,map_date=map_on,today=t,room_map=B.room_status_board(db,map_on),upcoming=R.booking_rows(db,status='confirmed',ci_from=(today()+dtm.timedelta(days=1)).isoformat(),limit=8))
# ---- rooms & categories
@bp.get('/rooms')
@require('rooms')
def rooms():
    db=get_db();rs=db.execute("SELECT r.*,c.name cat,p.name pname FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id ORDER BY p.id,r.number").fetchall()
    return render_template('admin/rooms.html',rooms=rs,board={b['id']:b for b in B.room_status_board(db)},cats=db.execute('SELECT c.*,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id ORDER BY p.id,c.name').fetchall(),props=db.execute('SELECT * FROM properties').fetchall())
def _money(v):
    v=(v or '').strip()
    if not v:return None
    x=round(float(v)*100)
    if x<=0:raise ValueError
    return x
@bp.post('/rooms/new')
@require('rooms')
def room_new():
    db=get_db();f=request.form
    try:
        cat=db.execute('SELECT * FROM room_categories WHERE id=?',(int(f['category_id']),)).fetchone();num=f['number'].strip()
        if not num or not cat:raise ValueError
        rid=db.execute('INSERT INTO rooms(property_id,category_id,number,floor,price_override_paise,description) VALUES(?,?,?,?,?,?)',(cat['property_id'],cat['id'],num[:10],f.get('floor','')[:10],_money(f.get('price')),f.get('description','')[:500])).lastrowid
        B.audit(db,g.user,'room_added','room',rid,num);flash('Room added.','ok');return redirect(url_for('admin.room_edit',rid=rid))
    except Exception as e:flash('Could not add the room. Check the number is unique for that property and the price is valid.','error')
    return back('admin.rooms')
@bp.route('/rooms/<int:rid>',methods=['GET','POST'])
@require('rooms')
def room_edit(rid):
    db=get_db();r=db.execute('SELECT r.*,c.name cat,p.name pname FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id WHERE r.id=?',(rid,)).fetchone()
    if not r:abort(404)
    if request.method=='POST':
        f=request.form
        try:
            with tx(db):
                db.execute('UPDATE rooms SET category_id=?,number=?,floor=?,price_override_paise=?,description=?,status=? WHERE id=?',(int(f['category_id']),f['number'].strip()[:10],f.get('floor','')[:10],_money(f.get('price')),f.get('description','')[:500],'active' if f.get('status')=='active' else 'inactive',rid))
                db.execute('DELETE FROM room_amenities WHERE room_id=?',(rid,))
                for a in {x.strip()[:40] for x in f.get('amenities','').split(',') if x.strip()}:
                    db.execute('INSERT OR IGNORE INTO amenities(name) VALUES(?)',(a,));db.execute('INSERT INTO room_amenities SELECT ?,id FROM amenities WHERE name=?',(rid,a))
                B.audit(db,g.user,'room_updated','room',rid)
            flash('Room saved.','ok')
        except Exception:flash('Could not save. Check the room number is unique and the price is valid.','error')
        return redirect(url_for('admin.room_edit',rid=rid))
    nights=db.execute("SELECT n.night,n.reason FROM room_nights n WHERE n.room_id=? AND n.reservation_id IS NULL AND n.night>=? ORDER BY n.night",(rid,today().isoformat())).fetchall()
    return render_template('admin/room_edit.html',r=r,cats=db.execute('SELECT * FROM room_categories WHERE property_id=?',(r['property_id'],)).fetchall(),amen=', '.join(B.amenities(db,rid)),imgs=db.execute('SELECT * FROM room_images WHERE room_id=? ORDER BY sort,id',(rid,)).fetchall(),blocks=nights)
@bp.post('/rooms/<int:rid>/image')
@require('rooms')
def room_image(rid):
    db=get_db();f=request.files.get('image')
    try:
        if not f or not f.filename:raise ValueError('Choose an image file.')
        raw=f.stream.read(5*1024*1024+1)
        if len(raw)>5*1024*1024:raise ValueError('Photo must be 5 MB or smaller.')
        im=Image.open(io.BytesIO(raw));im.verify();im=Image.open(io.BytesIO(raw))
        if im.format not in('JPEG','PNG','WEBP'):raise ValueError('Use a JPEG, PNG or WebP image.')
        im=im.convert('RGB');im.thumbnail((1600,1600));name=f'{secrets.token_hex(8)}.jpg';im.save(os.path.join(current_app.config['UPLOAD_DIR'],name),'JPEG',quality=82,optimize=True)
        db.execute('INSERT INTO room_images(room_id,path,alt,sort) VALUES(?,?,?,?)',(rid,'uploads/'+name,request.form.get('alt','')[:150],int(db.execute('SELECT COALESCE(MAX(sort),0)+1 FROM room_images WHERE room_id=?',(rid,)).fetchone()[0])));flash('Photo uploaded.','ok')
    except Exception as e:flash(str(e) if isinstance(e,ValueError) else 'That file is not a valid image.','error')
    return back('admin.rooms')
@bp.post('/rooms/<int:rid>/image/<int:iid>/delete')
@require('rooms')
def room_image_delete(rid,iid):
    db=get_db();r=db.execute('SELECT path FROM room_images WHERE id=? AND room_id=?',(iid,rid)).fetchone()
    if r:
        db.execute('DELETE FROM room_images WHERE id=?',(iid,))
        try:os.remove(os.path.join(current_app.config['UPLOAD_DIR'],os.path.basename(r[0])))
        except OSError:pass
    return back('admin.rooms')
@bp.post('/rooms/<int:rid>/block')
@require('rooms')
def room_block(rid):
    guard(B.block_room,get_db(),rid,request.form.get('start',''),request.form.get('end',''),request.form.get('reason',''),g.user,ok='Room blocked for those nights.');return back('admin.rooms')
@bp.post('/rooms/<int:rid>/unblock')
@require('rooms')
def room_unblock(rid):B.unblock(get_db(),rid,request.form.get('night',''),g.user);return back('admin.rooms')
@bp.route('/categories',methods=['GET','POST'])
@require('rooms')
def categories():
    db=get_db()
    if request.method=='POST':
        f=request.form
        try:
            args=(f['name'].strip()[:40],1 if f.get('ac') else 0,f.get('description','')[:400],_money(f.get('price')) or 0,int(f.get('max_adults',2)),int(f.get('max_children',1)))
            if f.get('id'):db.execute('UPDATE room_categories SET name=?,ac=?,description=?,base_price_paise=?,max_adults=?,max_children=?,active=? WHERE id=?',args+(1 if f.get('active') else 0,int(f['id'])))
            else:db.execute('INSERT INTO room_categories(name,ac,description,base_price_paise,max_adults,max_children,property_id) VALUES(?,?,?,?,?,?,?)',args+(int(f['property_id']),))
            B.audit(db,g.user,'category_saved','category',f.get('id',''),f['name']);flash('Category saved.','ok')
        except Exception:flash('Could not save the category. Check the name is unique for the property and numbers are valid.','error')
        return redirect(url_for('admin.categories'))
    return render_template('admin/categories.html',cats=db.execute('SELECT c.*,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id ORDER BY p.id,c.name').fetchall(),props=db.execute('SELECT * FROM properties').fetchall(),rules=db.execute('SELECT x.*,c.name cat FROM pricing_rules x JOIN room_categories c ON c.id=x.category_id ORDER BY x.start_date').fetchall())
@bp.post('/pricing')
@require('rooms')
def pricing_add():
    f=request.form
    try:get_db().execute('INSERT INTO pricing_rules(category_id,label,start_date,end_date,price_paise) VALUES(?,?,?,?,?)',(int(f['category_id']),f.get('label','')[:60],f['start'],f['end'],_money(f['price'])));flash('Seasonal rate added.','ok')
    except Exception:flash('Check the dates and price.','error')
    return back('admin.categories')
@bp.post('/pricing/<int:xid>/delete')
@require('rooms')
def pricing_delete(xid):get_db().execute('DELETE FROM pricing_rules WHERE id=?',(xid,));return back('admin.categories')
# ---- dynamic pricing
@bp.get('/pricing/dynamic')
@require('rooms')
def dynamic_pricing():
    db=get_db();cats=db.execute('SELECT c.id,c.name,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 ORDER BY p.id,c.name').fetchall()
    f=request.args;preview=None
    if f.get('category_id') and f.get('check_in') and f.get('check_out'):
        try:preview=DP.quote(db,f['category_id'],f['check_in'],f['check_out'])
        except (ValueError,TypeError) as e:flash(str(e),'error')
    rules=db.execute('SELECT r.*,c.name cat,p.name pname FROM dynamic_rules r LEFT JOIN room_categories c ON c.id=r.category_id LEFT JOIN properties p ON p.id=c.property_id ORDER BY r.priority DESC,r.id').fetchall()
    return render_template('admin/dynamic_pricing.html',cats=cats,rules=rules,settings=DP.settings(db),f=f,preview=preview)
@bp.post('/pricing/dynamic/settings')
@require('rooms')
def dynamic_settings():
    db=get_db();f=request.form
    try:
        with tx(db):
            DP.save_settings(db,bool(f.get('enabled')),int(f.get('max_increase_pct','')),int(f.get('max_discount_pct','')),int(f.get('rounding_rupees','')),int(f.get('peak_threshold_pct','')))
            B.audit(db,g.user,'dynamic_pricing_settings_saved','settings','dynamic_pricing')
        flash('Dynamic pricing settings saved.','ok')
    except (ValueError,TypeError) as e:flash(str(e) or 'Check the dynamic pricing settings.','error')
    return redirect(url_for('admin.dynamic_pricing'))
@bp.post('/pricing/dynamic/rules')
@require('rooms')
def dynamic_rule_add():
    db=get_db();f=request.form
    try:
        kind=f.get('kind','')
        if kind not in DP.KINDS:raise ValueError('Choose a valid rule type.')
        category=int(f['category_id']) if f.get('category_id') else None
        if category is not None and not db.execute('SELECT 1 FROM room_categories WHERE id=? AND active=1',(category,)).fetchone():raise ValueError('Choose an active room category.')
        name=' '.join(f.get('name','').split())[:80]
        if not name:raise ValueError('Enter a rule name.')
        pct=int(f.get('pct',''));priority=int(f.get('priority') or 0)
        if not -100<=pct<=300:raise ValueError('Adjustment must be from -100% to +300%.')
        if not 0<=priority<=10000:raise ValueError('Priority must be from 0 to 10,000.')
        threshold=None;dow=None;start=None;end=None
        if kind in('occupancy','last_minute','early_bird','long_stay'):
            threshold=int(f.get('threshold',''))
            if threshold<1 or (kind=='occupancy' and threshold>100) or (kind!='occupancy' and threshold>365):raise ValueError('Threshold is outside the allowed range.')
        if kind=='weekend':
            days=sorted({int(x) for x in f.getlist('dow')})
            if not days or any(x<0 or x>6 for x in days):raise ValueError('Choose at least one weekday.')
            dow=','.join(map(str,days))
        if kind=='event':
            start,end=f.get('start_date',''),f.get('end_date','')
            d(start);d(end)
            if end<start:raise ValueError('Event end date must not be before its start date.')
        with tx(db):
            rule_id=db.execute('INSERT INTO dynamic_rules(category_id,name,kind,dow,threshold,start_date,end_date,pct,priority) VALUES(?,?,?,?,?,?,?,?,?)',(category,name,kind,dow,threshold,start,end,pct,priority)).lastrowid
            B.audit(db,g.user,'dynamic_rule_added','dynamic_rule',rule_id,name)
        flash('Dynamic pricing rule added.','ok')
    except (ValueError,TypeError) as e:flash(str(e) or 'Check the rule details.','error')
    return redirect(url_for('admin.dynamic_pricing'))
@bp.post('/pricing/dynamic/rules/<int:rule_id>/toggle')
@require('rooms')
def dynamic_rule_toggle(rule_id):
    db=get_db();rule=db.execute('SELECT name,active FROM dynamic_rules WHERE id=?',(rule_id,)).fetchone()
    if not rule:abort(404)
    with tx(db):
        db.execute('UPDATE dynamic_rules SET active=1-active WHERE id=?',(rule_id,))
        B.audit(db,g.user,'dynamic_rule_toggled','dynamic_rule',rule_id,rule['name'])
    flash('Dynamic pricing rule updated.','ok');return redirect(url_for('admin.dynamic_pricing'))
@bp.post('/pricing/dynamic/rules/<int:rule_id>/delete')
@require('rooms')
def dynamic_rule_delete(rule_id):
    db=get_db();rule=db.execute('SELECT name FROM dynamic_rules WHERE id=?',(rule_id,)).fetchone()
    if not rule:abort(404)
    with tx(db):
        db.execute('DELETE FROM dynamic_rules WHERE id=?',(rule_id,))
        B.audit(db,g.user,'dynamic_rule_deleted','dynamic_rule',rule_id,rule['name'])
    flash('Dynamic pricing rule deleted.','ok');return redirect(url_for('admin.dynamic_pricing'))
# ---- site media
def _media_link(value):
    value=(value or '').strip()[:500]
    if value and (value.startswith('//') or value.lower().startswith(('javascript:','data:','vbscript:'))):
        raise ValueError('Use a safe relative link or an http/https URL.')
    if value and not value.startswith('/') and not value.lower().startswith(('http://','https://')):
        raise ValueError('Use a safe relative link or an http/https URL.')
    return value or None
def _media_context(db,kind):
    rows=db.execute('SELECT m.*,p.name pname,c.name cname FROM site_media m LEFT JOIN properties p ON p.id=m.property_id LEFT JOIN room_categories c ON c.id=m.category_id WHERE m.kind=? ORDER BY m.sort,m.id',(kind,)).fetchall()
    return rows
@bp.get('/media')
@require('rooms')
def media_page():
    db=get_db();kind=request.args.get('kind','room')
    if kind not in('room','slide','gallery'):kind='room'
    return render_template('admin/media.html',kind=kind,media_rows=_media_context(db,kind),
        props=db.execute('SELECT id,name FROM properties ORDER BY id').fetchall(),
        cats=db.execute('SELECT c.id,c.name,c.property_id,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 ORDER BY p.id,c.name').fetchall())
@bp.post('/media/upload')
@require('rooms')
def media_upload():
    db=get_db();f=request.form;kind=f.get('kind','')
    if kind not in('room','slide','gallery'):abort(400)
    files=[x for x in request.files.getlist('images') if x and x.filename]
    if not files:flash('Choose at least one image.','error');return redirect(url_for('admin.media_page',kind=kind))
    if len(files)>8:flash('Upload no more than 8 images at a time.','error');return redirect(url_for('admin.media_page',kind=kind))
    category_id=None;property_id=None
    try:
        property_id=int(f.get('property_id') or 0) or None
        if kind=='room':
            category_id=int(f.get('category_id',''))
            category=db.execute('SELECT property_id FROM room_categories WHERE id=? AND active=1',(category_id,)).fetchone()
            if not category:raise ValueError('Choose an active room category.')
            property_id=category['property_id']
        elif property_id and not db.execute('SELECT 1 FROM properties WHERE id=?',(property_id,)).fetchone():
            raise ValueError('Choose a valid property.')
        link=_media_link(f.get('link_url')) if kind=='slide' else None
        title=' '.join(f.get('title','').split())[:120] or None
        caption=' '.join(f.get('caption','').split())[:300] or None
        base_sort=db.execute('SELECT COALESCE(MAX(sort),-1)+1 FROM site_media WHERE kind=?',(kind,)).fetchone()[0]
        saved_count=0;errors=[]
        for index,upload in enumerate(files):
            try:stored=M.save_upload(upload,current_app.config['UPLOAD_DIR'])
            except M.MediaError as e:errors.append(f'{upload.filename}: {e}');continue
            inserted=False
            try:
                with tx(db):
                    media_id=db.execute('INSERT INTO site_media(kind,property_id,category_id,title,caption,link_url,path,sort) VALUES(?,?,?,?,?,?,?,?)',
                        (kind,property_id,category_id,title,caption,link,stored['path'],base_sort+index)).lastrowid
                    B.audit(db,g.user,'site_media_added','site_media',media_id,f'{kind}: {title or upload.filename}')
                inserted=True
            finally:
                if not inserted:M.delete_upload(stored['path'],current_app.config['UPLOAD_DIR'])
            saved_count+=1
        if saved_count:flash(f'{saved_count} image{"s" if saved_count!=1 else ""} uploaded.','ok')
        for error in errors:flash(error,'error')
    except (ValueError,TypeError) as e:flash(str(e) or 'Check the media details.','error')
    return redirect(url_for('admin.media_page',kind=kind))
@bp.post('/media/<int:media_id>/save')
@require('rooms')
def media_save(media_id):
    db=get_db();row=db.execute('SELECT * FROM site_media WHERE id=?',(media_id,)).fetchone()
    if not row:abort(404)
    f=request.form;kind=row['kind']
    try:
        category_id=None;property_id=int(f.get('property_id') or 0) or None
        if kind=='room':
            category_id=int(f.get('category_id',''))
            category=db.execute('SELECT property_id FROM room_categories WHERE id=? AND active=1',(category_id,)).fetchone()
            if not category:raise ValueError('Choose an active room category.')
            property_id=category['property_id']
        elif property_id and not db.execute('SELECT 1 FROM properties WHERE id=?',(property_id,)).fetchone():
            raise ValueError('Choose a valid property.')
        title=' '.join(f.get('title','').split())[:120] or None
        caption=' '.join(f.get('caption','').split())[:300] or None
        link=_media_link(f.get('link_url')) if kind=='slide' else None
        sort=int(f.get('sort','0'))
        if not 0<=sort<=100000:raise ValueError('Order must be from 0 to 100,000.')
        with tx(db):
            db.execute('UPDATE site_media SET property_id=?,category_id=?,title=?,caption=?,link_url=?,sort=?,active=? WHERE id=?',
                (property_id,category_id,title,caption,link,sort,1 if f.get('active') else 0,media_id))
            B.audit(db,g.user,'site_media_updated','site_media',media_id,title or '')
        flash('Image details saved.','ok')
    except (ValueError,TypeError) as e:flash(str(e) or 'Check the image details.','error')
    return redirect(url_for('admin.media_page',kind=kind))
@bp.post('/media/<int:media_id>/delete')
@require('rooms')
def media_delete(media_id):
    db=get_db();row=db.execute('SELECT kind,path,title FROM site_media WHERE id=?',(media_id,)).fetchone()
    if not row:abort(404)
    with tx(db):
        db.execute('DELETE FROM site_media WHERE id=?',(media_id,))
        B.audit(db,g.user,'site_media_deleted','site_media',media_id,row['title'] or '')
    M.delete_upload(row['path'],current_app.config['UPLOAD_DIR'])
    flash('Image deleted.','ok');return redirect(url_for('admin.media_page',kind=row['kind']))
# ---- bookings
def _filters():a=request.args;return dict(q=a.get('q',''),status=a.get('status',''),cat=a.get('cat',''),created_from=a.get('cf',''),created_to=a.get('ct',''),ci_from=a.get('if',''),ci_to=a.get('it',''))
@bp.get('/bookings')
@require('bookings')
def bookings():
    db=get_db();B.expire_holds(db);return render_template('admin/bookings.html',rows=R.booking_rows(db,**_filters()),f=request.args,cats=db.execute('SELECT id,name FROM room_categories').fetchall())
@bp.get('/bookings/export.<fmt>')
@require('bookings')
def bookings_export(fmt):
    rows=R.booking_rows(get_db(),**_filters(),limit=5000)
    if fmt=='xlsx':return Response(R.xlsx(rows),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename=bookings.xlsx'})
    if fmt=='pdf':return Response(R.pdf(rows,'Vijay Hospitality: bookings'),mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename=bookings.pdf'})
    abort(404)
@bp.get('/bookings/<int:rid>')
@require('bookings')
def booking_detail(rid):
    db=get_db();res=B.get_res(db,rid) if db.execute('SELECT 1 FROM reservations WHERE id=?',(rid,)).fetchone() else abort(404)
    free=[r for r in B.search(db,res['check_in'],res['check_out'],res['adults'],res['children'],allow_past=True)] if res['status'] in('pending','confirmed','checked_in') else []
    return render_template('admin/booking.html',res=res,paid=B.paid_total(db,rid),due=B.outstanding(db,rid),pays=db.execute('SELECT * FROM payments WHERE reservation_id=? ORDER BY id',(rid,)).fetchall(),notes=db.execute('SELECT * FROM notifications WHERE reservation_id=? ORDER BY id DESC LIMIT 12',(rid,)).fetchall(),
     logs=db.execute("SELECT * FROM audit_logs WHERE entity='reservation' AND entity_id=? ORDER BY id DESC LIMIT 15",(str(rid),)).fetchall(),free=free,canc=db.execute('SELECT * FROM cancellations WHERE reservation_id=?',(rid,)).fetchone(),checkin=db.execute('SELECT * FROM checkins WHERE reservation_id=?',(rid,)).fetchone())
@bp.post('/bookings/<int:rid>/<action>')
@require('bookings')
def booking_action(rid,action):
    db=get_db();f=request.form;u=g.user
    if action in('refund',) and not has_perm_refund():abort(403)
    if action=='cancel':
        r=guard(B.cancel,db,rid,f.get('reason',''),u);
        if r:notify.send(db,rid,'booking_cancelled');flash(f'Booking cancelled. Refund due by policy: {r[1]/100:,.0f} rupees.','ok')
    elif action=='modify':
        if guard(B.modify,db,rid,f.get('ci',''),f.get('co',''),f.get('adults',1),f.get('children',0),u,ok='Booking updated.'):notify.send(db,rid,'booking_modified')
    elif action=='reassign':guard(B.change_room,db,rid,f.get('room_id'),u,ok='Room reassigned.')
    elif action=='checkin':
        if guard(B.check_in,db,rid,u,f.get('room_id') or None,f.get('id_type',''),f.get('id_last4',''),f.get('note',''),ok='Guest checked in.'):notify.send(db,rid,'checked_in')
    elif action=='checkout':
        r=guard(B.check_out,db,rid,u)
        if r:flash('Guest checked out.'+(f' Balance of {r[1]/100:,.0f} rupees is still due.' if r[1] else ''),'ok');notify.send(db,rid,'checked_out')
    elif action=='noshow':
        try:
            with tx(db):
                if B.get_res(db,rid)['status']!='confirmed':raise B.Invalid('Only confirmed bookings can be marked no-show.')
                db.execute("UPDATE reservations SET status='no_show',updated_at=? WHERE id=?",(now_s(),rid));db.execute('DELETE FROM room_nights WHERE reservation_id=?',(rid,));B.audit(db,u,'no_show','reservation',rid)
            flash('Marked as no-show; room released.','ok')
        except B.BookingError as e:flash(str(e),'error')
    elif action=='payment':
        try:P.record_manual(db,rid,f.get('method'),round(float(f.get('amount') or 0)*100),f.get('reference',''),u);flash('Payment recorded.','ok');notify.send(db,rid,'payment_received')
        except (P.PaymentError,ValueError) as e:flash(str(e) if isinstance(e,P.PaymentError) else 'Enter a valid amount.','error')
    elif action=='refund':
        try:P.refund(db,int(f['payment_id']),round(float(f.get('amount') or 0)*100),u);flash('Refund recorded.','ok')
        except (P.PaymentError,ValueError) as e:flash(str(e) if isinstance(e,P.PaymentError) else 'Enter a valid amount.','error')
    elif action=='notify' and f.get('event') in('booking_confirmed','payment_received','booking_cancelled','booking_modified','checkin_reminder','checked_in','checked_out'):notify.send(db,rid,f['event']);flash('Notification attempted. See the log below for the result.','ok')
    else:abort(404)
    return redirect(url_for('admin.booking_detail',rid=rid))
def has_perm_refund():
    from ..security import has_perm;return has_perm(g.user['role'],'refunds')
@bp.get('/bookings/<int:rid>/receipt.pdf')
@require('bookings')
def booking_receipt(rid):return Response(P.receipt_pdf(get_db(),rid),mimetype='application/pdf',headers={'Content-Disposition':f'inline; filename=receipt-{rid}.pdf'})
# ---- reception
@bp.route('/reception',methods=['GET','POST'])
@require('reception')
def reception():
    db=get_db();B.expire_holds(db);t=today().isoformat();a=request.values;avail=None;errs={}
    if request.method=='POST':
        f=request.form
        try:
            res=B.create_reservation(db,f.get('room_id'),f.get('ci',''),f.get('co',''),f.get('adults',1),f.get('children',0),f.get('name'),f.get('phone'),f.get('email'),source=f.get('source','walk-in') if f.get('source') in('walk-in','phone') else 'walk-in',user=g.user,notes=f.get('notes','')[:300])
            amt=round(float(f.get('advance') or 0)*100)
            if amt>0:P.record_manual(db,res['id'],f.get('method','cash'),amt,f.get('reference',''),g.user)
            if f.get('checkin_now') and res['check_in']==t:B.check_in(db,res['id'],g.user,None,f.get('id_type',''),f.get('id_last4',''))
            notify.send(db,res['id'],'booking_confirmed');flash(f"Booking {res['ref']} created.",'ok');return redirect(url_for('admin.booking_detail',rid=res['id']))
        except B.Invalid as e:errs=e.errors or {'form':str(e)};flash(str(e),'error')
        except (B.BookingError,P.PaymentError,ValueError) as e:flash(str(e) if not isinstance(e,ValueError) else 'Check the amount.','error')
    if a.get('ci') and a.get('co'):
        try:avail=B.search(db,a['ci'],a['co'],a.get('adults',1),a.get('children',0))
        except B.Invalid as e:flash(str(e),'error')
    return render_template('admin/reception.html',arr=R.booking_rows(db,status='confirmed',ci_from=t,ci_to=t),dep=[r for r in R.booking_rows(db,status='checked_in',limit=300) if r['check_out']<=t],inh=R.booking_rows(db,status='checked_in',limit=300),pend=R.booking_rows(db,status='pending',limit=50),board=B.room_status_board(db),avail=avail,a=a,errs=errs,today=t)
# ---- reports
@bp.get('/reports')
@require('reports')
def reports():
    db=get_db();a=request.args;end=a.get('to') or today().isoformat();start=a.get('from') or (today()-dtm.timedelta(days=29)).isoformat();grp=a.get('group','day') if a.get('group') in('day','month','year') else 'day'
    try:d(start);d(end)
    except Exception:flash('Choose valid dates.','error');start=(today()-dtm.timedelta(days=29)).isoformat();end=today().isoformat()
    return render_template('admin/reports.html',s=R.summary(db,start,end),ser=R.series(db,start,end,grp),expenses=R.expense_rows(db,start,end),start=start,end=end,grp=grp,today=today().isoformat(),svg=R.svg_bars)
@bp.get('/reports/export.<fmt>')
@require('reports')
def reports_export(fmt):
    db=get_db();a=request.args;start=a.get('from',today().isoformat());end=a.get('to',today().isoformat());rows=R.booking_rows(db,ci_from=start,ci_to=end,limit=5000)
    if fmt=='xlsx':return Response(R.xlsx(rows,R.summary(db,start,end)),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename=report.xlsx'})
    if fmt=='pdf':return Response(R.pdf(rows,f'Vijay Hospitality report {start} to {end}'),mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename=report.pdf'})
    abort(404)
@bp.post('/expenses')
@require('reports')
def expense_add():
    f=request.form;db=get_db()
    try:
        kind=f.get('kind','');category=' '.join(f.get('category','').split())[:40];description=' '.join(f.get('description','').split())[:200];amount=_money(f.get('amount'));incurred=d(f.get('incurred_on','')).isoformat()
        if kind not in('operating','capital') or not category or not description or not amount:raise ValueError
        if incurred>today().isoformat():raise ValueError
        eid=db.execute('INSERT INTO expense_entries(kind,category,description,amount_paise,incurred_on,created_by) VALUES(?,?,?,?,?,?)',(kind,category,description,amount,incurred,g.user['id'])).lastrowid
        B.audit(db,g.user,'expense_added','expense',eid,f'{kind}: {amount} paise, {incurred}');flash('Financial entry recorded.','ok')
    except (ValueError,TypeError,KeyError):flash('Enter a valid type, category, description, amount and date (today or earlier).','error')
    return redirect(url_for('admin.reports',**{'from':request.form.get('from',''),'to':request.form.get('to',''),'group':request.form.get('group','day')}))
@bp.post('/expenses/<int:eid>/delete')
@require('reports')
def expense_delete(eid):
    db=get_db();row=db.execute('SELECT kind,category,amount_paise FROM expense_entries WHERE id=?',(eid,)).fetchone()
    if row:
        db.execute('DELETE FROM expense_entries WHERE id=?',(eid,));B.audit(db,g.user,'expense_deleted','expense',eid,f'{row["kind"]}: {row["category"]}, {row["amount_paise"]} paise');flash('Financial entry removed.','ok')
    return back('admin.reports')
# ---- users, settings, audit, conversations
@bp.route('/users',methods=['GET','POST'])
@require('users')
def users():
    db=get_db()
    if request.method=='POST':
        f=request.form;pw=f.get('password','');prob=password_problem(pw)
        if f.get('role') not in('admin','receptionist','owner') or (f.get('role')=='owner' and g.user['role']!='owner'):flash('Choose a valid role.','error')
        elif prob:flash(prob,'error')
        else:
            try:db.execute('INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',(f['email'].strip().lower(),f['name'].strip()[:60],hash_password(pw),f['role']));B.audit(db,g.user,'user_created','user',f['email']);flash('User created.','ok')
            except Exception:flash('That email already exists or the details are invalid.','error')
        return redirect(url_for('admin.users'))
    return render_template('admin/users.html',users=db.execute('SELECT id,email,name,role,active,created_at FROM users ORDER BY id').fetchall())
@bp.post('/users/<int:uid>/toggle')
@require('users')
def user_toggle(uid):
    if uid==g.user['id']:flash('You cannot deactivate your own account.','error')
    else:get_db().execute('UPDATE users SET active=1-active WHERE id=?',(uid,));B.audit(get_db(),g.user,'user_toggled','user',uid)
    return redirect(url_for('admin.users'))
@bp.post('/users/<int:uid>/password')
@require('users')
def user_password(uid):
    pw=request.form.get('password','');prob=password_problem(pw)
    if prob:flash(prob,'error')
    else:get_db().execute('UPDATE users SET password_hash=?,failed_logins=0,locked_until=NULL WHERE id=?',(hash_password(pw),uid));B.audit(get_db(),g.user,'password_reset','user',uid);flash('Password updated.','ok')
    return redirect(url_for('admin.users'))
@bp.route('/settings',methods=['GET','POST'])
@require('settings')
def settings():
    db=get_db()
    if request.method=='POST':
        f=request.form
        for p in db.execute('SELECT id FROM properties').fetchall():
            i=p['id'];db.execute('UPDATE properties SET name=?,address=?,phone=?,email=?,whatsapp=?,map_url=?,description=?,active=? WHERE id=?',tuple(f.get(f'{k}_{i}','').strip()[:400] or None for k in('name','address','phone','email','whatsapp','map_url','description'))+(1 if f.get(f'active_{i}') else 0,i))
        try:
            ap=int(f.get('advance_percent','100'))
            if not 1<=ap<=100:raise ValueError
            set_setting(db,'advance_percent',ap)
        except ValueError:flash('Advance percentage must be 1 to 100.','error');return redirect(url_for('admin.settings'))
        B.audit(db,g.user,'settings_saved','settings','');flash('Settings saved.','ok');return redirect(url_for('admin.settings'))
    return render_template('admin/settings.html',props=db.execute('SELECT * FROM properties').fetchall(),adv=setting(db,'advance_percent','100'),c=current_app.config,rz=P.configured())
@bp.get('/audit')
@require('audit')
def audit():return render_template('admin/audit.html',logs=get_db().execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 300').fetchall())
@bp.get('/enquiries')
@require('enquiries')
def enquiries():
    db=get_db();status=request.args.get('status','')
    if status not in('','new','contacted','closed'):status=''
    sql='SELECT e.*,p.name pname FROM guest_enquiries e LEFT JOIN properties p ON p.id=e.property_id';args=()
    if status:sql+=' WHERE e.status=?';args=(status,)
    rows=db.execute(sql+' ORDER BY CASE e.status WHEN \'new\' THEN 0 WHEN \'contacted\' THEN 1 ELSE 2 END,e.created_at DESC LIMIT 300',args).fetchall()
    return render_template('admin/enquiries.html',rows=rows,status=status,counts={s:db.execute('SELECT COUNT(*) FROM guest_enquiries WHERE status=?',(s,)).fetchone()[0] for s in('new','contacted','closed')})
@bp.post('/enquiries/<int:eid>/status')
@require('enquiries')
def enquiry_status(eid):
    status=request.form.get('status','')
    if status not in('new','contacted','closed'):abort(400)
    db=get_db();row=db.execute('SELECT status,kind,name FROM guest_enquiries WHERE id=?',(eid,)).fetchone()
    if not row:abort(404)
    db.execute('UPDATE guest_enquiries SET status=?,updated_at=? WHERE id=?',(status,now_s(),eid));B.audit(db,g.user,'guest_enquiry_status_changed','guest_enquiry',eid,f'{row["status"]} to {status}: {row["kind"]} from {row["name"]}');flash('Enquiry status updated.','ok')
    return redirect(url_for('admin.enquiries',status=request.form.get('filter','')))
@bp.get('/conversations')
@require('conversations')
def conversations():return render_template('admin/conversations.html',rows=get_db().execute("SELECT c.*,(SELECT COUNT(*) FROM ai_messages m WHERE m.conversation_id=c.id) n FROM ai_conversations c ORDER BY (c.status='handover') DESC,c.updated_at DESC LIMIT 100").fetchall())
@bp.get('/conversations/<int:cid>')
@require('conversations')
def conversation(cid):
    db=get_db();c=db.execute('SELECT * FROM ai_conversations WHERE id=?',(cid,)).fetchone()
    if not c:abort(404)
    return render_template('admin/conversation.html',c=c,msgs=db.execute("SELECT * FROM ai_messages WHERE conversation_id=? AND role!='tool' AND (content IS NOT NULL AND content!='') ORDER BY id",(cid,)).fetchall())
@bp.post('/conversations/<int:cid>/close')
@require('conversations')
def conversation_close(cid):get_db().execute("UPDATE ai_conversations SET status='closed',updated_at=? WHERE id=?",(now_s(),cid));return redirect(url_for('admin.conversations'))
