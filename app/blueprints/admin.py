import os,secrets,io,datetime as dtm,json,hashlib,hmac,sqlite3
from decimal import Decimal,InvalidOperation
from flask import Blueprint,render_template,request,redirect,url_for,flash,session,abort,Response,current_app,g
from PIL import Image
from ..db import get_db,tx,setting,set_setting
from ..security import require,login,rate_limit,hash_password,password_problem
from ..util import today,now_s,d
from ..services import booking as B,payments as P,notify,reports as R,dynamic_pricing as DP,media as M
from .. import configuration as HC
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
    ser=R.series(db,(today()-dtm.timedelta(days=13)).isoformat(),t,'day')
    return render_template('admin/dashboard.html',k=R.dashboard(db),arr=ar,dep=dp,ser=ser,kpi_totals=R.series_totals(ser),period='day',svg=R.svg_bars,upcoming=R.booking_rows(db,status='confirmed',ci_from=(today()+dtm.timedelta(days=1)).isoformat(),limit=8))
# ---- rooms & categories
@bp.get('/rooms')
@require('rooms')
def rooms():
    db=get_db();rs=db.execute("SELECT r.*,c.name cat,p.name pname FROM rooms r JOIN room_categories c ON c.id=r.category_id JOIN properties p ON p.id=r.property_id ORDER BY p.id,r.number").fetchall()
    return render_template('admin/rooms.html',rooms=rs,board={b['id']:b for b in B.room_status_board(db)},cats=db.execute('SELECT c.*,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id ORDER BY p.id,c.name').fetchall(),props=db.execute('SELECT * FROM properties').fetchall())
def _money(v):
    v=(v or '').strip()
    if not v:return None
    amount=Decimal(v)
    if not amount.is_finite() or amount<=0 or amount>Decimal('10000000') or amount!=amount.quantize(Decimal('0.01')):raise ValueError
    return int(amount*100)
def _category_unpublish(db,category_id,fields):
    allowed={'details':'details_status','inventory':'inventory_status','occupancy':'occupancy_status','rates':'rates_status','tax':'tax_status'}
    columns=[allowed[field] for field in fields if field in allowed]
    if not columns:return
    values=','.join(f"{column}='pending'" for column in columns)
    db.execute(f"UPDATE room_categories SET {values},publication_status='unpublished',updated_at=CURRENT_TIMESTAMP WHERE id=?",(category_id,))
def _unpublish_room_data(db,rates=False,occupancy=False,rooms=False,photos=False,tax=False):
    for changed,key in ((rates,'room_rates_confirmed'),(occupancy,'room_occupancy_confirmed'),(rooms,'room_inventory_confirmed'),(photos,'room_photos_confirmed')):
        if changed:set_setting(db,key,'0')
    columns=[]
    if rates:columns.append("rates_status='pending'")
    if occupancy:columns.append("occupancy_status='pending'")
    if rooms:columns.append("inventory_status='pending'")
    if photos:columns.append("photos_status='pending'")
    if tax:columns.append("tax_status='pending'")
    if columns:
        if tax and not rates and not occupancy and not rooms and not photos:
            db.execute("UPDATE room_categories SET "+','.join(columns)+",publication_status='unpublished',updated_at=CURRENT_TIMESTAMP WHERE tax_percent IS NULL")
        else:
            db.execute("UPDATE room_categories SET "+','.join(columns)+",publication_status='unpublished',updated_at=CURRENT_TIMESTAMP")
    if rates:db.execute('UPDATE pricing_rules SET approved=0')
@bp.post('/rooms/new')
@require('rooms')
def room_new():
    db=get_db();f=request.form
    try:
        cat=db.execute('SELECT * FROM room_categories WHERE id=?',(int(f['category_id']),)).fetchone();num=f['number'].strip()
        if not num or not cat:raise ValueError
        rid=db.execute('INSERT INTO rooms(property_id,category_id,number,floor,price_override_paise,description) VALUES(?,?,?,?,?,?)',(cat['property_id'],cat['id'],num[:10],f.get('floor','')[:10],_money(f.get('price')),f.get('description','')[:500])).lastrowid
        _category_unpublish(db,cat['id'],{'inventory'})
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
            previous_category=int(r['category_id'])
            with tx(db):
                db.execute('UPDATE rooms SET category_id=?,number=?,floor=?,price_override_paise=?,description=?,status=? WHERE id=?',(int(f['category_id']),f['number'].strip()[:10],f.get('floor','')[:10],_money(f.get('price')),f.get('description','')[:500],'active' if f.get('status')=='active' else 'inactive',rid))
                db.execute('DELETE FROM room_amenities WHERE room_id=?',(rid,))
                for a in {x.strip()[:40] for x in f.get('amenities','').split(',') if x.strip()}:
                    db.execute('INSERT OR IGNORE INTO amenities(name) VALUES(?)',(a,));db.execute('INSERT INTO room_amenities SELECT ?,id FROM amenities WHERE name=?',(rid,a))
                _category_unpublish(db,previous_category,{'inventory'})
                if int(f['category_id'])!=previous_category:_category_unpublish(db,int(f['category_id']),{'inventory'})
                B.audit(db,g.user,'room_updated','room',rid)
            flash('Room saved.','ok')
        except Exception:flash('Could not save. Check the room number is unique and the price is valid.','error')
        return redirect(url_for('admin.room_edit',rid=rid))
    nights=db.execute("SELECT n.night,n.reason FROM room_nights n WHERE n.room_id=? AND n.reservation_id IS NULL AND n.night>=? ORDER BY n.night",(rid,today().isoformat())).fetchall()
    return render_template('admin/room_edit.html',r=r,cats=db.execute('SELECT * FROM room_categories WHERE property_id=?',(r['property_id'],)).fetchall(),amen=', '.join(B.amenities(db,rid)),imgs=db.execute('SELECT * FROM room_images WHERE room_id=? ORDER BY sort,id',(rid,)).fetchall(),panorama=db.execute('SELECT path,alt FROM room_panorama WHERE room_id=?',(rid,)).fetchone(),blocks=nights)
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
        db.execute('INSERT INTO room_images(room_id,path,alt,sort) VALUES(?,?,?,?)',(rid,'uploads/'+name,request.form.get('alt','')[:150],int(db.execute('SELECT COALESCE(MAX(sort),0)+1 FROM room_images WHERE room_id=?',(rid,)).fetchone()[0])));db.execute("UPDATE room_categories SET photos_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT category_id FROM rooms WHERE id=?)",(rid,));flash('Photo uploaded. It will remain hidden until room photos are approved.','ok')
    except Exception as e:flash(str(e) if isinstance(e,ValueError) else 'That file is not a valid image.','error')
    return back('admin.rooms')
@bp.post('/rooms/<int:rid>/image/<int:iid>/delete')
@require('rooms')
def room_image_delete(rid,iid):
    db=get_db();r=db.execute('SELECT path FROM room_images WHERE id=? AND room_id=?',(iid,rid)).fetchone()
    if r:
        db.execute('DELETE FROM room_images WHERE id=?',(iid,))
        db.execute("UPDATE room_categories SET photos_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT category_id FROM rooms WHERE id=?)",(rid,))
        try:os.remove(os.path.join(current_app.config['UPLOAD_DIR'],os.path.basename(r[0])))
        except OSError:pass
    return back('admin.rooms')
@bp.post('/rooms/<int:rid>/panorama')
@require('rooms')
def room_panorama_upload(rid):
    db=get_db();upload=request.files.get('panorama')
    if not db.execute('SELECT 1 FROM rooms WHERE id=?',(rid,)).fetchone():abort(404)
    saved=None
    try:
        saved=M.save_panorama_upload(upload,current_app.config['UPLOAD_DIR'])
        alt=(request.form.get('alt') or '360-degree room panorama')[:150]
        previous=db.execute('SELECT path FROM room_panorama WHERE room_id=?',(rid,)).fetchone()
        committed=False
        try:
            with tx(db):
                db.execute('INSERT INTO room_panorama(room_id,path,alt) VALUES(?,?,?) ON CONFLICT(room_id) DO UPDATE SET path=excluded.path,alt=excluded.alt,created_at=CURRENT_TIMESTAMP',(rid,saved['path'],alt))
                db.execute("UPDATE room_categories SET photos_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT category_id FROM rooms WHERE id=?)",(rid,))
            committed=True
        finally:
            if not committed:M.delete_upload(saved['path'],current_app.config['UPLOAD_DIR'])
        if previous:M.delete_upload(previous['path'],current_app.config['UPLOAD_DIR'])
        flash('Panorama uploaded. It will appear on the public room page after room photos are approved.','ok')
    except (OSError,ValueError,M.MediaError) as e:
        flash(str(e) if isinstance(e,ValueError) else 'The panorama could not be processed.','error')
    return back('admin.rooms')
@bp.post('/rooms/<int:rid>/panorama/delete')
@require('rooms')
def room_panorama_delete(rid):
    db=get_db();row=db.execute('SELECT path FROM room_panorama WHERE room_id=?',(rid,)).fetchone()
    if row:
        db.execute('DELETE FROM room_panorama WHERE room_id=?',(rid,))
        db.execute("UPDATE room_categories SET photos_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=(SELECT category_id FROM rooms WHERE id=?)",(rid,))
        M.delete_upload(row['path'],current_app.config['UPLOAD_DIR'])
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
            name=' '.join(f.get('name','').split())[:40]
            room_type=' '.join(f.get('room_type','').split())[:40] or None
            if not name:raise ValueError('Enter a category display name.')
            rate=_money(f.get('price'))
            adults=int(f['max_adults']) if f.get('max_adults','').strip() else None
            children=int(f['max_children']) if f.get('max_children','').strip() else None
            total=int(f['max_occupancy']) if f.get('max_occupancy','').strip() else None
            tax_text=(f.get('tax_percent','') or '').strip()
            tax=Decimal(tax_text) if tax_text else None
            if tax is not None and (not tax.is_finite() or tax<0 or tax>100 or tax!=tax.quantize(Decimal('0.01'))):raise ValueError('Tax must be from 0 to 100 percent, or left blank to inherit the approved property tax.')
            if adults is not None and not 1<=adults<=12:raise ValueError('Maximum adults must be from 1 to 12, or left pending.')
            if children is not None and not 0<=children<=12:raise ValueError('Maximum children must be from 0 to 12, or left pending.')
            if total is not None and not 1<=total<=24:raise ValueError('Maximum total occupancy must be from 1 to 24, or left pending.')
            if total is not None and adults is not None and total<adults:raise ValueError('Maximum total occupancy cannot be lower than maximum adults.')
            args=(name,room_type,1 if f.get('ac') else 0,f.get('description','')[:400],rate,adults,children,total,float(tax) if tax is not None else None)
            if f.get('id'):
                cid=int(f['id'])
                with tx(db):
                    db.execute("""UPDATE room_categories SET name=?,room_type=?,ac=?,description=?,nightly_rate_paise=?,
                        max_adults_approved=?,max_children_approved=?,max_occupancy=?,tax_percent=?,
                        base_price_paise=COALESCE(?,0),max_adults=COALESCE(?,2),max_children=COALESCE(?,1),
                        active=?,details_status='pending',inventory_status='pending',occupancy_status='pending',
                        rates_status='pending',tax_status='pending',publication_status='unpublished',updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""",args+(rate or 0,adults or 2,children if children is not None else 1,1 if f.get('active') else 0,cid))
                    B.audit(db,g.user,'category_saved','category',cid,name)
            else:
                property_id=int(f.get('property_id',''))
                with tx(db):
                    cid=db.execute("""INSERT INTO room_categories(property_id,name,room_type,ac,description,nightly_rate_paise,
                        max_adults_approved,max_children_approved,max_occupancy,tax_percent,base_price_paise,max_adults,max_children,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",(property_id,name,room_type,args[2],args[3],rate,adults,children,total,args[8],rate or 0,adults or 2,children if children is not None else 1)).lastrowid
                    B.audit(db,g.user,'category_created','category',cid,name)
            flash('Category saved. Details, rates, occupancy, tax and inventory remain unpublished until separately approved.','ok')
        except (ValueError,TypeError,InvalidOperation) as e:
            flash(str(e) or 'Could not save the category. Check the supplied values.','error')
        except Exception:
            flash('Could not save the category. Check that the property/category is valid and the name is unique.','error')
        return redirect(url_for('admin.categories'))
    tax_percent=setting(db,'tax_percent')
    tax_confirmed=B.tax_configuration(db)[1]
    if tax_percent is None and current_app.config.get('TAX_PERCENT_CONFIGURED'):
        tax_percent=f"{current_app.config['TAX_PERCENT']:.2f}"
    cats=db.execute("""SELECT c.*,p.name pname,(SELECT COUNT(*) FROM rooms r WHERE r.category_id=c.id AND r.status='active') inventory_count,
        (SELECT COUNT(*) FROM room_images i JOIN rooms r ON r.id=i.room_id WHERE r.category_id=c.id) photo_count
        FROM room_categories c JOIN properties p ON p.id=c.property_id ORDER BY p.id,c.name""").fetchall()
    return render_template('admin/categories.html',cats=cats,props=db.execute('SELECT * FROM properties').fetchall(),rules=db.execute('SELECT x.*,c.name cat FROM pricing_rules x JOIN room_categories c ON c.id=x.category_id ORDER BY x.start_date').fetchall(),tax_percent=tax_percent or '',tax_confirmed=tax_confirmed)

@bp.post('/categories/<int:category_id>/approval')
@require('rooms')
def category_approval(category_id):
    db=get_db();f=request.form
    category=db.execute('SELECT * FROM room_categories WHERE id=?',(category_id,)).fetchone()
    if not category:abort(404)
    requested={key:'approved' if f.get('approve_'+key)=='1' else 'pending' for key in ('details','inventory','occupancy','rates','tax','photos')}
    try:
        inventory=db.execute("SELECT COUNT(*) FROM rooms WHERE category_id=? AND property_id=? AND status='active'",(category_id,category['property_id'])).fetchone()[0]
        photos=db.execute("SELECT COUNT(*) FROM room_images i JOIN rooms r ON r.id=i.room_id WHERE r.category_id=?",(category_id,)).fetchone()[0]
        if f.get('publish')=='1' and (not category['active'] or not db.execute('SELECT 1 FROM properties WHERE id=? AND active=1',(category['property_id'],)).fetchone()):raise ValueError('Activate the category and its property before publication.')
        if requested['details']=='approved' and not category['room_type']:raise ValueError('Add the room type before approving category details.')
        if requested['inventory']=='approved' and not inventory:raise ValueError('Add actual active room numbers before approving inventory.')
        if requested['occupancy']=='approved':
            if not category['max_adults_approved'] or category['max_children_approved'] is None or not category['max_occupancy']:raise ValueError('Enter approved adult, child and total occupancy limits first.')
            if category['max_occupancy']<max(category['max_adults_approved'],category['max_children_approved']):raise ValueError('Maximum total occupancy is lower than a guest limit.')
        if requested['rates']=='approved' and not category['nightly_rate_paise']:raise ValueError('Enter an approved nightly rate first.')
        if requested['tax']=='approved':
            if category['tax_percent'] is None and not B.tax_configuration(db)[1]:raise ValueError('Set an approved category tax or approve the property tax before approving tax.')
        if requested['photos']=='approved' and not photos:raise ValueError('Upload an authentic room photo before approving photographs. No photo is required to publish a category.')
        publish=f.get('publish')=='1'
        if publish:
            required=('details','inventory','occupancy','rates','tax')
            if any(requested[key]!='approved' for key in required):raise ValueError('Approve category details, inventory, occupancy, rates and tax before publication.')
            if category['tax_percent'] is not None and requested['tax']!='approved':raise ValueError('The category tax must be approved before publication.')
        with tx(db):
            db.execute("UPDATE pricing_rules SET approved=? WHERE category_id=?",(1 if requested['rates']=='approved' else 0,category_id))
            db.execute("""UPDATE room_categories SET details_status=?,inventory_status=?,occupancy_status=?,rates_status=?,tax_status=?,photos_status=?,
                publication_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                tuple(requested[key] for key in ('details','inventory','occupancy','rates','tax','photos'))+('published' if publish else 'unpublished',category_id))
            for key,setting_key in (('inventory','room_inventory_confirmed'),('occupancy','room_occupancy_confirmed'),('rates','room_rates_confirmed')):
                if requested[key]=='approved':set_setting(db,setting_key,'1')
            B.audit(db,g.user,'category_approval_updated','category',category_id,
                '; '.join(f'{key}={value}' for key,value in requested.items())+f"; publication={'published' if publish else 'unpublished'}")
        flash('Category approval and publication statuses saved.','ok')
    except ValueError as e:flash(str(e),'error')
    return redirect(url_for('admin.categories'))

def _inventory_categories(db,form=None):
    rows=[dict(r) for r in db.execute('SELECT c.id,c.name,c.room_type,c.ac,c.nightly_rate_paise,c.max_adults_approved max_adults,c.max_children_approved max_children,c.max_occupancy,c.details_status,c.property_id,p.name pname FROM room_categories c JOIN properties p ON p.id=c.property_id WHERE c.active=1 AND p.active=1 ORDER BY p.id,c.ac DESC,c.name')]
    for row in rows:
        room_key=f"rooms_{row['id']}";rate_key=f"rate_{row['id']}"
        existing=db.execute('SELECT id,number,status,category_id,price_override_paise FROM rooms WHERE property_id=? AND category_id=? ORDER BY number',(row['property_id'],row['id'])).fetchall()
        row['existing_rooms']=[dict(x) for x in existing]
        row['room_numbers']='\n'.join(x['number'] for x in existing)
        row['rate_value']=f"{row['nightly_rate_paise']/100:.2f}" if row['nightly_rate_paise'] else ''
        if form is not None:
            if rate_key in form:row['rate_value']=form.get(rate_key,'')
            if room_key in form:row['room_numbers']='\n'.join(form.getlist(room_key))
            for field in ('max_adults','max_children','max_occupancy'):
                key=f"{field}_{row['id']}"
                if key in form:row[field]=form.get(key,'')
    return rows

def _inventory_payload(db,form):
    errors=[];rows=_inventory_categories(db);expected={str(r['id']) for r in rows};submitted=form.getlist('category_id')
    if not rows:errors.append('No active room categories exist under active properties.')
    if len(submitted)!=len(set(submitted)) or set(submitted)!=expected:errors.append('Submit each active property/category exactly once.')
    proposal=[];seen_numbers={};rates_confirmed=form.get('confirm_rates')=='1';occupancy_confirmed=form.get('confirm_occupancy')=='1';inventory_confirmed=form.get('confirm_rooms')=='1';details_confirmed=form.get('confirm_details')=='1'
    for category in rows:
        cid=category['id'];pid=category['property_id'];name=category['name'];pname=category['pname']
        rate_text=(form.get(f'rate_{cid}','') or '').strip()
        rate_paise=None
        if rate_text:
            try:
                rate=Decimal(rate_text)
                if not rate.is_finite() or rate<=0 or rate>Decimal('10000000') or rate!=rate.quantize(Decimal('0.01')):raise ValueError
                rate_paise=int(rate*100)
            except (InvalidOperation,ValueError):errors.append(f'{pname} · {name}: enter a nightly rate with at most two decimal places, or leave it pending.')
        elif rates_confirmed:errors.append(f'{pname} · {name}: an approved nightly rate is required to confirm pricing.')
        occupancy={}
        for field,label,low,high in (('max_adults','maximum adults',1,12),('max_children','maximum children',0,12),('max_occupancy','maximum total occupancy',1,24)):
            text=(form.get(f'{field}_{cid}','') or '').strip()
            try:
                number=int(text) if text else None
                if number is not None and not low<=number<=high:raise ValueError
                if number is None and occupancy_confirmed:raise ValueError
            except (TypeError,ValueError):
                errors.append(f'{pname} · {name}: {label} must be from {low} to {high}, or left blank and pending.')
                number=None
            occupancy[field]=number
        if occupancy_confirmed and all(occupancy.get(k) is not None for k in ('max_adults','max_children','max_occupancy')) and occupancy['max_occupancy']<max(occupancy['max_adults'],occupancy['max_children']):
            errors.append(f'{pname} · {name}: maximum total occupancy cannot be lower than an approved guest limit.')
        room_numbers=[]
        for value in form.getlist(f'rooms_{cid}'):
            for raw in value.replace(',','\n').splitlines():
                number=raw.strip()
                if not number:continue
                if len(number)>10:
                    errors.append(f'{pname} · {name}: room number “{number}” exceeds 10 characters.')
                    continue
                key=(pid,number.casefold())
                if key in seen_numbers:
                    errors.append(f'{pname}: duplicate room number “{number}” appears in more than one category or row.')
                    continue
                seen_numbers[key]=cid
                existing=db.execute('SELECT id,category_id,status,price_override_paise FROM rooms WHERE property_id=? AND lower(number)=lower(?)',(pid,number)).fetchone()
                if existing and existing['category_id']!=cid:
                    errors.append(f'{pname}: room {number} is already assigned to another category. Reassign it in Admin → Rooms first.')
                    continue
                room_numbers.append({'number':number,'existing_id':existing['id'] if existing else None,'was_inactive':bool(existing and existing['status']!='active'),'has_price_override':bool(existing and existing['price_override_paise'])})
        if inventory_confirmed and not room_numbers and not category['room_numbers']:errors.append(f'{pname} · {name}: enter actual room numbers before confirming inventory.')
        if details_confirmed and not category['room_type']:errors.append(f'{pname} · {name}: enter a management-confirmed room type before confirming details.')
        if form.get('confirm_room_photos')=='1':
            photo_count=db.execute('SELECT COUNT(*) FROM room_images i JOIN rooms r ON r.id=i.room_id WHERE r.category_id=?',(cid,)).fetchone()[0]
            if not photo_count:errors.append(f'{pname} · {name}: upload authentic room photos before confirming photographs.')
        proposal.append({'category_id':cid,'property_id':pid,'property':pname,'category':name,'room_type':category['room_type'],'ac':bool(category['ac']),'rate_paise':rate_paise,'max_adults':occupancy['max_adults'],'max_children':occupancy['max_children'],'max_occupancy':occupancy['max_occupancy'],'rooms':room_numbers})
    tax_text=(form.get('tax_percent','') or '').strip();tax_confirmed=form.get('confirm_tax')=='1'
    if not tax_text:
        if tax_confirmed:errors.append('Enter the management-approved tax percentage; a blank value cannot be confirmed as 0%.')
        tax_value=''
    else:
        try:
            tax=Decimal(tax_text)
            if not tax.is_finite() or not 0<=tax<=100 or tax!=tax.quantize(Decimal('0.01')):raise ValueError
            tax_value=format(tax,'f')
        except (InvalidOperation,ValueError):
            errors.append('Tax must be a percentage from 0 to 100 with at most two decimal places, or remain blank and pending.')
            tax_value=''
    return {
        'categories':proposal,
        'tax_percent':tax_value,
        'tax_confirmed':tax_confirmed,
        'details_confirmed':details_confirmed,
        'rates_confirmed':rates_confirmed,
        'occupancy_confirmed':occupancy_confirmed,
        'rooms_confirmed':inventory_confirmed,
        'photos_confirmed':form.get('confirm_room_photos')=='1'
    },errors

def _inventory_setup_page(db,form=None,errors=None,status=200):
    tax_percent=setting(db,'tax_percent')
    if tax_percent is None and current_app.config.get('TAX_PERCENT_CONFIGURED'):tax_percent=f"{current_app.config['TAX_PERCENT']:.2f}"
    if form is not None and 'tax_percent' in form:tax_percent=form.get('tax_percent','')
    tax_confirmed=B.tax_configuration(db)[1]
    config=B.inventory_configuration(db)
    categories=_inventory_categories(db,form)
    details_confirmed=bool(categories) and all(category.get('details_status')=='approved' for category in categories)
    if form is not None:
        tax_confirmed=form.get('confirm_tax')=='1'
        details_confirmed=form.get('confirm_details')=='1'
        config={
            'rates_confirmed':form.get('confirm_rates')=='1',
            'occupancy_confirmed':form.get('confirm_occupancy')=='1',
            'rooms_confirmed':form.get('confirm_rooms')=='1',
            'photos_confirmed':form.get('confirm_room_photos')=='1'
        }
    return render_template('admin/inventory_setup.html',categories=categories,tax_percent=tax_percent or '',tax_confirmed=tax_confirmed,details_confirmed=details_confirmed,errors=errors or [],**config),status

@bp.get('/inventory-setup')
@require('rooms')
def inventory_setup():return _inventory_setup_page(get_db())

@bp.post('/inventory-setup/review')
@require('rooms')
def inventory_setup_review():
    db=get_db();proposal,errors=_inventory_payload(db,request.form)
    if errors:return _inventory_setup_page(db,request.form,errors,400)
    token=secrets.token_urlsafe(24);fingerprint=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    session['inventory_review']={'token':token,'fingerprint':fingerprint}
    return render_template('admin/inventory_review.html',proposal=proposal,token=token)

@bp.post('/inventory-setup/apply')
@require('rooms')
def inventory_setup_apply():
    db=get_db();proposal,errors=_inventory_payload(db,request.form)
    review=session.get('inventory_review') or {};token=request.form.get('review_token','')
    fingerprint=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if request.form.get('action')=='edit':
        session.pop('inventory_review',None)
        return _inventory_setup_page(db,request.form,errors,400 if errors else 200)
    if errors or not token or not hmac.compare_digest(token,review.get('token','')) or not hmac.compare_digest(fingerprint,review.get('fingerprint','')):
        session.pop('inventory_review',None)
        if not errors:errors=['Review token expired or configuration changed. Review the inventory again before applying.']
        return _inventory_setup_page(db,request.form,errors,400)
    try:
        with tx(db):
            for category in proposal['categories']:
                current=db.execute('SELECT id FROM room_categories WHERE id=? AND property_id=? AND active=1',(category['category_id'],category['property_id'])).fetchone()
                if not current or not db.execute('SELECT 1 FROM properties WHERE id=? AND active=1',(category['property_id'],)).fetchone():raise ValueError('A property or category changed after review. Review the inventory again.')
                db.execute("""UPDATE room_categories SET nightly_rate_paise=?,max_adults_approved=?,max_children_approved=?,max_occupancy=?,
                    base_price_paise=COALESCE(?,0),max_adults=COALESCE(?,2),max_children=COALESCE(?,1),updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND property_id=?""",(category['rate_paise'],category['max_adults'],category['max_children'],category['max_occupancy'],category['rate_paise'],category['max_adults'],category['max_children'],category['category_id'],category['property_id']))
                for room in category['rooms']:
                    existing=db.execute('SELECT id,category_id FROM rooms WHERE property_id=? AND lower(number)=lower(?)',(category['property_id'],room['number'])).fetchone()
                    if existing and existing['category_id']!=category['category_id']:raise ValueError(f"Room {room['number']} was assigned to another category after review.")
                    if existing:db.execute("UPDATE rooms SET status='active' WHERE id=?",(existing['id'],))
                    else:db.execute('INSERT INTO rooms(property_id,category_id,number) VALUES(?,?,?)',(category['property_id'],category['category_id'],room['number']))
                statuses={
                    'details':'approved' if proposal['details_confirmed'] else 'pending',
                    'inventory':'approved' if proposal['rooms_confirmed'] else 'pending',
                    'occupancy':'approved' if proposal['occupancy_confirmed'] else 'pending',
                    'rates':'approved' if proposal['rates_confirmed'] else 'pending',
                    'tax':'approved' if proposal['tax_confirmed'] else 'pending',
                    'photos':'approved' if proposal['photos_confirmed'] else 'pending'}
                count=db.execute("SELECT COUNT(*) FROM rooms WHERE category_id=? AND status='active'",(category['category_id'],)).fetchone()[0]
                ready=all(statuses[k]=='approved' for k in ('details','inventory','occupancy','rates','tax')) and count>0 and category['rate_paise'] and category['max_adults'] is not None and category['max_children'] is not None and category['max_occupancy'] is not None
                db.execute("""UPDATE room_categories SET details_status=?,inventory_status=?,occupancy_status=?,rates_status=?,tax_status=?,photos_status=?,
                    publication_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    tuple(statuses[k] for k in ('details','inventory','occupancy','rates','tax','photos'))+('published' if ready else 'unpublished',category['category_id']))
                db.execute("UPDATE pricing_rules SET approved=? WHERE category_id=?",(1 if proposal['rates_confirmed'] else 0,category['category_id']))
            set_setting(db,'tax_percent',proposal['tax_percent'])
            set_setting(db,'tax_percent_confirmed','1' if proposal['tax_confirmed'] else '0')
            set_setting(db,'room_rates_confirmed','1' if proposal['rates_confirmed'] else '0')
            set_setting(db,'room_occupancy_confirmed','1' if proposal['occupancy_confirmed'] else '0')
            set_setting(db,'room_inventory_confirmed','1' if proposal['rooms_confirmed'] else '0')
            set_setting(db,'room_photos_confirmed','1' if proposal['photos_confirmed'] else '0')
            B.audit(db,g.user,'inventory_setup_applied','settings','inventory',f"{len(proposal['categories'])} categories; rooms {'confirmed' if proposal['rooms_confirmed'] else 'pending'}; rates {'confirmed' if proposal['rates_confirmed'] else 'pending'}; occupancy {'confirmed' if proposal['occupancy_confirmed'] else 'pending'}; photos {'confirmed' if proposal['photos_confirmed'] else 'pending'}; tax {'confirmed' if proposal['tax_confirmed'] else 'pending'}")
        session.pop('inventory_review',None)
        flash('Inventory configuration applied. Management confirmations were saved.' if proposal['rates_confirmed'] and proposal['occupancy_confirmed'] and proposal['rooms_confirmed'] and proposal['photos_confirmed'] and proposal['tax_confirmed'] else 'Inventory configuration applied. Any unconfirmed room numbers, rates, occupancy, room photos or tax remain unpublished. Online booking is disabled until required approvals are complete.','ok' if proposal['rates_confirmed'] and proposal['occupancy_confirmed'] and proposal['rooms_confirmed'] and proposal['photos_confirmed'] and proposal['tax_confirmed'] else 'error')
        return redirect(url_for('admin.inventory_setup'))
    except Exception as e:
        session.pop('inventory_review',None);flash(str(e) or 'Could not apply inventory configuration. No changes were saved.','error')
        return redirect(url_for('admin.inventory_setup'))

@bp.post('/tax')
@require('rooms')
def tax_settings():
    db=get_db();f=request.form
    try:
        confirmed=bool(f.get('confirm_tax'))
        tax_text=(f.get('tax_percent','') or '').strip()
        if not tax_text:
            if confirmed:raise ValueError
            value=''
        else:
            value=Decimal(tax_text)
            if not value.is_finite() or value<0 or value>100 or value!=value.quantize(Decimal('0.01')):raise ValueError
            value=format(value,'f')
        set_setting(db,'tax_percent',value);set_setting(db,'tax_percent_confirmed','1' if confirmed else '0')
        if confirmed:
            db.execute("""UPDATE room_categories SET tax_status='approved',
                publication_status=CASE WHEN details_status='approved' AND inventory_status='approved' AND occupancy_status='approved' AND rates_status='approved' THEN 'published' ELSE 'unpublished' END,
                updated_at=CURRENT_TIMESTAMP WHERE tax_percent IS NULL""")
        else:_unpublish_room_data(db,tax=True)
        B.audit(db,g.user,'tax_settings_saved','settings','tax_percent',f"{'confirmed' if confirmed else 'unconfirmed'} {value}%")
        flash('Tax percentage confirmed and saved.' if confirmed else 'Tax value saved as unconfirmed; online bookings remain disabled.','ok' if confirmed else 'error')
    except (InvalidOperation,ValueError):flash('Enter a tax percentage from 0 to 100. Confirm it only after management approval.','error')
    return redirect(url_for('admin.categories'))
@bp.post('/pricing')
@require('rooms')
def pricing_add():
    f=request.form;db=get_db()
    try:
        category_id=int(f['category_id'])
        if f['end']<f['start']:raise ValueError
        db.execute('INSERT INTO pricing_rules(category_id,label,start_date,end_date,price_paise) VALUES(?,?,?,?,?)',(category_id,f.get('label','')[:60],f['start'],f['end'],_money(f['price'])))
        _category_unpublish(db,category_id,{'rates'});flash('Seasonal rate added as pending. Approve category rates before publishing.','ok')
    except Exception:flash('Check the dates and price.','error')
    return back('admin.categories')
@bp.post('/pricing/<int:xid>/delete')
@require('rooms')
def pricing_delete(xid):
    db=get_db();row=db.execute('SELECT category_id FROM pricing_rules WHERE id=?',(xid,)).fetchone()
    if row:
        db.execute('DELETE FROM pricing_rules WHERE id=?',(xid,));_category_unpublish(db,row['category_id'],{'rates'})
    return back('admin.categories')
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
            _unpublish_room_data(db,rates=True)
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
            _unpublish_room_data(db,rates=True)
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
        _unpublish_room_data(db,rates=True)
        B.audit(db,g.user,'dynamic_rule_toggled','dynamic_rule',rule_id,rule['name'])
    flash('Dynamic pricing rule updated.','ok');return redirect(url_for('admin.dynamic_pricing'))
@bp.post('/pricing/dynamic/rules/<int:rule_id>/delete')
@require('rooms')
def dynamic_rule_delete(rule_id):
    db=get_db();rule=db.execute('SELECT name FROM dynamic_rules WHERE id=?',(rule_id,)).fetchone()
    if not rule:abort(404)
    with tx(db):
        db.execute('DELETE FROM dynamic_rules WHERE id=?',(rule_id,))
        _unpublish_room_data(db,rates=True)
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
        if saved_count and kind=='room':
            _unpublish_room_data(db,photos=True)
            flash(f'{saved_count} room image{"s" if saved_count!=1 else ""} uploaded. Hidden until management reapproves room photos.','ok')
        elif saved_count:flash(f'{saved_count} image{"s" if saved_count!=1 else ""} uploaded.','ok')
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
            if kind=='room':_unpublish_room_data(db,photos=True)
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
        if row['kind']=='room':_unpublish_room_data(db,photos=True)
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
    try:
        if d(start)>d(end):raise ValueError
    except Exception:flash('Choose valid dates.','error');start=(today()-dtm.timedelta(days=29)).isoformat();end=today().isoformat()
    ser=R.series(db,start,end,grp)
    return render_template('admin/reports.html',s=R.summary(db,start,end),ser=ser,kpi_totals=R.series_totals(ser),start=start,end=end,grp=grp,period=grp,svg=R.svg_bars)
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
@bp.get('/configuration')
@require('settings')
def configuration_page():
    db=get_db();config=HC.load(db,current_app.config);inventory=B.inventory_configuration(db)
    _,tax_confirmed=B.tax_configuration(db)
    row=db.execute('SELECT setup_complete FROM hotel_configuration WHERE id=1').fetchone()
    checks=[
        ('Hotel identity and brand',bool(config['brand']['name']),url_for('admin.configuration_onboarding')),
        ('Property details',db.execute('SELECT COUNT(*) FROM properties WHERE active=1').fetchone()[0]>0,url_for('admin.settings')),
        ('Room categories and inventory',db.execute('SELECT COUNT(*) FROM rooms WHERE status=\'active\'').fetchone()[0]>0,url_for('admin.rooms')),
        ('Rates approved',inventory['rates_confirmed'],url_for('admin.categories')),
        ('Tax confirmed',tax_confirmed,url_for('admin.categories')),
        ('Payment gateway',P.configured(),url_for('admin.settings')),
        ('AI receptionist',bool(current_app.config['LLM_API_KEY']),url_for('admin.settings')),
    ]
    return render_template('admin/configuration.html',config=config,config_json=json.dumps(config,ensure_ascii=False,indent=2),
        catalog=HC.export_payload(db),checks=checks,setup_complete=bool(row and row['setup_complete']))

@bp.post('/configuration/save')
@require('settings')
def configuration_save():
    try:
        config=HC.validate(request.form.get('configuration',''))
        with tx(get_db()):
            HC.save(get_db(),config,g.user['id'])
            B.audit(get_db(),g.user,'configuration_saved','configuration',1,'Admin configuration editor')
        flash('Configuration saved. Existing reservations and payment records were not modified.','ok')
    except HC.ConfigurationError as exc:
        flash(str(exc),'error')
    return redirect(url_for('admin.configuration_page'))

@bp.get('/configuration/export')
@require('settings')
def configuration_export():
    payload=HC.export_payload(get_db());payload['configuration']=HC.load(get_db(),current_app.config)
    return Response(json.dumps(payload,ensure_ascii=False,indent=2),mimetype='application/json',
        headers={'Content-Disposition':'attachment; filename=hotel-configuration.json','Cache-Control':'no-store'})

def _catalog_matches(db,properties,categories,rooms):
    current=HC.export_payload(db)
    for key,imported in (('properties',properties),('room_categories',categories),('rooms',rooms)):
        if json.dumps(current[key],sort_keys=True)!=json.dumps(imported,sort_keys=True):
            return False
    return True

def _import_catalog(db,properties,categories,rooms):
    property_ids={}
    for prop in properties:
        cur=db.execute('''INSERT INTO properties(slug,name,kind,address,phone,email,whatsapp,map_url,description,active)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(prop['slug'],prop['name'],prop['kind'],prop['address'],prop['phone'],
            prop['email'],prop['whatsapp'],prop['map_url'],prop['description'],prop['active']))
        property_ids[prop['slug']]=cur.lastrowid
    category_ids={}
    for cat in categories:
        if cat['property_slug'] not in property_ids:raise HC.ConfigurationError('Every room category must refer to a property in the same export.')
        int_fields=('base_price_paise','max_adults','max_children')
        for field in int_fields:
            if not isinstance(cat[field],int) or cat[field]<0:raise HC.ConfigurationError(f'room_categories.{field} must be a non-negative integer.')
        if cat['max_adults']<1:raise HC.ConfigurationError('A room category must allow at least one adult.')
        for field in ('nightly_rate_paise','max_adults_approved','max_children_approved','max_occupancy'):
            value=cat[field]
            if value is not None and (not isinstance(value,int) or value<0):raise HC.ConfigurationError(f'room_categories.{field} must be a non-negative integer or null.')
        tax=cat['tax_percent']
        if tax is not None and (not isinstance(tax,(int,float)) or not 0<=tax<=100):raise HC.ConfigurationError('A room category tax percentage must be from 0 to 100.')
        cur=db.execute('''INSERT INTO room_categories(property_id,name,room_type,ac,description,base_price_paise,max_adults,max_children,
            nightly_rate_paise,max_adults_approved,max_children_approved,max_occupancy,currency,tax_percent)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(property_ids[cat['property_slug']],cat['name'],cat['room_type'],
            int(bool(cat['ac'])),cat['description'],cat['base_price_paise'],cat['max_adults'],cat['max_children'],
            cat['nightly_rate_paise'],cat['max_adults_approved'],cat['max_children_approved'],cat['max_occupancy'],
            cat['currency'],tax))
        category_ids[(cat['property_slug'],cat['name'])]=cur.lastrowid
    for room in rooms:
        category_id=category_ids.get((room['property_slug'],room['category_name']))
        property_id=property_ids.get(room['property_slug'])
        if not category_id or not property_id:raise HC.ConfigurationError('Every room must refer to a category and property in the same export.')
        price=room['price_override_paise']
        if price is not None and (not isinstance(price,int) or price<=0):raise HC.ConfigurationError('Room price overrides must be positive integer paise or null.')
        db.execute('''INSERT INTO rooms(property_id,category_id,number,floor,price_override_paise,description,status)
            VALUES(?,?,?,?,?,?,?)''',(property_id,category_id,room['number'],room['floor'],price,room['description'],room['status']))

@bp.post('/configuration/import')
@require('settings')
def configuration_import():
    upload=request.files.get('config_file')
    if not upload:
        flash('Choose a configuration JSON file to import.','error')
        return redirect(url_for('admin.configuration_page'))
    try:
        raw=upload.read(HC.MAX_CONFIG_BYTES+1).decode('utf-8')
        config,properties,categories,rooms=HC.import_payload(raw)
        db=get_db()
        has_history=db.execute('SELECT EXISTS(SELECT 1 FROM reservations UNION ALL SELECT 1 FROM payments UNION ALL SELECT 1 FROM transactions)').fetchone()[0]
        has_catalog=db.execute('SELECT EXISTS(SELECT 1 FROM properties UNION ALL SELECT 1 FROM room_categories UNION ALL SELECT 1 FROM rooms)').fetchone()[0]
        catalog_matches=_catalog_matches(db,properties,categories,rooms)
        if not catalog_matches and (has_history or has_catalog):
            raise HC.ConfigurationError('This import contains different property or inventory data. Import that data only into an empty installation; use the existing property, room and rate screens to avoid changing historical records.')
        with tx(db):
            if not catalog_matches:_import_catalog(db,properties,categories,rooms)
            HC.save(db,config,g.user['id'])
            B.audit(db,g.user,'configuration_imported','configuration',1,
                f'{len(properties)} properties, {len(categories)} categories, {len(rooms)} rooms')
        flash('Configuration imported and validated. Existing bookings and payment history were preserved.','ok')
    except (UnicodeDecodeError,HC.ConfigurationError) as exc:
        flash(str(exc) or 'Configuration file must be UTF-8 JSON.','error')
    except (sqlite3.IntegrityError,ValueError,TypeError,KeyError):
        current_app.logger.exception('Configuration import failed')
        flash('Configuration was not imported. Check for duplicate property/category/room identifiers and review the file.','error')
    return redirect(url_for('admin.configuration_page'))

@bp.route('/configuration/onboarding',methods=['GET','POST'])
@require('settings')
def configuration_onboarding():
    db=get_db();config=HC.load(db,current_app.config)
    if request.method=='POST':
        step=request.form.get('step','1')
        if step=='complete':
            with tx(db):
                HC.save(db,config,g.user['id'],complete=True)
                B.audit(db,g.user,'configuration_onboarding_completed','configuration',1,'Launch checklist reviewed')
            flash('Hotel setup marked complete. Review the configuration checklist any time you need to make changes.','ok')
            return redirect(url_for('admin.configuration_page'))
        if step!='1':abort(400)
        proposed=json.loads(json.dumps(config))
        f=request.form
        proposed['brand'].update({'name':f.get('name',''),'tagline':f.get('tagline',''),
            'logo_path':f.get('logo_path',''),'favicon_path':f.get('favicon_path','')})
        proposed['brand']['colors'].update({'primary':f.get('primary_color',''),'accent':f.get('accent_color',''),
            'background':f.get('background_color','')})
        proposed['brand']['fonts'].update({'heading':f.get('heading_font',''),'body':f.get('body_font','')})
        proposed['website'].update({'site_url':f.get('site_url',''),'primary_domain':f.get('primary_domain',''),
            'default_language':f.get('default_language','')})
        proposed['deployment'].update({'site_url':proposed['website']['site_url'],'primary_domain':proposed['website']['primary_domain']})
        proposed['pages']['home']['title']=proposed['brand']['name']
        proposed['pages']['home']['eyebrow']=f.get('location_label','')
        if config['brand']['name']!=proposed['brand']['name']:
            if config['pages']['home']['subtitle']==HC.DEFAULT_CONFIG['pages']['home']['subtitle']:
                proposed['pages']['home']['subtitle']=proposed['brand']['tagline'] or 'A welcoming stay, thoughtfully hosted.'
            if config['pages']['home']['description']==HC.DEFAULT_CONFIG['pages']['home']['description']:
                proposed['pages']['home']['description']='A welcoming destination for comfortable stays and memorable celebrations. Contact our team to discuss your plans.'
            if config['pages']['about']['description']==HC.DEFAULT_CONFIG['pages']['about']['description']:
                proposed['pages']['about']['description']=f"Welcome to {proposed['brand']['name']}. Contact our team to ask about stays and celebrations."
            if config['seo']['description']==HC.DEFAULT_CONFIG['seo']['description']:
                proposed['seo']['description']=f"Discover stays, experiences and guest services at {proposed['brand']['name']}."
        proposed['seo']['title']=f.get('seo_title','')
        try:
            proposed=HC.validate(proposed)
            with tx(db):
                HC.save(db,proposed,g.user['id'],complete=False)
                B.audit(db,g.user,'configuration_identity_saved','configuration',1,'Hotel identity and launch configuration')
            flash('Hotel identity saved. Continue to the operational setup checklist.','ok')
            return redirect(url_for('admin.configuration_onboarding',step=2))
        except HC.ConfigurationError as exc:
            flash(str(exc),'error')
            config=proposed
    step=request.args.get('step','1')
    if step=='2':
        inventory=B.inventory_configuration(db);_,tax_confirmed=B.tax_configuration(db)
        checks=[
            ('Property details',db.execute('SELECT COUNT(*) FROM properties WHERE active=1').fetchone()[0]>0,url_for('admin.settings')),
            ('Room categories and inventory',db.execute('SELECT COUNT(*) FROM rooms WHERE status=\'active\'').fetchone()[0]>0,url_for('admin.rooms')),
            ('Rates approved',inventory['rates_confirmed'],url_for('admin.categories')),
            ('Tax confirmed',tax_confirmed,url_for('admin.categories')),
            ('Contact channels',bool(db.execute('SELECT COUNT(*) FROM properties WHERE active=1 AND (phone IS NOT NULL OR email IS NOT NULL)').fetchone()[0]),url_for('admin.settings')),
        ]
        return render_template('admin/configuration_onboarding_operations.html',config=config,checks=checks)
    if step=='3':
        checks=[
            ('Public identity and metadata',bool(config['brand']['name'] and config['seo']['title']),url_for('admin.configuration_onboarding',step=1)),
            ('Production website URL',config['website']['site_url'].startswith('https://'),url_for('admin.configuration_onboarding',step=1)),
            ('Payment gateway credentials',P.configured(),url_for('admin.settings')),
            ('AI receptionist credentials',bool(current_app.config['LLM_API_KEY']),url_for('admin.settings')),
            ('Email delivery credentials',bool(current_app.config['SMTP_HOST'] and current_app.config['SMTP_FROM']),url_for('admin.settings')),
            ('WhatsApp credentials',bool(current_app.config['WHATSAPP_TOKEN'] and current_app.config['WHATSAPP_PHONE_ID']),url_for('admin.settings')),
        ]
        return render_template('admin/configuration_onboarding_review.html',config=config,checks=checks)
    if step!='1':abort(404)
    return render_template('admin/configuration_onboarding.html',config=config,fonts=HC.FONTS)

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
    tax_percent,tax_confirmed=B.tax_configuration(db)
    return render_template('admin/settings.html',props=db.execute('SELECT * FROM properties').fetchall(),adv=setting(db,'advance_percent','100'),c=current_app.config,rz=P.configured(),tax_percent=tax_percent,tax_confirmed=tax_confirmed)
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
