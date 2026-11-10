"""Create or remove an explicitly labeled local dataset for admin/report testing."""
import argparse
import datetime as dtm
import os
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.db import get_db
from app.services import booking as B,payments as P
from app.util import today


def remove_demo_data(db):
    prop=db.execute("SELECT id FROM properties WHERE slug='demo-analytics'").fetchone()
    if not prop:
        print('No demo dataset found.')
        return
    room_ids=[r[0] for r in db.execute('SELECT id FROM rooms WHERE property_id=?',(prop['id'],))]
    reservation_ids=[r[0] for r in db.execute("SELECT id FROM reservations WHERE notes LIKE 'DEMO DATA:%' AND room_id IN (SELECT id FROM rooms WHERE property_id=?)",(prop['id'],))]
    payment_ids=[]
    if reservation_ids:
        res_marks=','.join('?' for _ in reservation_ids)
        payment_ids=[r[0] for r in db.execute(f'SELECT id FROM payments WHERE reservation_id IN ({res_marks})',reservation_ids)]
    if payment_ids:
        marks=','.join('?' for _ in payment_ids);db.execute(f'DELETE FROM transactions WHERE payment_id IN ({marks})',payment_ids)
    if reservation_ids:
        res_marks=','.join('?' for _ in reservation_ids)
        for table in ('notifications','cancellations','checkins','checkouts','payments'):
            db.execute(f'DELETE FROM {table} WHERE reservation_id IN ({res_marks})',reservation_ids)
        db.execute(f'DELETE FROM reservations WHERE id IN ({res_marks})',reservation_ids)
    db.execute("DELETE FROM customers WHERE email LIKE 'demo-%@example.invalid'")
    db.execute("DELETE FROM expense_entries WHERE description LIKE 'DEMO DATA:%'")
    remaining=db.execute('SELECT COUNT(*) FROM reservations WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(prop['id'],)).fetchone()[0]
    nights=db.execute('SELECT COUNT(*) FROM room_nights WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(prop['id'],)).fetchone()[0]
    if remaining or nights:
        print(f'Removed {len(reservation_ids)} demo reservations and finance records; kept the demo rooms/property because {remaining} reservation(s) and {nights} room-night(s) still reference them.')
        return
    if room_ids:
        marks=','.join('?' for _ in room_ids);db.execute(f'DELETE FROM room_amenities WHERE room_id IN ({marks})',room_ids);db.execute(f'DELETE FROM room_images WHERE room_id IN ({marks})',room_ids)
    db.execute('DELETE FROM rooms WHERE property_id=?',(prop['id'],))
    db.execute('DELETE FROM pricing_rules WHERE category_id IN (SELECT id FROM room_categories WHERE property_id=?)',(prop['id'],))
    db.execute('DELETE FROM room_categories WHERE property_id=?',(prop['id'],));db.execute('DELETE FROM properties WHERE id=?',(prop['id'],))
    print(f'Removed {len(reservation_ids)} demo reservations and the demo property/finance records.')


def seed_demo_data(db):
    owner=db.execute("SELECT id,email FROM users WHERE active=1 AND role IN('owner','admin') ORDER BY role='owner' DESC,id LIMIT 1").fetchone()
    if not owner:raise SystemExit('Create an active owner or admin account first.')
    if db.execute("SELECT 1 FROM reservations WHERE notes LIKE 'DEMO DATA:%' LIMIT 1").fetchone():
        raise SystemExit('Demo reservations already exist. Use --remove --confirm before reseeding.')
    db.execute("INSERT OR IGNORE INTO properties(slug,name,kind,address,description,active) VALUES('demo-analytics','Demo Analytics Property','Training data only','Local test dataset','Clearly labeled sample data for reports and room-map review.',1)")
    prop=db.execute("SELECT id FROM properties WHERE slug='demo-analytics'").fetchone();pid=prop['id'];db.execute('UPDATE properties SET active=1 WHERE id=?',(pid,))
    for name,ac,rate in [('Demo AC Room',1,280000),('Demo Non-AC Room',0,180000)]:
        db.execute('INSERT OR IGNORE INTO room_categories(property_id,name,ac,description,base_price_paise,max_adults,max_children) VALUES(?,?,?,?,?,2,1)',(pid,name,ac,'DEMO DATA only - not a live room category.',rate))
    db.execute('UPDATE room_categories SET active=1 WHERE property_id=?',(pid,))
    categories=db.execute('SELECT id,name,ac FROM room_categories WHERE property_id=? ORDER BY ac DESC,id',(pid,)).fetchall();rooms=[]
    for index in range(4):
        category=categories[index%len(categories)];number=f'DEMO-{101+index}'
        db.execute('INSERT OR IGNORE INTO rooms(property_id,category_id,number,floor,price_override_paise,description) VALUES(?,?,?,?,?,?)',(pid,category['id'],number,'Demo',None,'DEMO DATA only - generated for local admin testing.'))
        rooms.append(db.execute('SELECT id FROM rooms WHERE property_id=? AND number=?',(pid,number)).fetchone()['id'])
    base=today();scenarios=[(0,-28,2,70),(1,-24,1,100),(2,-18,3,100),(3,-12,2,50),(0,-6,1,100),(1,-2,2,75),(2,0,1,100),(3,2,2,0)]
    for index,(room_index,offset,nights,paid_pct) in enumerate(scenarios,1):
        checkin=base+dtm.timedelta(days=offset);checkout=checkin+dtm.timedelta(days=nights)
        created=max(checkin-dtm.timedelta(days=4),base-dtm.timedelta(days=29)).strftime('%Y-%m-%d 12:00:00')
        res=B.create_reservation(db,rooms[room_index],checkin.isoformat(),checkout.isoformat(),2,0,f'Demo Guest {index:02d}',f'999000{index:04d}',f'demo-{index:02d}@example.invalid',source='phone',user=owner,notes=f'DEMO DATA: generated report sample {index:02d}',allow_past=True)
        db.execute('UPDATE reservations SET created_at=?,updated_at=? WHERE id=?',(created,created,res['id']))
        if offset+nights<=0:db.execute("UPDATE reservations SET status='checked_out' WHERE id=?",(res['id'],))
        if paid_pct:
            paid=max(1,res['total_paise']*paid_pct//100);P.record_manual(db,res['id'],'cash',paid,'DEMO DATA',owner)
            db.execute('UPDATE payments SET created_at=?,paid_at=? WHERE reservation_id=?',(created,created,res['id']))
    capital=[(-330,'Property fit-out',18000000),(-240,'Room furnishings',7500000)]
    operating=[(-24,'Utilities',550000),(-13,'Housekeeping',350000),(-4,'Maintenance',275000)]
    for offset,category,amount in capital+operating:
        kind='capital' if offset<-100 else 'operating';incurred=(base+dtm.timedelta(days=offset)).isoformat()
        db.execute('INSERT INTO expense_entries(kind,category,description,amount_paise,incurred_on,created_by) VALUES(?,?,?,?,?,?)',(kind,category,f'DEMO DATA: sample {category.lower()} entry',amount,incurred,owner['id']))
    db.execute('UPDATE room_categories SET active=0 WHERE property_id=?',(pid,));db.execute('UPDATE properties SET active=0 WHERE id=?',(pid,))
    print('Demo dataset created: 4 rooms, 8 reservations, sample receipts, and 5 finance entries; hidden from public listings.')
    print('All demo content is labeled; remove it with: python scripts/seed_demo_data.py --remove --confirm')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove',action='store_true',help='remove this script\'s demo property and sample records')
    parser.add_argument('--confirm',action='store_true',help='confirm the local database change')
    args=parser.parse_args()
    if os.environ.get('APP_ENV','development').lower()=='production':raise SystemExit('Demo data is disabled when APP_ENV=production.')
    if not args.confirm:raise SystemExit('Pass --confirm to change the configured local database.')
    app=create_app()
    with app.app_context():
        db=get_db()
        if args.remove:remove_demo_data(db)
        else:seed_demo_data(db)


if __name__=='__main__':main()