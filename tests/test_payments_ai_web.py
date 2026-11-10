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
    def test_online_payment_order_stops_when_tax_is_unconfirmed(self):
        r=self.book(1,hold_minutes=30)
        self.db.execute("UPDATE settings SET value='0' WHERE key='tax_percent_confirmed'")
        with mock.patch.object(P,'_rz') as gateway:
            with self.assertRaisesRegex(P.PaymentError,'tax setting'):P.create_order(self.db,r['id'])
            gateway.assert_not_called()
class Fake:
    def __init__(s,*steps):s.steps=list(steps);s.seen=[]
    def __call__(s,msgs):s.seen.append(msgs);return s.steps.pop(0)
def tc(fn,/,**a):return {'content':None,'tool_calls':[{'id':'c'+fn,'type':'function','function':{'name':fn,'arguments':json.dumps(a)}}]}
class AI(Base):
    def tearDown(self):ai.LLM=None;super().tearDown()
    def test_unconfigured_is_honest(self):
        ai.LLM=None;self.app.config['LLM_API_KEY']='';o=ai.reply(self.db,None,'hello');self.assertTrue(o['unavailable']);self.assertNotIn('booked',o['reply'].lower())
        home=self.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('data-ai-ready="false"',home)
    def test_receptionist_language_modes_and_hindi_fallback(self):
        self.assertIn('Kabita, the Vijay Intelligent Reception Assistant',ai.system_prompt(self.db));self.assertIn('Respond only in natural Hindi using Devanagari',ai.system_prompt(self.db,'hi'));self.assertIn('Respond in English',ai.system_prompt(self.db,'en'))
        ai.LLM=None;self.app.config['LLM_API_KEY']='';client=self.app.test_client();token=self.csrf(client,'/')
        hindi=client.post('/api/chat',json={'message':'कमरे की उपलब्धता बताइए','language':'auto'},headers={'X-CSRF-Token':token}).get_json()
        english=client.post('/api/chat',json={'message':'कमरे की उपलब्धता बताइए','language':'en'},headers={'X-CSRF-Token':token}).get_json()
        self.assertIn('वर्चुअल रिसेप्शनिस्ट',hindi['reply']);self.assertIn('virtual receptionist',english['reply'].lower())
    def test_chat_history_is_restored_only_from_own_session(self):
        ai.LLM=None;self.app.config['LLM_API_KEY']='';first=self.app.test_client();csrf=self.csrf(first,'/')
        first.post('/api/chat',json={'message':'hello','language':'en'},headers={'X-CSRF-Token':csrf})
        history=first.get('/api/chat').get_json();self.assertEqual([m['role'] for m in history['messages']],['user','assistant']);self.assertTrue(history['messages'][0]['created_at'])
        second=self.app.test_client();self.assertEqual(second.get('/api/chat').get_json()['messages'],[])
        second_csrf=self.csrf(second,'/')
        second.post('/api/chat',json={'token':first.get_cookie('session').value,'message':'other session','language':'en'},headers={'X-CSRF-Token':second_csrf})
        self.assertEqual(len(first.get('/api/chat').get_json()['messages']),2)
    def test_local_receptionist_searches_live_inventory_in_english_and_hindi(self):
        ai.LLM=None;self.app.config['LLM_API_KEY']='';ci=day(10);co=day(12)
        english=ai.reply(self.db,None,f'Find an AC room from {ci} to {co} for 2 adults','en')
        self.assertIn('Live rooms available',english['reply']);self.assertIn('Room 101',english['reply']);self.assertTrue(english['link'].startswith('/search?'))
        hindi=ai.reply(self.db,None,f'{ci} से {co} तक एसी कमरे की उपलब्धता देखें','auto')
        self.assertIn('उपलब्ध',hindi['reply']);self.assertIn('Room 101',hindi['reply'])
        client=self.app.test_client();token=self.csrf(client,'/')
        api=client.post('/api/chat',json={'message':f'Find an AC room from {ci} to {co}','language':'en'},headers={'X-CSRF-Token':token}).get_json()
        self.assertIn('Live rooms available',api['reply']);self.assertTrue(api['link'].startswith('/search?'))
    def test_unconfigured_fallback_collects_natural_stay_and_routes_events(self):
        from datetime import timedelta
        from app.util import today
        ai.LLM=None;self.app.config['LLM_API_KEY']=''
        first=ai.reply(self.db,None,'book for room','en');self.assertIn('check-in date',first['reply'])
        tomorrow=(today()+timedelta(days=1)).isoformat();checkout=(today()+timedelta(days=3)).isoformat()
        second=ai.reply(self.db,first['token'],'tomorrow for 2 nights','en')
        self.assertIn('Live rooms available',second['reply']);self.assertIn('Room 101',second['reply']);self.assertIn(f'ci={tomorrow}',second['link']);self.assertIn(f'co={checkout}',second['link'])
        event=ai.reply(self.db,None,'I have an event enquiry','en')
        self.assertEqual(event['link'],'/contact?kind=event#enquiry');self.assertIn('event enquiry',event['reply'])
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
        self.assertIn('href="/about"',home);self.assertIn('href="/search"',home);self.assertIn('Book a Stay',home)
        self.assertIn('Watch venue videos',home);self.assertIn('tab=video&amp;menu_slider=0',home);self.assertIn('data-slide-next',home)
        self.assertEqual(home.count('id="chat"'),1);self.assertIn('Kabita virtual receptionist',home);self.assertNotIn('IRA',home)
        for text in ('Welcome to Vijay Utsav Hall','Premium hospitality. Elegant stays. Memorable celebrations.','Located near Sikhar More on Gaya-Nawada Road','Accommodation enquiries','Weddings and receptions','Family celebrations','Guest assistance','Check room availability','Enquire about an event','View gallery','Contact the property','Talk to Kabita','Gather for life’s special moments.','See the place for yourself.'):
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
        self.assertIn('2 requested',grouped);self.assertIn('View details',grouped);self.assertIn('Enquire about 2 rooms',grouped);self.assertNotIn('Book this room',grouped);self.assertIn('Each reservation is confirmed separately',grouped)
        for detail in ('Manpur, Gaya','Up to 2 adults','Nightly rate breakdown','18 Nov','Room charges','Total'):
            self.assertIn(detail,grouped)
        for control in ('Select a room and rate','filter-room-type','filter-facility','sort-rooms','data-clear-filters','data-room-photo','Rates from','View Rates','data-rate-total-label','search.js'):
            self.assertIn(control,grouped)
        facilities=c.get('/facilities').get_data(as_text=True);self.assertIn('Details to confirm',facilities);self.assertIn('Accessibility and luggage assistance',facilities)
        faq=c.get('/faq').get_data(as_text=True);self.assertIn('not confirmed event bookings',faq);self.assertIn('management-confirmed room rates',faq)
        self.assertEqual(c.get('/book',query_string=query).status_code,200)
        stylesheet=c.get('/static/css/public.css');self.assertEqual(stylesheet.status_code,200);stylesheet.close()
    def test_unpriced_room_categories_are_visible_but_not_bookable(self):
        self.db.execute("UPDATE room_categories SET base_price_paise=0,nightly_rate_paise=NULL,rates_status='pending',publication_status='unpublished'")
        client=self.app.test_client();home=client.get('/').get_data(as_text=True);rooms=client.get('/rooms').get_data(as_text=True)
        for page in (home,rooms):
            self.assertIn('AC Room',page);self.assertIn('Non-AC Room',page);self.assertIn('Rates on request',page)
            self.assertNotIn('/book?room_id=',page)
        self.assertIn('Contact for details',home);self.assertIn('Ask about availability',rooms)
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
    def test_room_search_api_uses_approved_categories_and_actual_inventory(self):
        client=self.app.test_client();dates={'check_in':day(10),'check_out':day(12),'adults':4,'children':0,'rooms':2}
        response=client.get('/api/rooms/search',query_string=dates);self.assertEqual(response.status_code,200)
        data=response.get_json();self.assertTrue(data['can_accommodate']);self.assertEqual(data['available_count'],3)
        self.assertEqual(sum(category['available_count'] for category in data['categories']),3)
        self.assertEqual(client.get('/api/room-categories').get_json()['items'][0]['currency'],'INR')
        category_id=data['categories'][0]['category_id'];self.assertEqual(client.get(f'/api/rooms/{category_id}').status_code,200)
        blocked=self.book(1,ci=10,co=12,name='Occupied room',phone='9899999911')
        refreshed=client.get('/api/rooms/availability',query_string=dates).get_json()
        self.assertEqual(refreshed['count'],2);self.assertTrue(refreshed['can_accommodate'])
        too_many=client.get('/api/rooms/search',query_string=dates|{'adults':5}).get_json()
        self.assertEqual(too_many['available_count'],0);self.assertFalse(too_many['can_accommodate'])
        self.assertEqual(B.get_res(self.db,blocked['id'])['status'],'confirmed')
    def test_category_configuration_keeps_missing_values_pending_then_publishes_real_inventory(self):
        admin,_=self.login();token=self.csrf(admin,'/admin/categories')
        admin.post('/admin/categories',data={'csrf_token':token,'property_id':'1','name':'Pending Suite'})
        category=self.db.execute("SELECT * FROM room_categories WHERE name='Pending Suite'").fetchone()
        self.assertIsNone(category['nightly_rate_paise']);self.assertIsNone(category['max_adults_approved'])
        self.assertEqual(category['details_status'],'pending');self.assertEqual(category['publication_status'],'unpublished')
        self.assertNotIn('Pending Suite',admin.get('/api/room-categories').get_data(as_text=True))
        admin.post('/admin/categories',data={'csrf_token':token,'id':category['id'],'property_id':'1','name':'Confirmed Suite','room_type':'Standard suite','price':'2400','max_adults':'2','max_children':'1','max_occupancy':'3','description':'Management-approved suite','active':'1'})
        admin.post('/admin/rooms/new',data={'csrf_token':token,'category_id':category['id'],'number':'901'})
        approved=admin.post(f"/admin/categories/{category['id']}/approval",data={'csrf_token':token,'approve_details':'1','approve_inventory':'1','approve_occupancy':'1','approve_rates':'1','approve_tax':'1','publish':'1'})
        self.assertEqual(approved.status_code,302)
        row=self.db.execute('SELECT publication_status,inventory_status,rates_status FROM room_categories WHERE id=?',(category['id'],)).fetchone()
        self.assertEqual(tuple(row),('published','approved','approved'))
        result=admin.get('/api/rooms/search',query_string={'check_in':day(10),'check_out':day(12),'adults':2,'children':0,'rooms':1}).get_json()
        self.assertTrue(any(x['category_id']==category['id'] and x['available_count']==1 for x in result['categories']))
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
        self.assertIsNotNone(B.inventory_publication_error(self.db))
    def test_inventory_setup_requires_review_and_applies_only_reviewed_data(self):
        from app.services import booking as B
        self.db.execute("DELETE FROM settings WHERE key IN('tax_percent','tax_percent_confirmed')")
        admin,_=self.login();token=self.csrf(admin,'/admin/inventory-setup')
        form={'csrf_token':token,'category_id':['1','2'],'rate_1':'1800','max_adults_1':'2','max_children_1':'1','max_occupancy_1':'3','rooms_1':'101\n102\n104','rate_2':'900','max_adults_2':'2','max_children_2':'1','max_occupancy_2':'3','rooms_2':'201','tax_percent':'','confirm_tax':''}
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
        self.assertEqual(self.db.execute("SELECT value FROM settings WHERE key='room_rates_confirmed'").fetchone()[0],'0')
        self.assertEqual(self.db.execute("SELECT value FROM settings WHERE key='room_occupancy_confirmed'").fetchone()[0],'0')
        self.assertEqual(self.db.execute("SELECT value FROM settings WHERE key='room_inventory_confirmed'").fetchone()[0],'0')
        with self.assertRaises(B.Invalid):B.create_reservation(self.db,1,day(10),day(12),2,0,'Pending Tax Guest','9888888801',None)
    def test_unapproved_room_data_is_not_published_or_bookable(self):
        from app.db import set_setting
        from app.services import booking as B
        for key in ('room_rates_confirmed','room_occupancy_confirmed','room_inventory_confirmed','room_photos_confirmed'):
            set_setting(self.db,key,'0')
        self.db.execute("INSERT INTO room_images(room_id,path,alt,sort) VALUES(1,'img/room-ac-480.avif','Unapproved room photo',0)")
        self.db.execute("UPDATE room_categories SET photos_status='pending' WHERE id=1")
        client=self.app.test_client();dates={'ci':day(10),'co':day(12),'adults':2}
        search=client.get('/search',query_string=dates).get_data(as_text=True)
        customer_message='Room availability and tariff details are currently being updated. Please contact our property team for the latest information and reservations.'
        self.assertEqual(search.count(customer_message),1);self.assertNotIn('Room details pending management confirmation',search)
        self.assertIn('Room options',search);self.assertIn('On request',search);self.assertIn('Ask about a room',search);self.assertIn('2 nights',search)
        self.assertNotIn('₹1,800',search);self.assertNotIn('Up to 2 adults',search);self.assertNotIn('Room 101',search)
        landing=client.get('/search').get_data(as_text=True)
        self.assertIn(customer_message,landing);self.assertIn('Room options',landing);self.assertIn('On request',landing)
        with client.session_transaction() as browser_session:
            browser_session['_flashes']=[('error','Room rates and occupancy details are subject to management confirmation. Please contact the property team.')]
        refreshed=client.get('/search',query_string=dates).get_data(as_text=True)
        self.assertEqual(refreshed.count(customer_message),1);self.assertNotIn('Room rates and occupancy details are subject to management confirmation',refreshed)
        blocked_booking=client.get('/book',query_string={'room_id':1,'ci':day(10),'co':day(12),'adults':2},follow_redirects=True).get_data(as_text=True)
        self.assertEqual(blocked_booking.count(customer_message),1)
        rooms=client.get('/rooms').get_data(as_text=True)
        self.assertIn('Room photograph',rooms);self.assertIn('Pending management approval',rooms)
        self.assertNotIn('room-ac-480.avif',rooms);self.assertNotIn('Up to 2 adults',rooms)
        self.assertEqual(client.get('/rooms/1').status_code,404)
        gallery=client.get('/gallery').get_data(as_text=True)
        self.assertNotIn('room-ac',gallery);self.assertNotIn('Unapproved room photo',gallery)
        availability=client.get('/api/availability',query_string={'check_in':day(10),'check_out':day(12),'adults':2}).get_json()
        self.assertEqual(availability['inventory_status'],'management_confirmation_pending')
        self.assertEqual(availability['rooms'],[])
        quote=client.get('/api/price-quote',query_string={'category_id':1,'check_in':day(10),'check_out':day(12)})
        self.assertEqual(quote.status_code,409)
        room_media=client.get('/api/media?kind=room').get_json();self.assertEqual(room_media['items'],[])
        with self.assertRaises(B.Invalid):
            B.create_reservation(self.db,1,day(10),day(12),2,0,'Pending Approval Guest','9888888802',None)
        ai.LLM=None;self.app.config['LLM_API_KEY']=''
        response=ai.reply(self.db,None,f'Check room availability from {day(10)} to {day(12)} for 2 adults','en')
        self.assertIn('subject to management confirmation',response['reply'])
        self.assertEqual(response['link'],'/contact?kind=room#enquiry')
    def test_inventory_setup_publishes_only_explicitly_confirmed_data(self):
        from app.services import booking as B
        admin,_=self.login();token=self.csrf(admin,'/admin/inventory-setup')
        form={'csrf_token':token,'category_id':['1','2'],'rate_1':'1800','max_adults_1':'2','max_children_1':'1','max_occupancy_1':'3','rooms_1':'101\n102','rate_2':'900','max_adults_2':'2','max_children_2':'1','max_occupancy_2':'3','rooms_2':'201','tax_percent':'0','confirm_tax':'1','confirm_details':'1','confirm_rates':'1','confirm_occupancy':'1','confirm_rooms':'1'}
        review=admin.post('/admin/inventory-setup/review',data=form);self.assertEqual(review.status_code,200)
        page=review.get_data(as_text=True);review_token=re.search(r'name="review_token" value="([^"]+)"',page).group(1)
        applied=admin.post('/admin/inventory-setup/apply',data=form|{'review_token':review_token,'action':'apply'})
        self.assertEqual(applied.status_code,302)
        self.assertEqual(B.inventory_configuration(self.db),{'rates_confirmed':True,'occupancy_confirmed':True,'rooms_confirmed':True,'photos_confirmed':False})
        search=admin.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2}).get_data(as_text=True)
        self.assertIn('Room 101',search);self.assertIn('Up to 2 adults',search);self.assertIn('₹1,800',search)
    def test_availability_explains_unpublished_inventory(self):
        self.db.execute("UPDATE rooms SET status='inactive'");client=self.app.test_client();dates={'check_in':day(10),'check_out':day(12),'adults':2}
        api=client.get('/api/availability',query_string=dates).get_json();self.assertEqual(api['count'],0);self.assertEqual(api['inventory_status'],'not_configured');self.assertIn('configured yet',api['message'])
        page=client.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2}).get_data(as_text=True);                self.assertIn('Room options require a quick enquiry',page);self.assertIn('Ask about a room',page)
    def test_search_explains_active_rooms_without_published_rates(self):
        self.db.execute("UPDATE room_categories SET base_price_paise=0,nightly_rate_paise=NULL,rates_status='pending',publication_status='unpublished'")
        client=self.app.test_client();page=client.get('/search',query_string={'ci':day(10),'co':day(12),'adults':2}).get_data(as_text=True)
        customer_message='Room availability and tariff details are currently being updated. Please contact our property team for the latest information and reservations.'
        self.assertEqual(page.count(customer_message),1);self.assertIn('Room options',page);self.assertIn('Ask about a room',page)
        self.assertNotIn('3 active room listings',page);self.assertNotIn('₹1,800',page)
        self.assertNotIn('/* Shared premium hospitality system */',page)
    def test_dashboard_reports_and_exports_use_real_data(self):
        from app.services import reports as R
        u={'id':1,'email':'x'};r=self.book(1,ci=0,co=2);P.record_manual(self.db,r['id'],'cash',100000,'',u);k=R.dashboard(self.db)
        self.assertEqual(k['total_rooms'],3);self.assertEqual(k['reserved'],1);self.assertEqual(k['available'],2);self.assertEqual(k['rev_day'],100000);self.assertEqual(k['checkins_today'],1)
        s=R.summary(self.db,day(0),day(1));self.assertEqual(s['nights_sold'],2);self.assertEqual(s['bookings'],1);self.assertEqual(s['ac'],2)
        c,_=self.login();self.assertEqual(c.get('/admin/reports').status_code,200)
        x=c.get('/admin/bookings/export.xlsx');self.assertTrue(x.data.startswith(b'PK'));self.assertTrue(c.get('/admin/reports/export.pdf').data.startswith(b'%PDF'))
    def test_daily_report_series_has_zero_days_and_actual_event_counts(self):
        from app.services import booking as B,reports as R
        u={'id':1,'email':'x'}
        checked=self.book(1,ci=0,co=1)
        cancelled=self.book(2,ci=0,co=1,phone='9812345678')
        P.record_manual(self.db,checked['id'],'cash',100000,'',u)
        B.check_in(self.db,checked['id'],u)
        B.check_out(self.db,checked['id'],u)
        B.cancel(self.db,cancelled['id'],'test cancellation',u)

        rows=R.series(self.db,day(-1),day(1),'day')
        self.assertEqual([row['label'] for row in rows],[day(-1),day(0),day(1)])
        today_row=rows[1]
        self.assertEqual(today_row['bookings'],2)
        self.assertEqual(today_row['booked'],checked['total_paise']+cancelled['total_paise'])
        self.assertEqual(today_row['collected'],100000)
        self.assertEqual(today_row['cancels'],1)
        self.assertEqual(today_row['checkins'],1)
        self.assertEqual(today_row['checkouts'],1)
        self.assertTrue(all(not row[key] for row in (rows[0],rows[2]) for key in ('bookings','booked','collected','cancels','checkins','checkouts')))
        self.assertEqual(R.series_totals(rows),dict(bookings=2,booked=checked['total_paise']+cancelled['total_paise'],collected=100000,cancels=1,checkins=1,checkouts=1))
        chart=R.svg_bars(rows,'bookings')
        self.assertIn('viewBox="0 0 360 100"',chart)
        self.assertEqual(chart.count('<text '),3)
        c,_=self.login()
        dashboard=c.get('/admin/')
        reports=c.get('/admin/reports',query_string={'from':day(-1),'to':day(1)})
        self.assertEqual(dashboard.status_code,200)
        self.assertEqual(reports.status_code,200)
        for page in (dashboard,reports):
            html=page.get_data(as_text=True)
            self.assertEqual(html.count('class="kpi trend-kpi"'),6)
            self.assertIn('Bookings created per day',html)
            self.assertIn('Booked value by day created',html)
    def test_security_headers_xss_and_sql_injection(self):
        c=self.app.test_client();r=c.get('/');self.assertIn("default-src 'self'",r.headers['Content-Security-Policy']);self.assertIn("https://fonts.googleapis.com",r.headers['Content-Security-Policy']);self.assertIn("https://fonts.gstatic.com",r.headers['Content-Security-Policy']);self.assertEqual(r.headers['X-Frame-Options'],'DENY')
        self.book(1,name='<script>alert(1)</script>',phone='9855555555');oc,_=self.login();page=oc.get('/admin/bookings').get_data(as_text=True);self.assertNotIn('<script>alert(1)</script>',page);self.assertIn('&lt;script&gt;',page)
        self.assertEqual(oc.get('/admin/bookings',query_string={'q':"x' OR '1'='1"}).status_code,200);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)
    def test_admin_room_and_booking_management(self):
        c,_=self.login();t=self.csrf(c,'/admin/rooms')
        r=c.post('/admin/rooms/new',data=dict(csrf_token=t,category_id=1,number='301',price='2500'));self.assertEqual(r.status_code,302);self.assertEqual(self.db.execute("SELECT price_override_paise FROM rooms WHERE number='301'").fetchone()[0],250000)
        self.assertEqual(self.db.execute("SELECT inventory_status FROM room_categories WHERE id=1").fetchone()[0],'pending')
        b=self.book(3,ci=0,co=1);r=c.post(f"/admin/bookings/{b['id']}/checkin",data=dict(csrf_token=t));self.assertEqual(B.get_res(self.db,b['id'])['status'],'checked_in')
        r=c.post(f"/admin/bookings/{b['id']}/payment",data=dict(csrf_token=t,method='upi',amount='1000'));self.assertEqual(B.paid_total(self.db,b['id']),100000)
        r=c.post(f"/admin/bookings/{b['id']}/checkout",data=dict(csrf_token=t));self.assertEqual(B.get_res(self.db,b['id'])['status'],'checked_out')
        self.assertEqual(c.get(f"/admin/bookings/{b['id']}").status_code,200)
