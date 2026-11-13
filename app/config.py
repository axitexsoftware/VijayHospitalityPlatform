import os
from dotenv import load_dotenv
load_dotenv()
def load(**over):
    e=os.environ.get;prod=e('APP_ENV','development')=='production';tax=e('TAX_PERCENT')
    c=dict(ENV='production' if prod else 'development',SECRET_KEY=e('SECRET_KEY',''),DATABASE=e('DATABASE_PATH','instance/hotel.db'),SITE_URL=e('SITE_URL','http://localhost:5000').rstrip('/'),
     TIMEZONE=e('TIMEZONE','Asia/Kolkata'),UPLOAD_DIR=e('UPLOAD_DIR','app/static/uploads'),
     TAX_PERCENT=float(tax or '0'),TAX_PERCENT_CONFIGURED=tax is not None,HOLD_MINUTES=int(e('HOLD_MINUTES','30')),CANCEL_FREE_HOURS=int(e('CANCEL_FREE_HOURS','24')),LATE_CANCEL_REFUND_PERCENT=int(e('LATE_CANCEL_REFUND_PERCENT','0')),
     MAX_STAY_NIGHTS=int(e('MAX_STAY_NIGHTS','30')),
     RAZORPAY_KEY_ID=e('RAZORPAY_KEY_ID',''),RAZORPAY_KEY_SECRET=e('RAZORPAY_KEY_SECRET',''),RAZORPAY_WEBHOOK_SECRET=e('RAZORPAY_WEBHOOK_SECRET',''),
     LLM_BASE_URL=e('LLM_BASE_URL','https://api.openai.com/v1').rstrip('/'),LLM_API_KEY=e('LLM_API_KEY',''),LLM_MODEL=e('LLM_MODEL','gpt-4o-mini'),
     VIRA_VOICE_ENABLED=e('VIRA_VOICE_ENABLED','1')=='1',VIRA_TTS_ENABLED=e('VIRA_TTS_ENABLED','1')=='1',VIRA_SPEECH_LANG=e('VIRA_SPEECH_LANG','en-IN'),
     SMTP_HOST=e('SMTP_HOST',''),SMTP_PORT=int(e('SMTP_PORT','587')),SMTP_USER=e('SMTP_USER',''),SMTP_PASSWORD=e('SMTP_PASSWORD',''),SMTP_FROM=e('SMTP_FROM',''),
     WHATSAPP_TOKEN=e('WHATSAPP_TOKEN',''),WHATSAPP_PHONE_ID=e('WHATSAPP_PHONE_ID',''),SMS_WEBHOOK_URL=e('SMS_WEBHOOK_URL',''),SMS_WEBHOOK_TOKEN=e('SMS_WEBHOOK_TOKEN',''),
     MAX_CONTENT_LENGTH=5*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=prod,RATELIMIT_ENABLED=True)
    c.update(over)
    if 'TAX_PERCENT' in over and 'TAX_PERCENT_CONFIGURED' not in over:c['TAX_PERCENT_CONFIGURED']=True
    if prod and (len(c['SECRET_KEY'])<32):raise RuntimeError('Set SECRET_KEY (32+ random characters) in the environment for production.')
    if not c['SECRET_KEY']:c['SECRET_KEY']='dev-only-not-secret'
    return c
