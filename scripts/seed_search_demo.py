"""Seed clearly labeled public test rooms for local search and room-detail UI review."""
import argparse
import os
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import current_app
from app import create_app
from app.db import get_db,tx,set_setting
from app.services import media as M

SLUG='demo-search-preview'
PROPERTY_NAME='Vijay Utsav Hall'
CATEGORY='AC Room'
ROOMS=(('101','1'),('102','2'),('103','3'),('104','4'))
ROOM_DESCRIPTION=('TEST DATA ONLY: Temporary room description for UI checks. Illustrative deluxe AC room, '
    'approximately 18 sq m, with a king bed, private bathroom with hot and cold shower, LED television, '
    'wardrobe, work desk, tea and coffee maker, fresh towels, and daily housekeeping. The room details '
    'and Rs 4,200 nightly test rate are not confirmed real inventory.')
ROOM_SUMMARY='TEST DATA ONLY: Temporary room description for UI checks.'
ROOM_PHOTOS=(
    ('img/room-ac-480.jpg','Test room photo 1 - bed and sitting area'),
    ('img/room-b-480.jpg','Test room photo 2 - opposite angle'),
    ('img/room-c-480.jpg','Test room photo 3 - room interior'),
    ('img/room-d-480.jpg','Test room photo 4 - room interior'),
)


def seed_search_demo(db):
    with tx(db):
        db.execute("""CREATE TABLE IF NOT EXISTS demo_search_seed_state(
            key TEXT PRIMARY KEY,was_present INTEGER NOT NULL,value TEXT)""")
        if not db.execute('SELECT 1 FROM demo_search_seed_state LIMIT 1').fetchone():
            for key in ('room_rates_confirmed','room_occupancy_confirmed','room_inventory_confirmed',
                        'tax_percent_confirmed','tax_percent'):
                old=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                db.execute('INSERT INTO demo_search_seed_state(key,was_present,value) VALUES(?,?,?)',
                           (key,1 if old else 0,old['value'] if old else None))
        db.execute("""INSERT INTO properties(slug,name,kind,address,description,active)
            VALUES(?,?,?,?,?,1) ON CONFLICT(slug) DO UPDATE SET active=1""",
            (SLUG,PROPERTY_NAME,'Local test listing',
             'Manpur, Gaya, Bihar','TEST DATA ONLY. Sample listing for local search and room-detail UI testing.'))
        prop=db.execute('SELECT id FROM properties WHERE slug=?',(SLUG,)).fetchone()
        pid=prop['id']
        legacy=db.execute("SELECT id FROM room_categories WHERE property_id=? AND name='DEMO DATA - Deluxe AC Test Room'",(pid,)).fetchone()
        if legacy:
            if db.execute('SELECT 1 FROM reservations WHERE room_id IN (SELECT id FROM rooms WHERE category_id=?) LIMIT 1',(legacy['id'],)).fetchone():
                raise SystemExit('Legacy demo rooms have reservations; cancel or move them before reseeding.')
            db.execute('DELETE FROM room_nights WHERE room_id IN (SELECT id FROM rooms WHERE category_id=?)',(legacy['id'],))
            db.execute('DELETE FROM room_amenities WHERE room_id IN (SELECT id FROM rooms WHERE category_id=?)',(legacy['id'],))
            db.execute('DELETE FROM room_images WHERE room_id IN (SELECT id FROM rooms WHERE category_id=?)',(legacy['id'],))
            db.execute('DELETE FROM rooms WHERE category_id=?',(legacy['id'],))
            db.execute('DELETE FROM room_categories WHERE id=?',(legacy['id'],))
        db.execute("""UPDATE properties SET name=?,kind='Local test listing',address=?,
            description='TEST DATA ONLY. Sample listing for local search and room-detail UI testing.',active=1 WHERE id=?""",
            (PROPERTY_NAME,'Manpur, Gaya, Bihar',pid))
        db.execute("""INSERT INTO room_categories(property_id,name,room_type,ac,description,
            base_price_paise,max_adults,max_children,active,nightly_rate_paise,max_adults_approved,
            max_children_approved,max_occupancy,currency,tax_percent,details_status,inventory_status,
            occupancy_status,rates_status,tax_status,photos_status,publication_status,created_at,updated_at)
            VALUES(?,?,?,1,?,420000,2,1,1,420000,2,1,3,'INR',12,'approved','approved',
            'approved','approved','approved','approved','published',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(property_id,name) DO UPDATE SET room_type=excluded.room_type,ac=1,
            description=excluded.description,base_price_paise=420000,nightly_rate_paise=420000,
            max_adults=2,max_children=1,max_adults_approved=2,max_children_approved=1,max_occupancy=3,
            tax_percent=12,details_status='approved',inventory_status='approved',occupancy_status='approved',
            rates_status='approved',tax_status='approved',photos_status='approved',
            publication_status='published',active=1,updated_at=CURRENT_TIMESTAMP""",
            (pid,CATEGORY,'Demo deluxe AC room',ROOM_DESCRIPTION))
        category=db.execute('SELECT id FROM room_categories WHERE property_id=? AND name=?',(pid,CATEGORY)).fetchone()
        cid=category['id']
        for number,floor in ROOMS:
            db.execute("""INSERT INTO rooms(property_id,category_id,number,floor,description,status)
                VALUES(?,?,?,?,?,'active') ON CONFLICT(property_id,number) DO UPDATE SET
                category_id=excluded.category_id,floor=excluded.floor,description=excluded.description,status='active'""",
                    (pid,cid,number,floor,ROOM_SUMMARY))
            room_id=db.execute('SELECT id FROM rooms WHERE property_id=? AND number=?',(pid,number)).fetchone()['id']
            for name in ('Housekeeping','Private bathroom'):
                db.execute('INSERT OR IGNORE INTO amenities(name) VALUES(?)',(name,))
                amenity_id=db.execute('SELECT id FROM amenities WHERE name=?',(name,)).fetchone()['id']
                db.execute('INSERT OR IGNORE INTO room_amenities(room_id,amenity_id) VALUES(?,?)',(room_id,amenity_id))
            for photo_order,(path,alt) in enumerate(ROOM_PHOTOS,1):
                db.execute("""INSERT INTO room_images(room_id,path,alt,sort)
                    SELECT ?,?,?,? WHERE NOT EXISTS(
                    SELECT 1 FROM room_images WHERE room_id=? AND path=?)""",
                    (room_id,path,alt,photo_order,room_id,path))
        for key in ('room_rates_confirmed','room_occupancy_confirmed','room_inventory_confirmed'):
            set_setting(db,key,'1')
        saved_tax=db.execute("SELECT was_present,value FROM demo_search_seed_state WHERE key='tax_percent'").fetchone()
        if saved_tax and saved_tax['was_present']:set_setting(db,'tax_percent',saved_tax['value'])
        elif saved_tax:db.execute("DELETE FROM settings WHERE key='tax_percent'")
        saved_tax_confirmed=db.execute("SELECT was_present,value FROM demo_search_seed_state WHERE key='tax_percent_confirmed'").fetchone()
        if saved_tax_confirmed and saved_tax_confirmed['was_present']:set_setting(db,'tax_percent_confirmed',saved_tax_confirmed['value'])
        elif saved_tax_confirmed:db.execute("DELETE FROM settings WHERE key='tax_percent_confirmed'")
    print(f'Seeded {len(ROOMS)} clearly labeled demo rooms at Rs 4,200/night with 12% test tax and 4 sample photos per room.')
    print('Photo rotation works as a multi-angle preview; upload a real 2:1 panorama for a continuous 360-degree tour.')
    print('Remove with: python scripts/seed_search_demo.py --remove --confirm')


def remove_search_demo(db):
    prop=db.execute('SELECT id FROM properties WHERE slug=?',(SLUG,)).fetchone()
    pid=prop['id'] if prop else None
    if pid and db.execute('SELECT 1 FROM reservations WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?) LIMIT 1',(pid,)).fetchone():
        raise SystemExit('Demo rooms have reservations; cancel or move them before removing demo data.')
    with tx(db):
        if pid:
            for row in db.execute("""SELECT path FROM room_images WHERE room_id IN
                    (SELECT id FROM rooms WHERE property_id=?)""",(pid,)).fetchall():
                if row['path'].startswith('uploads/'):
                    M.delete_upload(row['path'],current_app.config['UPLOAD_DIR'])
            for row in db.execute("""SELECT path FROM room_panorama WHERE room_id IN
                    (SELECT id FROM rooms WHERE property_id=?)""",(pid,)).fetchall():
                if row['path'].startswith('uploads/'):
                    M.delete_upload(row['path'],current_app.config['UPLOAD_DIR'])
            db.execute('DELETE FROM room_nights WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(pid,))
            db.execute('DELETE FROM room_amenities WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(pid,))
            db.execute('DELETE FROM room_images WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(pid,))
            db.execute('DELETE FROM room_panorama WHERE room_id IN (SELECT id FROM rooms WHERE property_id=?)',(pid,))
            db.execute('DELETE FROM rooms WHERE property_id=?',(pid,))
            db.execute('DELETE FROM room_categories WHERE property_id=?',(pid,))
            db.execute('DELETE FROM properties WHERE id=?',(pid,))
        state=db.execute('SELECT key,was_present,value FROM demo_search_seed_state').fetchall() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_search_seed_state'").fetchone() else []
        for item in state:
            if item['was_present']:set_setting(db,item['key'],item['value'])
            else:db.execute('DELETE FROM settings WHERE key=?',(item['key'],))
        db.execute('DROP TABLE IF EXISTS demo_search_seed_state')
    print('Removed the demo search listing and restored the previous inventory/tax settings.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove',action='store_true',help='remove this script\'s public demo listing')
    parser.add_argument('--confirm',action='store_true',help='confirm the configured local database change')
    args=parser.parse_args()
    if os.environ.get('APP_ENV','development').lower()=='production':
        raise SystemExit('Public demo data is disabled when APP_ENV=production.')
    if not args.confirm:
        raise SystemExit('Pass --confirm to change the configured local database.')
    app=create_app()
    with app.app_context():
        db=get_db()
        if args.remove:remove_search_demo(db)
        else:seed_search_demo(db)


if __name__=='__main__':main()
