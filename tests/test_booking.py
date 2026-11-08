import threading,sqlite3
from tests.base import *
from app.services import booking as B
from app.db import connect
class T(Base):
    def test_search_available_and_unavailable(self):
        rs=B.search(self.db,day(10),day(12),2,0);self.assertEqual(len(rs),3)
        self.assertEqual(rs[0]['quote']['room_total'],360000);self.assertEqual(rs[0]['quote']['tax'],43200);self.assertEqual(rs[0]['quote']['total'],403200)
        for r in (1,2,3):self.book(r,phone='98765432%02d'%r)
        self.assertEqual(B.search(self.db,day(10),day(12),2,0),[])
    def test_book_ac_and_non_ac(self):
        a=self.book(1);n=self.book(3,phone='9123456780');self.assertEqual(a['cat_name'],'AC Room');self.assertEqual(n['cat_name'],'Non-AC Room');self.assertTrue(a['ref'].startswith('VH'));self.assertEqual(a['status'],'confirmed')
        self.assertEqual(len(B.search(self.db,day(10),day(12),2,0,category_id=2)),0)
    def test_validation(self):
        for kw,field in [(dict(phone='12345'),'phone'),(dict(name=''),'name'),(dict(email='bad'),'email')]:
            with self.assertRaises(B.Invalid) as e:self.book(**kw)
            self.assertIn(field,e.exception.errors)
        with self.assertRaises(B.Invalid):self.book(ci=-3,co=-1)
        with self.assertRaises(B.Invalid):self.book(ci=12,co=10)
        with self.assertRaises(B.Invalid):self.book(adults=0)
        with self.assertRaises(B.Invalid):self.book(adults=5)
    def test_duplicate_and_overlap_prevented(self):
        self.book(1)
        with self.assertRaises(B.Unavailable):self.book(1)
        with self.assertRaises(B.Unavailable):self.book(1,ci=11,co=13,name='Other',phone='9000000001')
        with self.assertRaises(B.Unavailable):self.book(1,ci=9,co=11,name='Other',phone='9000000001')
        self.book(1,ci=12,co=14,name='Next',phone='9000000002')  # check-out day is free for the next guest
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM room_nights WHERE room_id=1').fetchone()[0],4)
    def test_database_refuses_double_booking_even_if_code_is_bypassed(self):
        r=self.book(1)
        with self.assertRaises(sqlite3.IntegrityError):self.db.execute("INSERT INTO room_nights(room_id,night,reservation_id) VALUES(1,?,NULL)",(day(10),))
    def test_simultaneous_requests_only_one_wins(self):
        out=[];bar=threading.Barrier(8)
        def go(i):
            with self.app.app_context():
                db=connect(self.path);bar.wait()
                try:out.append(B.create_reservation(db,1,day(20),day(22),2,0,f'Guest {i}',f'98000000{i:02d}',None)['ref'])
                except B.Unavailable:out.append(None)
                finally:db.close()
        ts=[threading.Thread(target=go,args=(i,)) for i in range(8)];[t.start() for t in ts];[t.join() for t in ts]
        self.assertEqual(len([x for x in out if x]),1);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reservations WHERE room_id=1 AND check_in=?",(day(20),)).fetchone()[0],1)
    def test_modify_cancel_and_inventory_release(self):
        r=self.book(1);self.assertEqual(len(B.search(self.db,day(10),day(12),2,0,category_id=1)),1)
        m=B.modify(self.db,r['id'],day(10),day(13),2,0);self.assertEqual(m['nights'],3);self.assertEqual(m['total_paise'],604800)
        with self.assertRaises(B.Unavailable):self.book(1,ci=12,co=14,name='X Y',phone='9111111111')
        res,due=B.cancel(self.db,r['id'],'plans changed',by_customer=True);self.assertEqual(res['status'],'cancelled')
        self.assertEqual(len(B.search(self.db,day(10),day(13),2,0,category_id=1)),2)
        with self.assertRaises(B.Invalid):B.cancel(self.db,r['id'])
    def test_late_cancellation_refund_policy(self):
        from app.services import payments as P
        r=self.book(1,ci=0,co=1,name='Late One');uid=self.db.execute("SELECT id FROM users LIMIT 1").fetchone()[0];u={'id':uid,'email':'x'}
        P.record_manual(self.db,r['id'],'cash',100000,'',u);_,due=B.cancel(self.db,r['id'],'',by_customer=True);self.assertEqual(due,0)
        r2=self.book(1,ci=10,co=11,name='Early One',phone='9222222222');P.record_manual(self.db,r2['id'],'upi',100000,'',u);_,due=B.cancel(self.db,r2['id'],'',by_customer=True);self.assertEqual(due,100000)
    def test_hold_expires_and_releases_room(self):
        r=self.book(1,hold_minutes=30);self.db.execute("UPDATE reservations SET hold_expires_at='2000-01-01 00:00:00' WHERE id=?",(r['id'],))
        self.assertEqual(len(B.search(self.db,day(10),day(12),2,0,category_id=1)),2);self.assertEqual(B.get_res(self.db,r['id'])['status'],'expired')
    def test_block_room_and_status_board(self):
        B.block_room(self.db,3,day(0),day(2),'Repairs',None);self.assertEqual(len(B.search(self.db,day(0),day(1),1,0,category_id=2)),0)
        b={x['number']:x for x in B.room_status_board(self.db)};self.assertEqual(b['201']['state'],'blocked');self.assertEqual(b['101']['state'],'available')
        with self.assertRaises(B.Unavailable):B.block_room(self.db,3,day(1),day(3),'x',None)
    def test_check_in_out_and_reassign(self):
        u={'id':self.db.execute("SELECT id FROM users LIMIT 1").fetchone()[0],'email':'x'};r=self.book(1,ci=0,co=2)
        r=B.change_room(self.db,r['id'],2,u);self.assertEqual(r['rnumber'],'102')
        r=B.check_in(self.db,r['id'],u,None,'Aadhaar','1234');self.assertEqual(r['status'],'checked_in')
        r,bal=B.check_out(self.db,r['id'],u);self.assertEqual(r['status'],'checked_out');self.assertEqual(bal,r['total_paise'])
        with self.assertRaises(B.Invalid):B.check_in(self.db,self.book(1,ci=5,co=6,phone='9333333333')['id'],u)
