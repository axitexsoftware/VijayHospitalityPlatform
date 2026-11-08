"""Razorpay (server-side order + signature verification) and manually recorded payments."""
import hmac,hashlib,json,io,requests
from flask import current_app
from ..db import tx,setting
from ..util import now_s,inr
from . import booking as B
class PaymentError(Exception):pass
class NotConfigured(PaymentError):pass
def configured():c=current_app.config;return bool(c['RAZORPAY_KEY_ID'] and c['RAZORPAY_KEY_SECRET'])
def _rz(method,path,payload=None):
    c=current_app.config
    if not configured():raise NotConfigured('Online payments are not configured.')
    r=requests.request(method,'https://api.razorpay.com/v1'+path,json=payload,auth=(c['RAZORPAY_KEY_ID'],c['RAZORPAY_KEY_SECRET']),timeout=20)
    if r.status_code>=400:raise PaymentError('Payment gateway error: '+(r.json().get('error',{}).get('description','') if r.headers.get('content-type','').startswith('application/json') else r.text[:100]))
    return r.json()
def _txn(db,pid,kind,amount=None,ref=None,payload=None):
    db.execute('INSERT INTO transactions(payment_id,kind,amount_paise,gateway_ref,payload,created_at) VALUES(?,?,?,?,?,?)',(pid,kind,amount,ref,json.dumps(payload)[:4000] if payload is not None else None,now_s()))
def create_order(db,rid):
    res=B.get_res(db,rid)
    if res['status']!='pending':raise PaymentError('This booking is not awaiting payment.')
    if not B.tax_configuration(db,res['category_id'])[1]:raise PaymentError('Online payments are disabled until management confirms the applicable tax setting.')
    pct=int(setting(db,'advance_percent','100'));due=B.outstanding(db,rid);amt=max(100,round(due*pct/100))
    if amt>due:amt=due
    if amt<=0:raise PaymentError('Nothing is due on this booking.')
    o=_rz('POST','/orders',{'amount':amt,'currency':'INR','receipt':res['ref'],'notes':{'booking':res['ref']}})
    with tx(db):
        pid=db.execute("INSERT INTO payments(reservation_id,method,amount_paise,status,gateway_order_id,created_at) VALUES(?,?,?,?,?,?)",(rid,'razorpay',amt,'created',o['id'],now_s())).lastrowid
        _txn(db,pid,'order',amt,o['id'],o)
    return dict(order_id=o['id'],amount=amt,currency='INR',key_id=current_app.config['RAZORPAY_KEY_ID'],reference=res['ref'])
def sign_ok(order_id,payment_id,signature):
    exp=hmac.new(current_app.config['RAZORPAY_KEY_SECRET'].encode(),f'{order_id}|{payment_id}'.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp,signature or '')
def _mark_paid(db,p,payment_id,payload):
    with tx(db):
        p=db.execute('SELECT * FROM payments WHERE id=?',(p['id'],)).fetchone()
        if p['status']=='paid':return B.get_res(db,p['reservation_id'])
        db.execute("UPDATE payments SET status='paid',gateway_payment_id=?,paid_at=? WHERE id=?",(payment_id,now_s(),p['id']));_txn(db,p['id'],'capture',p['amount_paise'],payment_id,payload)
        res=B.get_res(db,p['reservation_id'])
        if res['status']=='expired':
            try:
                B._insert_nights(db,res['room_id'],res['id'],res['check_in'],res['check_out']);db.execute("UPDATE reservations SET status='confirmed',hold_expires_at=NULL,updated_at=? WHERE id=?",(now_s(),res['id']));B.audit(db,None,'hold_revived_after_payment','reservation',res['id'])
            except B.Unavailable:
                db.execute('INSERT OR IGNORE INTO cancellations(reservation_id,reason,refund_due_paise,at) VALUES(?,?,?,?)',(res['id'],'Paid after hold expired; room no longer free',p['amount_paise'],now_s()));B.audit(db,None,'late_payment_refund_due','reservation',res['id'])
        elif res['status']=='pending':
            db.execute("UPDATE reservations SET status='confirmed',hold_expires_at=NULL,updated_at=? WHERE id=?",(now_s(),res['id']));B.audit(db,None,'payment_verified','reservation',res['id'],payment_id)
    return B.get_res(db,p['reservation_id'])
def verify(db,order_id,payment_id,signature):
    p=db.execute('SELECT * FROM payments WHERE gateway_order_id=?',(order_id,)).fetchone()
    if not p:raise PaymentError('Unknown payment order.')
    if not sign_ok(order_id,payment_id,signature):
        _txn(db,p['id'],'failure',None,payment_id,{'reason':'signature mismatch'});raise PaymentError('Payment could not be verified. If money was deducted, contact us with your booking reference.')
    return _mark_paid(db,p,payment_id,{'via':'checkout'})
def mark_failed(db,order_id,reason=''):
    p=db.execute('SELECT * FROM payments WHERE gateway_order_id=?',(order_id,)).fetchone()
    if p and p['status']=='created':
        with tx(db):db.execute("UPDATE payments SET status='failed' WHERE id=?",(p['id'],));_txn(db,p['id'],'failure',None,order_id,{'reason':reason[:200]})
def webhook(db,raw,signature):
    sec=current_app.config['RAZORPAY_WEBHOOK_SECRET']
    if not sec:raise NotConfigured('Webhook secret not configured.')
    if not hmac.compare_digest(hmac.new(sec.encode(),raw,hashlib.sha256).hexdigest(),signature or ''):raise PaymentError('Bad signature.')
    ev=json.loads(raw);t=ev.get('event');pay=ev.get('payload',{}).get('payment',{}).get('entity',{})
    p=db.execute('SELECT * FROM payments WHERE gateway_order_id=?',(pay.get('order_id'),)).fetchone() if pay else None
    if t=='payment.captured' and p:_mark_paid(db,p,pay['id'],{'via':'webhook'})
    elif t=='payment.failed' and p:mark_failed(db,pay.get('order_id'),'webhook')
    return t
def record_manual(db,rid,method,amount_paise,reference,user):
    if method not in('cash','upi','card','bank'):raise PaymentError('Choose a payment method.')
    amount_paise=int(amount_paise)
    if amount_paise<=0:raise PaymentError('Enter an amount greater than zero.')
    with tx(db):
        if amount_paise>B.outstanding(db,rid):raise PaymentError('Amount is more than the balance due.')
        pid=db.execute("INSERT INTO payments(reservation_id,method,amount_paise,status,reference,created_by,created_at,paid_at) VALUES(?,?,?,?,?,?,?,?)",(rid,method,amount_paise,'paid',(reference or '')[:60],user['id'],now_s(),now_s())).lastrowid
        _txn(db,pid,'capture',amount_paise,reference,{'manual':True})
        if B.get_res(db,rid)['status']=='pending':db.execute("UPDATE reservations SET status='confirmed',hold_expires_at=NULL,updated_at=? WHERE id=?",(now_s(),rid))
        B.audit(db,user,'payment_recorded','reservation',rid,f'{method} {amount_paise}')
    return pid
def refund(db,payment_id,amount_paise,user):
    p=db.execute('SELECT * FROM payments WHERE id=?',(payment_id,)).fetchone();amount_paise=int(amount_paise)
    if not p or p['status'] not in('paid','partially_refunded'):raise PaymentError('Only paid payments can be refunded.')
    if amount_paise<=0 or amount_paise>p['amount_paise']-p['refunded_paise']:raise PaymentError('Refund amount is more than what can be refunded.')
    payload=None
    if p['method']=='razorpay':payload=_rz('POST',f"/payments/{p['gateway_payment_id']}/refund",{'amount':amount_paise,'notes':{'by':user['email']}})
    with tx(db):
        tot=p['refunded_paise']+amount_paise;db.execute('UPDATE payments SET refunded_paise=?,status=? WHERE id=?',(tot,'refunded' if tot>=p['amount_paise'] else 'partially_refunded',p['id']))
        _txn(db,p['id'],'refund',amount_paise,(payload or {}).get('id'),payload);B.audit(db,user,'refund_processed','payment',p['id'],str(amount_paise))
def receipt_pdf(db,rid):
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas
    res=B.get_res(db,rid);pays=db.execute("SELECT * FROM payments WHERE reservation_id=? AND status IN('paid','partially_refunded','refunded') ORDER BY id",(rid,)).fetchall()
    prop=db.execute('SELECT * FROM properties WHERE id=?',(res['pid'],)).fetchone();buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=A5);W,H=A5;y=H-40
    def line(t,sz=10,b=False,dx=0):
        nonlocal y;c.setFont('Helvetica-Bold' if b else 'Helvetica',sz);c.drawString(36+dx,y,str(t));y-=sz+6
    line(prop['name'],16,True);line(prop['address'] or '',9);y-=6;line('PAYMENT RECEIPT',12,True);line(f"Booking {res['ref']}   Status: {res['status'].replace('_',' ')}",10)
    line(f"Guest: {res['cname']}  ({res['cphone']})");line(f"Room {res['rnumber']} ({res['cat_name']})   {res['check_in']} to {res['check_out']}  ({res['nights']} nights)");y-=6
    line(f"Room charges: Rs {res['room_total_paise']/100:,.2f}");line(f"Tax: Rs {res['tax_paise']/100:,.2f}");line(f"Total: Rs {res['total_paise']/100:,.2f}",11,True);y-=6;line('Payments',10,True)
    for p in pays:line(f"{p['paid_at'][:16]}  {p['method'].upper()}  Rs {p['amount_paise']/100:,.2f}  {p['gateway_payment_id'] or p['reference'] or ''}  {'(refunded Rs %.2f)'%(p['refunded_paise']/100) if p['refunded_paise'] else ''}",9)
    y-=4;line(f"Balance due: Rs {B.outstanding(db,rid)/100:,.2f}",11,True);c.showPage();c.save();return buf.getvalue()
