import sys,os,getpass;sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.db import get_db
from app.security import hash_password,password_problem
app=create_app()
with app.app_context():
    email=input('Admin email: ').strip().lower();name=input('Name: ').strip();role=(input('Role (owner/admin/receptionist) [owner]: ').strip() or 'owner').lower();pw=getpass.getpass('Password: ')
    p=password_problem(pw)
    if p:sys.exit(p)
    get_db().execute('INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',(email,name,hash_password(pw),role));print('User created.')
