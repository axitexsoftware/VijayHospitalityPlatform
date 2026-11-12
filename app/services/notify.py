"""Customer notifications. Every attempt is logged; nothing is recorded as 'sent' unless the provider accepted it."""
import smtplib,requests
from email.message import EmailMessage
from email.utils import formataddr
from string import Template
from flask import current_app
from ..util import now_s,d,today
from ..configuration import load as hotel_configuration
from . import booking as B
def _text(res,event,db):
    fmt=lambda s:d(s).strftime('%d %b %Y');base=f"Booking {res['ref']} at {res['pname']}, Room {res['rnumber']} ({res['cat_name']}), {fmt(res['check_in'])} to {fmt(res['check_out'])}."
    paid=B.paid_total(db,res['id']);due=max(0,res['total_paise']-paid)
    m={'booking_confirmed':f"Hello {res['cname']}, your booking is confirmed. {base} Total Rs {res['total_paise']/100:,.0f}. Check-in is from 12:00 PM.",
    'payment_received':f"Hello {res['cname']}, we received your payment. {base} Paid Rs {paid/100:,.0f}; balance Rs {due/100:,.0f}.",
    'booking_cancelled':f"Hello {res['cname']}, your booking {res['ref']} has been cancelled.",
    'booking_modified':f"Hello {res['cname']}, your booking was updated. {base} New total Rs {res['total_paise']/100:,.0f}.",
    'checkin_reminder':f"Hello {res['cname']}, a reminder that your stay begins tomorrow. {base} Check-in is from 12:00 PM.",
    'checked_in':f"Welcome {res['cname']}! You are checked in to Room {res['rnumber']}. {base}",
    'checked_out':f"Thank you for staying with us, {res['cname']}. Your check-out for booking {res['ref']} is complete. Balance Rs {due/100:,.0f}."}
    template=hotel_configuration(db,current_app.config)['notifications']['templates'].get(event)
    if not template:return m[event]
    values={'guest':res['cname'],'reference':res['ref'],'property':res['pname'],'room':res['rnumber'],
        'category':res['cat_name'],'check_in':fmt(res['check_in']),'check_out':fmt(res['check_out']),
        'total':f"{res['total_paise']/100:,.0f}",'paid':f"{paid/100:,.0f}",'balance':f"{due/100:,.0f}"}
    return Template(template).safe_substitute(values)
def _log(db,rid,ch,ev,rcpt,st,detail=''):db.execute('INSERT INTO notifications(reservation_id,channel,event,recipient,status,detail,created_at) VALUES(?,?,?,?,?,?,?)',(rid,ch,ev,rcpt,st,detail[:300],now_s()))
def send(db,rid,event):
    c=current_app.config;hotel=hotel_configuration(db,c);res=B.get_res(db,rid);text=_text(res,event,db)
    sender=hotel['integrations']['email']
    from_email=sender['from_email'] or c['SMTP_FROM']
    from_name=sender['from_name']
    if res['cemail'] and c['SMTP_HOST'] and from_email:
        try:
            m=EmailMessage();m['Subject']=f"{res['pname']}: "+event.replace('_',' ').capitalize();m['From']=formataddr((from_name,from_email));m['To']=res['cemail'];m.set_content(text)
            with smtplib.SMTP(c['SMTP_HOST'],c['SMTP_PORT'],timeout=15) as s:
                s.starttls()
                if c['SMTP_USER']:s.login(c['SMTP_USER'],c['SMTP_PASSWORD'])
                s.send_message(m)
            _log(db,rid,'email',event,res['cemail'],'sent')
        except Exception as e:_log(db,rid,'email',event,res['cemail'],'failed',str(e))
    else:_log(db,rid,'email',event,res['cemail'],'skipped','Email not configured or guest has no email')
    if c['WHATSAPP_TOKEN'] and c['WHATSAPP_PHONE_ID']:
        try:
            r=requests.post(f"https://graph.facebook.com/v19.0/{c['WHATSAPP_PHONE_ID']}/messages",headers={'Authorization':'Bearer '+c['WHATSAPP_TOKEN']},json={'messaging_product':'whatsapp','to':'91'+res['cphone'],'type':'text','text':{'body':text}},timeout=15)
            _log(db,rid,'whatsapp',event,res['cphone'],'sent' if r.ok else 'failed',f'HTTP {r.status_code} {"" if r.ok else r.text[:150]}')
        except Exception as e:_log(db,rid,'whatsapp',event,res['cphone'],'failed',str(e))
    else:_log(db,rid,'whatsapp',event,res['cphone'],'skipped','WhatsApp provider not configured')
    if c['SMS_WEBHOOK_URL']:
        try:
            r=requests.post(c['SMS_WEBHOOK_URL'],headers={'Authorization':'Bearer '+c['SMS_WEBHOOK_TOKEN']},json={'to':'91'+res['cphone'],'message':text},timeout=15)
            _log(db,rid,'sms',event,res['cphone'],'sent' if r.ok else 'failed',f'HTTP {r.status_code}')
        except Exception as e:_log(db,rid,'sms',event,res['cphone'],'failed',str(e))
    else:_log(db,rid,'sms',event,res['cphone'],'skipped','SMS provider not configured')
def send_reminders(db):
    import datetime as dtm;t=(today()+dtm.timedelta(days=1)).isoformat();n=0
    for r in db.execute("SELECT id FROM reservations WHERE status='confirmed' AND check_in=? AND NOT EXISTS(SELECT 1 FROM notifications x WHERE x.reservation_id=reservations.id AND x.event='checkin_reminder')",(t,)).fetchall():send(db,r[0],'checkin_reminder');n+=1
    return n
