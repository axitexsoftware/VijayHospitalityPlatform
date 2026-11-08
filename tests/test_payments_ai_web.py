import json,hmac,hashlib
from tests.base import *
from unittest import mock
from app.services import booking as B,payments as P,ai
def sig(o,p):return hmac.new(b'sekret',f'{o}|{p}'.encode(),hashlib.sha256).hexdigest()
class Pay(Base):
    def order(self,r):
        with mock.patch.object(P,'_rz',return_value={'id':'order_1'}):return P.create_order(self.db,r['id'])
    def test_successful_payment_requires_valid_signature(self):
        r=self.book(1,hold_minutes=30);self.assertEqual(r['status'],'pending');o=self.order(r);self.assertEqual(o['amount'],r['total_paise'])
        with self.assertRaises(P.PaymentError):P.verify(self.db,'order_1','pay_1','forged')
        self.assertEqual(B.get_res(self.db,r['id'])['status'],'pending');self.assertEqual(B.paid_total(self.db,r['id']),0)
        res=P.verify(self.db,'order_1','pay_1',sig('order_1','pay_1'));self.assertEqual(res['status'],'confirmed');self.assertEqual(B.outstanding(self.db,r['id']),0)
        P.verify(self.db,'order_1','pay_1',sig('order_1','pay_1'));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM payments WHERE status="paid"').fetchone()[0],1)
    def test_failed_payment_keeps_booking_pending(self):
        r=self.book(1,hold_minutes=30);self.order(r);P.mark_failed(self.db,'order_1','declined')
        self.assertEqual(self.db.execute('SELECT status FROM payments').fetchone()[0],'failed');self.assertEqual(B.get_res(self.db,r['id'])['status'],'pending')
    def test_webhook_signature_and_capture(self):
        r=self.book(1,hold_minutes=30);self.order(r);body=json.dumps({'event':'payment.captured','payload':{'payment':{'entity':{'id':'pay_9','order_id':'order_1'}}}}).encode()
        with self.assertRaises(P.PaymentError):P.webhook(self.db,body,'bad')
        P.webhook(self.db,body,hmac.new(b'whsec',body,hashlib.sha256).hexdigest());self.assertEqual(B.get_res(self.db,r['id'])['status'],'confirmed')
    def test_late_payment_after_expiry_revives_or_flags_refund(self):
        r=self.book(1,hold_minutes=30);self.order(r);self.db.execute("UPDATE reservations SET hold_expires_at='2000-01-01 00:00:00' WHERE id=?",(r['id'],));B.expire_holds(self.db)
        self.book(1,name='Rival Guest',phone='9444444444');P.verify(self.db,'order_1','pay_2',sig('order_1','pay_2'))
        self.assertEqual(B.get_res(self.db,r['id'])['status'],'expired');self.assertEqual(self.db.execute('SELECT refund_due_paise FROM cancellations WHERE reservation_id=?',(r['id'],)).fetchone()[0],r['total_paise'])
    def test_manual_payment_and_refund_and_receipt(self):
        u={'id':1,'email':'x'};r=self.book(1)
        with self.assertRaises(P.PaymentError):P.record_manual(self.db,r['id'],'cash',r['total_paise']+1,'',u)
        pid=P.record_manual(self.db,r['id'],'cash',100000,'R1',u);P.refund(self.db,pid,40000,u);self.assertEqual(B.paid_total(self.db,r['id']),60000)
        with self.assertRaises(P.PaymentError):P.refund(self.db,pid,100000,u)
        self.assertTrue(P.receipt_pdf(self.db,r['id']).startswith(b'%PDF'))
class Fake:
    def __init__(s,*steps):s.steps=list(steps);s.seen=[]
    def __call__(s,msgs):s.seen.append(msgs);return s.steps.pop(0)
def tc(fn,/,**a):return {'content':None,'tool_calls':[{'id':'c'+fn,'type':'function','function':{'name':fn,'arguments':json.dumps(a)}}]}
class AI(Base):
    def tearDown(self):ai.LLM=None;super().tearDown()
    def test_unconfigured_is_honest(self):
        ai.LLM=None;self.app.config['LLM_API_KEY']='';o=ai.reply(self.db,None,'hello');self.assertTrue(o['unavailable']);self.assertNotIn('booked',o['reply'].lower())
    def test_receptionist_language_modes_and_hindi_fallback(self):
        self.assertIn('Respond only in natural Hindi using Devanagari',ai.system_prompt(self.db,'hi'));self.assertIn('Respond in English',ai.system_prompt(self.db,'en'))
        ai.LLM=None;self.app.config['LLM_API_KEY']='';client=self.app.test_client();token=self.csrf(client,'/')
        hindi=client.post('/api/chat',json={'message':'कमरे की उपलब्धता बताइए','language':'auto'},headers={'X-CSRF-Token':token}).get_json()
        english=client.post('/api/chat',json={'message':'कमरे की उपलब्धता बताइए','language':'en'},headers={'X-CSRF-Token':token}).get_json()
        self.assertIn('वर्चुअल रिसेप्शनिस्ट',hindi['reply']);self.assertIn('virtual receptionist',english['reply'].lower())
    def test_local_receptionist_searches_live_inventory_in_english_and_hindi(self):
        ai.LLM=None;self.app.config['LLM_API_KEY']='';ci=day(10);co=day(12)
        english=ai.reply(self.db,None,f'Find an AC room from {ci} to {co} for 2 adults','en')
        self.assertIn('Live rooms available',english['reply']);self.assertIn('Room 101',english['reply']);self.assertTrue(english['link'].startswith('/search?'))
        hindi=ai.reply(self.db,None,f'{ci} से {co} तक एसी कमरे की उपलब्धता देखें','auto')
        self.assertIn('उपलब्ध',hindi['reply']);self.assertIn('Room 101',hindi['reply'])
        client=self.app.test_client();token=self.csrf(client,'/')
        api=client.post('/api/chat',json={'message':f'Find an AC room from {ci} to {co}','language':'en'},headers={'X-CSRF-Token':token}).get_json()
        self.assertIn('Live rooms available',api['reply']);self.assertTrue(api['link'].startswith('/search?'))
    def test_full_booking_conversation_uses_real_backend(self):
        ai.LLM=Fake(tc('search_availability',check_in=day(10),check_out=day(12),adults=2,children=0,ac=True),{'content':'Room 101 is Rs 2,240 per night. Shall I book it?'})
        o=ai.reply(self.db,None,'Two adults AC room '+day(10)+' to '+day(12));tok=o['token'];self.assertIn('Room 101',o['reply'])
        tool=self.db.execute("SELECT content FROM ai_messages WHERE role='tool'").fetchone()[0];self.assertEqual(json.loads(tool)['count'],2)
        cq=dict(room_id=1,check_in=day(10),check_out=day(12),adults=2,children=0,name='Asha Kumar',phone='9876543210')
        ai.LLM=Fake(tc('create_booking',confirmation_quote='tell me more',**cq),{'content':'I need your confirmation first.'})
        ai.reply(self.db,tok,'tell me more');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
        ai.LLM=Fake(tc('create_booking',confirmation_quote='yes, please confirm',**cq),{'content':'Booked!'})
        o=ai.reply(self.db,tok,'yes, please confirm');self.assertTrue(o['booking']);self.assertEqual(B.get_res(self.db,ref=o['booking'])['source'],'ai');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)
    def test_cannot_book_room_that_was_never_searched_or_with_negative_reply(self):
        ai.LLM=Fake(tc('create_booking',room_id=1,check_in=day(10),check_out=day(12),adults=2,children=0,name='A B',phone='9876543210',confirmation_quote='yes'),{'content':'ok'})
        o=ai.reply(self.db,None,'yes');self.assertIsNone(o.get('booking'));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
    def test_lookup_cancel_and_handover(self):
        r=self.book(1);ai.LLM=Fake(tc('lookup_booking',reference=r['ref'],phone='9876543210'),{'content':'found'});o=ai.reply(self.db,None,'status?')
        self.assertIn(r['ref'],self.db.execute("SELECT content FROM ai_messages WHERE role='tool'").fetchone()[0])
        ai.LLM=Fake(tc('lookup_booking',reference=r['ref'],phone='9000000000'),{'content':'nf'});ai.reply(self.db,o['token'],'again');self.assertIn('could not find',self.db.execute("SELECT content FROM ai_messages WHERE role='tool' ORDER BY id DESC").fetchone()[0])
        ai.LLM=Fake(tc('cancel_booking',reference=r['ref'],phone='9876543210',confirmation_quote='yes cancel it'),{'content':'done'});ai.reply(self.db,o['token'],'yes cancel it');self.assertEqual(B.get_res(self.db,r['id'])['status'],'cancelled')
        ai.LLM=Fake(tc('escalate_to_human',reason='complaint'),{'content':'passing you on'});o2=ai.reply(self.db,None,'I want to complain');self.assertEqual(o2['status'],'handover')
class Web(Base):
    def test_admin_login_roles_and_csrf(self):
        c,r=self.login();self.assertEqual(r.status_code,302);self.assertEqual(c.get('/admin/').status_code,200)
        self.assertEqual(c.post('/admin/logout',data={}).status_code,400)  # missing CSRF token
        bad,r=self.login(pw='wrong');self.assertEqual(r.status_code,200)
        rc,_=self.login('rec@t.in');self.assertEqual(rc.get('/admin/reception').status_code,200);self.assertEqual(rc.get('/admin/users').status_code,403);self.assertEqual(rc.get('/admin/rooms').status_code,403);self.assertEqual(rc.get('/admin/settings').status_code,403)
        self.assertEqual(self.app.test_client().get('/admin/bookings').status_code,302)
    def test_account_lockout(self):
        for _ in range(5):self.login(pw='nope')
        c,r=self.login();self.assertEqual(r.status_code,200)
    def test_public_page_templates_and_booking_entry_render(self):
        c=self.app.test_client();paths=('/','/properties','/rooms','/rooms/1','/events','/offers','/gallery','/facilities','/contact','/about','/faq','/privacy','/search','/booking/lookup')
        for path in paths:self.assertEqual(c.get(path).status_code,200,path)
        self.assertIn('Gaya-Nawada Road',c.get('/').get_data(as_text=True));self.assertIn('24.7981768,85.045966',c.get('/contact').get_data(as_text=True))
        home=c.get('/').get_data(as_text=True)
        self.assertIn('href="/about"',home);self.assertIn('href="/search" aria-current="false">Search rooms</a>',home)
        for text in ('Welcome to Vijay Utsav Hall','A convenient destination in Manpur, Gaya','Located near Sikhar More on Gaya-Nawada Road','Accommodation enquiries','Weddings and receptions','Family celebrations','Guest assistance','Check Room Availability','Enquire About Banquet','Plan Your Wedding','View Gallery','Get Directions','Talk to IRA'):
            self.assertIn(text,home)
        about=c.get('/about').get_data(as_text=True)
        for text in ('Wedding, marriage function and reception enquiries.','Housekeeping and guest assistance, subject to property confirmation.','Our vision','Our mission'):
            self.assertIn(text,about)
        self.assertIn('selected',c.get('/contact',query_string={'kind':'event'}).get_data(as_text=True))
        sitemap=c.get('/sitemap.xml').get_data(as_text=True);self.assertIn('/events',sitemap);self.assertIn('/offers',sitemap)
        facilities=c.get('/facilities').get_data(as_text=True);self.assertIn('facility-jump',facilities);self.assertIn('name="ci"',facilities);self.assertIn('Destination',facilities)
        self.assertNotIn('guest-searchbar',c.get('/').get_data(as_text=True));self.assertNotIn('guest-searchbar',c.get('/search').get_data(as_text=True))
        query=dict(room_id=1,ci=day(10),co=day(12),adults=2,children=0)
        self.assertEqual(c.get('/search',query_string=query|{'room_id':''}).status_code,200)
        self.db.execute("INSERT INTO room_images(room_id,path,alt,sort) VALUES(1,'img/room-ac-480.avif','Test AC room',0)")
        grouped=c.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2,'rooms':2}).get_data(as_text=True)
        self.assertIn('2 requested',grouped);self.assertIn('View details',grouped);self.assertIn('Book this room',grouped);self.assertIn('Each reservation is confirmed separately',grouped)
        for detail in ('Manpur, Gaya','Up to 2 adults','Nightly rate breakdown','18 Nov','Room charges','Total'):
            self.assertIn(detail,grouped)
        for control in ('SELECT A ROOM AND RATE','filter-room-type','filter-facility','sort-rooms','data-clear-filters','data-room-photo','View Rates','data-rate-total-label','data-book-price-without-tax','search.js'):
            self.assertIn(control,grouped)
        facilities=c.get('/facilities').get_data(as_text=True);self.assertIn('Details to confirm',facilities);self.assertIn('Accessibility and luggage assistance',facilities)
        faq=c.get('/faq').get_data(as_text=True);self.assertIn('not confirmed event bookings',faq);self.assertIn('shown during booking',faq)
        self.assertEqual(c.get('/book',query_string=query).status_code,200)
        stylesheet=c.get('/static/css/public.css');self.assertEqual(stylesheet.status_code,200);stylesheet.close()
    def test_unpriced_room_categories_are_visible_but_not_bookable(self):
        self.db.execute('UPDATE room_categories SET base_price_paise=0')
        client=self.app.test_client();home=client.get('/').get_data(as_text=True);rooms=client.get('/rooms').get_data(as_text=True)
        for page in (home,rooms):
            self.assertIn('AC Room',page);self.assertIn('Non-AC Room',page);self.assertIn('Rates on request',page)
            self.assertNotIn('/book?room_id=',page)
        self.assertIn('Contact for rates',home);self.assertIn('Ask about availability',rooms)
        error=client.get('/rooms/999');self.assertEqual(error.status_code,404);self.assertIn(b'We couldn\'t find that page.',error.data);self.assertIn(b'Find a room',error.data)
    def test_guest_event_enquiry_is_saved_and_managed_by_admin(self):
        client=self.app.test_client();token=self.csrf(client,'/contact');payload={'csrf_token':token,'kind':'event','property_id':'1','preferred_date':day(10),'guest_count':'80','name':'Asha Event Host','phone':'9811111199','email':'asha@example.in','message':'Please contact me about an event at Vijay Garden.'}
        response=client.post('/contact',data=payload);self.assertEqual(response.status_code,302);self.assertIn('#enquiry',response.headers['Location'])
        row=self.db.execute('SELECT * FROM guest_enquiries').fetchone();self.assertEqual(row['kind'],'event');self.assertEqual(row['status'],'new');self.assertEqual(row['guest_count'],80)
        staff,_=self.login();inbox=staff.get('/admin/enquiries');self.assertEqual(inbox.status_code,200);self.assertIn('Asha Event Host',inbox.get_data(as_text=True));self.assertIn('Vijay Utsav',inbox.get_data(as_text=True))
        admin_token=self.csrf(staff,'/admin/enquiries');updated=staff.post(f'/admin/enquiries/{row["id"]}/status',data={'csrf_token':admin_token,'status':'contacted'});self.assertEqual(updated.status_code,302)
        self.assertEqual(self.db.execute('SELECT status FROM guest_enquiries WHERE id=?',(row['id'],)).fetchone()[0],'contacted')
        self.assertTrue(self.db.execute("SELECT 1 FROM audit_logs WHERE entity='guest_enquiry' AND entity_id=? AND action='guest_enquiry_status_changed'",(str(row['id']),)).fetchone())
        invalid=self.app.test_client();bad_token=self.csrf(invalid,'/contact');payload.update(csrf_token=bad_token,phone='123');self.assertEqual(invalid.post('/contact',data=payload).status_code,200);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM guest_enquiries').fetchone()[0],1)
        receptionist,_=self.login('rec@t.in');self.assertEqual(receptionist.get('/admin/enquiries').status_code,403)
    def test_contact_page_views_do_not_consume_submission_limit(self):
        app=create_app(DATABASE=self.path,RATELIMIT_ENABLED=True,SECRET_KEY='test-contact-rate-limit-key')
        client=app.test_client()
        for _ in range(12):self.assertEqual(client.get('/contact').status_code,200)
    def test_public_booking_flow_and_manage(self):
        c=self.app.test_client();t=self.csrf(c,'/');q=dict(room_id=1,ci=day(10),co=day(12),adults=2,children=0)
        self.assertIn('Book this room',c.get('/search',query_string=dict(ci=q['ci'],co=q['co'],adults=2)).get_data(as_text=True))
        r=c.post('/book',query_string=q,data=dict(csrf_token=t,name='Ravi Sharma',phone='+91 98765 43210',email='r@x.in',pay_mode='hotel'));self.assertEqual(r.status_code,302);ref=r.headers['Location'].split('/')[-1]
        page=c.get('/booking/'+ref).get_data(as_text=True);self.assertIn('Booking confirmed',page);self.assertIn(ref,page)
        self.assertEqual(c.get('/booking/'+ref+'/receipt.pdf').status_code,200)
        other=self.app.test_client();self.assertEqual(other.get('/booking/'+ref).status_code,302)  # not yours without ref+phone
        t2=self.csrf(other,'/booking/lookup');r=other.post('/booking/lookup',data=dict(csrf_token=t2,ref=ref,phone='9876543210'));self.assertEqual(r.status_code,302)
        r=other.post(f'/booking/{ref}/cancel',data=dict(csrf_token=t2));self.assertEqual(B.get_res(self.db,ref=ref)['status'],'cancelled')
        self.assertEqual(c.post('/book',query_string=q,data=dict(name='x')).status_code,400)
    def test_api_availability_and_booking_conflict(self):
        c=self.app.test_client();t=self.csrf(c,'/');j=c.get('/api/availability',query_string=dict(check_in=day(10),check_out=day(12),adults=2)).get_json();self.assertEqual(j['count'],3)
        group=c.get('/api/availability',query_string=dict(check_in=day(10),check_out=day(12),adults=2,rooms=2)).get_json();self.assertTrue(group['can_accommodate']);self.assertEqual(group['rooms_required'],2)
        short=c.get('/api/availability',query_string=dict(check_in=day(10),check_out=day(12),adults=2,rooms=4)).get_json();self.assertFalse(short['can_accommodate']);self.assertIn('Only 3 room',short['message'])
        self.assertEqual(c.get('/api/availability',query_string=dict(check_in=day(10),check_out=day(12),rooms=0)).status_code,400)
        body=dict(room_id=1,check_in=day(10),check_out=day(12),adults=2,name='Mina Das',phone='9811111111');h={'X-CSRF-Token':t}
        self.assertEqual(c.post('/api/bookings',json=body,headers=h).status_code,201);body['name']='Other Guy';body['phone']='9822222222'
        self.assertEqual(c.post('/api/bookings',json=body,headers=h).status_code,409);self.assertEqual(c.post('/api/bookings',json=body).status_code,400)
    def test_tax_requires_admin_confirmation_and_updates_quotes(self):
        from app.services import booking as B,ai
        self.app.config['TAX_PERCENT_CONFIGURED']=False
        self.db.execute("DELETE FROM settings WHERE key IN('tax_percent','tax_percent_confirmed')")
        self.assertEqual(B.tax_configuration(self.db),(0.0,False))
        self.assertEqual(self.db.execute("SELECT value FROM settings WHERE key='test_rate_label'").fetchone()[0],'TEST ONLY - NOT APPROVED')
        self.assertEqual(self.db.execute('SELECT base_price_paise FROM room_categories WHERE id=1').fetchone()[0],180000)
        self.assertEqual(self.db.execute('SELECT base_price_paise FROM room_categories WHERE id=2').fetchone()[0],90000)
        self.assertIn('Tax has not been confirmed in Admin',ai.system_prompt(self.db))
        client=self.app.test_client();dates={'ci':day(10),'co':day(12),'adults':2,'children':0,'rooms':1}
        page=client.get('/search',query_string=dates).get_data(as_text=True)
        self.assertIn('Tax pending Admin confirmation',page);self.assertIn('Enquire to confirm final price',page);self.assertNotIn('Book this room ·',page)
        blocked_booking=client.get('/book',query_string={'room_id':1,'ci':day(10),'co':day(12),'adults':2,'children':0})
        self.assertEqual(blocked_booking.status_code,302);self.assertIn('/search?',blocked_booking.headers['Location'])
        availability=client.get('/api/availability',query_string={'check_in':day(10),'check_out':day(12),'adults':2}).get_json()
        self.assertFalse(availability['can_accommodate']);self.assertEqual(availability['inventory_status'],'tax_unconfirmed')
        pending_quote=client.get('/api/price-quote',query_string={'category_id':1,'check_in':day(10),'check_out':day(12)}).get_json()
        self.assertFalse(pending_quote['tax_configured']);self.assertEqual(pending_quote['tax_paise'],0);self.assertEqual(pending_quote['total_paise'],pending_quote['room_total_paise'])
        with self.assertRaises(B.Invalid):B.create_reservation(self.db,1,day(10),day(12),2,0,'Tax Pending Guest','9876543211',None)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
        admin,_=self.login();token=self.csrf(admin,'/admin/categories')
        self.assertEqual(admin.get('/admin/categories').status_code,200)
        saved=admin.post('/admin/tax',data={'csrf_token':token,'tax_percent':'7.5','confirm_tax':'1'})
        self.assertEqual(saved.status_code,302);self.assertEqual(B.tax_configuration(self.db),(7.5,True))
        self.assertIn('7.5% confirmed',admin.get('/admin/settings').get_data(as_text=True))
        quote=admin.get('/api/price-quote',query_string={'category_id':1,'check_in':day(10),'check_out':day(12)}).get_json()
        self.assertTrue(quote['tax_configured']);self.assertEqual(quote['tax_paise'],27000);self.assertEqual(quote['total_paise'],387000)
        reservation=B.create_reservation(self.db,1,day(10),day(12),2,0,'Tax Confirmed Guest','9876543212',None)
        self.assertEqual(reservation['tax_paise'],27000);self.assertEqual(reservation['total_paise'],387000)
        pending=admin.post('/admin/tax',data={'csrf_token':token,'tax_percent':'','confirm_tax':''})
        self.assertEqual(pending.status_code,302);self.assertEqual(B.tax_configuration(self.db),(0.0,False))
        self.assertEqual(B.quote(self.db,dict(self.db.execute(B.ROOM_SQL+' AND r.id=?',(1,)).fetchone()),day(10),day(12))['total'],360000)
    def test_inventory_setup_requires_review_and_applies_only_reviewed_data(self):
        from app.services import booking as B
        self.db.execute("DELETE FROM settings WHERE key IN('tax_percent','tax_percent_confirmed')")
        admin,_=self.login();token=self.csrf(admin,'/admin/inventory-setup')
        form={'csrf_token':token,'category_id':['1','2'],'rate_1':'1800','max_adults_1':'2','max_children_1':'1','rooms_1':'101\n102\n104','rate_2':'900','max_adults_2':'2','max_children_2':'1','rooms_2':'201','tax_percent':'','confirm_tax':''}
        duplicate=admin.post('/admin/inventory-setup/review',data=form|{'rooms_2':'101'})
        self.assertEqual(duplicate.status_code,400);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0],3)
        review=admin.post('/admin/inventory-setup/review',data=form);self.assertEqual(review.status_code,200);page=review.get_data(as_text=True)
        self.assertIn('PENDING',page);self.assertIn('Nothing has been written to the database yet.',page)
        self.assertEqual(self.db.execute('SELECT base_price_paise FROM room_categories WHERE id=1').fetchone()[0],180000)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rooms WHERE number='104'").fetchone()[0],0)
        review_token=re.search(r'name="review_token" value="([^"]+)"',page).group(1)
        applied=admin.post('/admin/inventory-setup/apply',data=form|{'review_token':review_token,'action':'apply'})
        self.assertEqual(applied.status_code,302);self.assertEqual(self.db.execute("SELECT category_id FROM rooms WHERE number='104'").fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT base_price_paise FROM room_categories WHERE id=2').fetchone()[0],90000)
        self.assertEqual(B.tax_configuration(self.db),(0.0,False))
        with self.assertRaises(B.Invalid):B.create_reservation(self.db,1,day(10),day(12),2,0,'Pending Tax Guest','9888888801',None)
    def test_availability_explains_unpublished_inventory(self):
        self.db.execute("UPDATE rooms SET status='inactive'");client=self.app.test_client();dates={'check_in':day(10),'check_out':day(12),'adults':2}
        api=client.get('/api/availability',query_string=dates).get_json();self.assertEqual(api['count'],0);self.assertEqual(api['inventory_status'],'not_configured');self.assertIn('configured yet',api['message'])
        page=client.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2}).get_data(as_text=True);self.assertIn('Room options require a quick enquiry',page);self.assertIn('Rates and date availability on request',page);self.assertNotIn('No rooms available for those dates',page)
    def test_search_explains_active_rooms_without_published_rates(self):
        self.db.execute('UPDATE room_categories SET base_price_paise=0')
        client=self.app.test_client();page=client.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2}).get_data(as_text=True)
        self.assertIn('3 active room listings are configured',page);self.assertIn('Room rates are not published yet',page);self.assertIn('Ask about a room',page)
    def test_dashboard_reports_and_exports_use_real_data(self):
        from app.services import reports as R
        u={'id':1,'email':'x'};r=self.book(1,ci=0,co=2);P.record_manual(self.db,r['id'],'cash',100000,'',u);k=R.dashboard(self.db)
        self.assertEqual(k['total_rooms'],3);self.assertEqual(k['reserved'],1);self.assertEqual(k['available'],2);self.assertEqual(k['rev_day'],100000);self.assertEqual(k['checkins_today'],1)
        s=R.summary(self.db,day(0),day(1));self.assertEqual(s['nights_sold'],2);self.assertEqual(s['bookings'],1);self.assertEqual(s['ac'],2)
        c,_=self.login();self.assertEqual(c.get('/admin/reports').status_code,200)
        x=c.get('/admin/bookings/export.xlsx');self.assertTrue(x.data.startswith(b'PK'));self.assertTrue(c.get('/admin/reports/export.pdf').data.startswith(b'%PDF'))
    def test_dashboard_shows_complete_analytics_values(self):
        from app.services import reports as R
        user={'id':1,'email':'owner@t.in'}
        bookings=[self.book(1,ci=0,co=1,name='Analytics Guest One',phone='9811111101'),self.book(2,ci=4,co=5,name='Analytics Guest Two',phone='9811111102'),self.book(3,ci=8,co=9,name='Analytics Guest Three',phone='9811111103')]
        for reservation,offset,amount in zip(bookings,(-12,-7,-2),(50000,70000,80000)):
            stamp=f'{day(offset)} 12:00:00'
            self.db.execute('UPDATE reservations SET created_at=? WHERE id=?',(stamp,reservation['id']))
            P.record_manual(self.db,reservation['id'],'cash',amount,'analytics-test',user)
            self.db.execute('UPDATE payments SET paid_at=? WHERE reservation_id=?',(stamp,reservation['id']))
        rows=R.series(self.db,day(-13),day(0),'day');occupancy=R.occupancy_series(self.db,day(-13),day(0))
        self.assertEqual(len(rows),14);self.assertEqual(sum(row['bookings'] for row in rows),3);self.assertEqual(sum(row['collected'] for row in rows),200000)
        self.assertEqual(len(occupancy),14);self.assertEqual(occupancy[-1]['occupancy'],33.3)
        client,_=self.login();response=client.get('/admin/');self.assertEqual(response.status_code,200);page=response.get_data(as_text=True)
        for text in ('Bookings created','Payments collected','Occupancy by night','Room-category demand','Show all bookings values','Show all collected values','33.3%'):
            self.assertIn(text,page)
    def test_owner_pages_and_finance_ledger_workflows(self):
        from app.services import reports as R
        reservation=self.book(1,ci=0,co=1);P.record_manual(self.db,reservation['id'],'cash',100000,'test-cash',{'id':1,'email':'owner@t.in'})
        client,_=self.login();paths=('/admin/reception','/admin/bookings','/admin/rooms','/admin/categories','/admin/pricing/dynamic','/admin/media','/admin/reports','/admin/enquiries','/admin/conversations','/admin/users')
        for path in paths:self.assertEqual(client.get(path).status_code,200,path)
        token=self.csrf(client,'/admin/reports');entry=dict(csrf_token=token,**{'from':day(0),'to':day(0),'group':'day','incurred_on':day(0)})
        self.assertEqual(client.post('/admin/expenses',data=entry|{'kind':'capital','category':'Renovation','description':'Test capital investment','amount':'1000','incurred_on':day(-100)}).status_code,302)
        self.assertEqual(client.post('/admin/expenses',data=entry|{'kind':'operating','category':'Utilities','description':'Test operating cost','amount':'250'}).status_code,302)
        finance=R.summary(self.db,day(0),day(0));self.assertEqual(finance['collected'],100000);self.assertEqual(finance['operating'],25000);self.assertEqual(finance['investment'],100000);self.assertEqual(finance['net_return'],75000);self.assertEqual(finance['roi_pct'],75.0)
        page=client.get('/admin/reports',query_string={'from':day(0),'to':day(0)}).get_data(as_text=True);self.assertIn('75.0%',page);self.assertIn('Test operating cost',page);self.assertIn('Test capital investment',page)
        eid=self.db.execute("SELECT id FROM expense_entries WHERE category='Utilities'").fetchone()[0]
        self.assertEqual(client.post(f'/admin/expenses/{eid}/delete',data={'csrf_token':token}).status_code,302)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM expense_entries WHERE id=?',(eid,)).fetchone()[0],0)
    def test_receptionist_cannot_write_financial_entries(self):
        client,_=self.login('rec@t.in');token=self.csrf(client,'/admin/reception')
        response=client.post('/admin/expenses',data={'csrf_token':token,'kind':'capital','category':'Test','description':'Blocked','amount':'10','incurred_on':day(0)})
        self.assertEqual(response.status_code,403)
    def test_security_headers_xss_and_sql_injection(self):
        c=self.app.test_client();r=c.get('/');self.assertIn("default-src 'self'",r.headers['Content-Security-Policy']);self.assertEqual(r.headers['X-Frame-Options'],'DENY')
        self.book(1,name='<script>alert(1)</script>',phone='9855555555');oc,_=self.login();page=oc.get('/admin/bookings').get_data(as_text=True);self.assertNotIn('<script>alert(1)</script>',page);self.assertIn('&lt;script&gt;',page)
        self.assertEqual(oc.get('/admin/bookings',query_string={'q':"x' OR '1'='1"}).status_code,200);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)
    def test_admin_room_and_booking_management(self):
        c,_=self.login();t=self.csrf(c,'/admin/rooms')
        r=c.post('/admin/rooms/new',data=dict(csrf_token=t,category_id=1,number='301',price='2500'));self.assertEqual(r.status_code,302);self.assertEqual(self.db.execute("SELECT price_override_paise FROM rooms WHERE number='301'").fetchone()[0],250000)
        b=self.book(1,ci=0,co=1);r=c.post(f"/admin/bookings/{b['id']}/checkin",data=dict(csrf_token=t));self.assertEqual(B.get_res(self.db,b['id'])['status'],'checked_in')
        r=c.post(f"/admin/bookings/{b['id']}/payment",data=dict(csrf_token=t,method='upi',amount='1000'));self.assertEqual(B.paid_total(self.db,b['id']),100000)
        r=c.post(f"/admin/bookings/{b['id']}/checkout",data=dict(csrf_token=t));self.assertEqual(B.get_res(self.db,b['id'])['status'],'checked_out')
        self.assertEqual(c.get(f"/admin/bookings/{b['id']}").status_code,200)
