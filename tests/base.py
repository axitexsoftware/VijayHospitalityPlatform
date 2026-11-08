import os,tempfile,unittest,datetime as dtm,re
from app import create_app
from app.db import connect,tx
from app.security import hash_password
def day(n):return (dtm.date.today()+dtm.timedelta(days=n)).isoformat()
class Base(unittest.TestCase):
    def setUp(self):
        self.path=tempfile.mktemp(suffix='.db');self.up=tempfile.mkdtemp()
        self.app=create_app(DATABASE=self.path,UPLOAD_DIR=self.up,RATELIMIT_ENABLED=False,SECRET_KEY='test-secret-key-not-for-production',RAZORPAY_KEY_ID='rzp_test_x',RAZORPAY_KEY_SECRET='sekret',RAZORPAY_WEBHOOK_SECRET='whsec',TAX_PERCENT=12)
        db=connect(self.path);self.db=db
        db.execute("INSERT INTO settings(key,value) VALUES('test_rate_label','TEST ONLY - NOT APPROVED')")
        db.execute("INSERT INTO settings(key,value) VALUES('tax_percent','12')")
        db.execute("INSERT INTO settings(key,value) VALUES('tax_percent_confirmed','1')")
        db.execute("INSERT INTO settings(key,value) VALUES('room_rates_confirmed','1')")
        db.execute("INSERT INTO settings(key,value) VALUES('room_occupancy_confirmed','1')")
        db.execute("INSERT INTO settings(key,value) VALUES('room_inventory_confirmed','1')")
        db.execute("INSERT INTO settings(key,value) VALUES('room_photos_confirmed','1')")
        db.execute("INSERT INTO properties(slug,name,kind,address) VALUES('u','Vijay Utsav','Hotel','Manpur, Gaya')")
        db.execute("INSERT INTO room_categories(property_id,name,ac,base_price_paise,max_adults,max_children) VALUES(1,'AC Room',1,180000,2,1)")
        db.execute("INSERT INTO room_categories(property_id,name,ac,base_price_paise,max_adults,max_children) VALUES(1,'Non-AC Room',0,90000,2,1)")
        for n,c in [('101',1),('102',1),('201',2)]:db.execute('INSERT INTO rooms(property_id,category_id,number) VALUES(1,?,?)',(c,n))
        db.execute("""UPDATE room_categories SET room_type=CASE WHEN ac=1 THEN 'AC room' ELSE 'Non-AC room' END,
            nightly_rate_paise=base_price_paise,max_adults_approved=max_adults,max_children_approved=max_children,max_occupancy=max_adults+max_children,
            details_status='approved',inventory_status='approved',occupancy_status='approved',rates_status='approved',tax_status='approved',
            photos_status='approved',publication_status='published'""")
        for e,r in [('owner@t.in','owner'),('rec@t.in','receptionist')]:db.execute('INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',(e,r,hash_password('Passw0rd!!x'),r))
        self.ctx=self.app.app_context();self.ctx.push()
    def tearDown(self):
        self.ctx.pop();self.db.close()
        for s in('','-wal','-shm'):
            try:os.remove(self.path+s)
            except OSError:pass
    def book(self,room=1,ci=10,co=12,name='Asha Kumar',phone='9876543210',**k):
        from app.services import booking as B
        return B.create_reservation(self.db,room,day(ci),day(co),k.pop('adults',2),k.pop('children',0),name,phone,k.pop('email','a@b.in'),**k)
    def csrf(self,c,url='/admin/login'):
        r=c.get(url);m=re.search(r'name="csrf_token" value="([^"]+)"',r.get_data(as_text=True)) or re.search(r'name="csrf" content="([^"]+)"',r.get_data(as_text=True));return m.group(1)
    def login(self,email='owner@t.in',pw='Passw0rd!!x'):
        c=self.app.test_client();t=self.csrf(c);r=c.post('/admin/login',data={'email':email,'password':pw,'csrf_token':t});return c,r
