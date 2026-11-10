"""Create property records without inventing room categories or guest-facing room details."""
import sys,os;sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.db import get_db
app=create_app()
with app.app_context():
    db=get_db()
    P=[('vijay-utsav','Vijay Utsav','Hotel','Vijay Utsav Hall, South of Sikhar More, Post Baragandhar, Manpur, Gaya, Bihar, India','Hotel in Manpur, Gaya with AC and Non-AC rooms.'),
       ('vijay-garden','Vijay Garden','Hotel, banquet and events','Vijay Garden, South of Sikhar More, Manpur, Gaya, Bihar, India','Hotel rooms and a landscaped banquet lawn with a stage.')]
    for s,n,k,a,d in P:db.execute('INSERT OR IGNORE INTO properties(slug,name,kind,address,description) VALUES(?,?,?,?,?)',(s,n,k,a,d))
    print('Seeded property records. Add management-confirmed room categories, room numbers, rates, occupancy and photos in Admin before publishing.')
