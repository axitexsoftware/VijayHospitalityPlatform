(function(){
var open=document.getElementById('chat-open'),box=document.getElementById('chat'),log=document.getElementById('chat-log'),form=document.getElementById('chat-form'),inp=document.getElementById('chat-in'),language=document.getElementById('chat-language');
if(!open||!box||!form||!inp)return;
var csrf=document.querySelector('meta[name=csrf]').content,busy=false,started=false,historyReady=false,aiReady=box.dataset.aiReady==='true',storageKey='vijay-chat-language';
try{var saved=localStorage.getItem(storageKey);if(language&&['auto','en','hi'].includes(saved))language.value=saved}catch(e){}
function isHindi(){return language&&language.value==='hi'}
function words(en,hi){return isHindi()?hi:en}
function setMessage(item,text,createdAt){item.replaceChildren();var content=document.createElement('span');content.className='cm-text';content.textContent=text;item.appendChild(content);var time=document.createElement('time');time.className='cm-time';time.textContent=createdAt?createdAt.slice(11,16):new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});item.appendChild(time)}
function add(text,kind,createdAt){var item=document.createElement('div');item.className='cm '+kind;setMessage(item,text,createdAt);log.appendChild(item);log.scrollTop=log.scrollHeight;return item}
function welcome(){if(started)return;started=true;var message=aiReady?words('Namaste! Welcome to Vijay Utsav Hall. I am Kabita, your virtual receptionist. I can help check room availability, answer supported property questions and route event enquiries. How may I assist you?','नमस्ते! विजय उत्सव हॉल में आपका स्वागत है। मैं कबिता, आपकी वर्चुअल रिसेप्शनिस्ट हूँ। मैं कमरे की उपलब्धता देखने, संपत्ति से जुड़े सवालों के जवाब देने और कार्यक्रम की पूछताछ भेजने में मदद कर सकती हूँ। मैं आपकी कैसे सहायता करूँ?'):words('Namaste! I can check room availability when you share your stay dates, or send an event enquiry to our team. The conversational AI service is not configured, so I cannot complete a full booking or answer general questions yet.','नमस्ते! आप ठहरने की तारीखें बताकर कमरे की उपलब्धता पूछ सकते हैं या कार्यक्रम की पूछताछ हमारी टीम को भेज सकते हैं। संवादात्मक AI सेवा अभी कॉन्फ़िगर नहीं है, इसलिए मैं पूरी बुकिंग या सामान्य सवालों में अभी मदद नहीं कर सकती।');add(message,'b')}
function loadHistory(){fetch('/api/chat').then(function(response){if(!response.ok)throw new Error('history unavailable');return response.json()}).then(function(data){(data.messages||[]).forEach(function(message){add(message.content,message.role==='user'?'u':'b',message.created_at)});if(data.messages&&data.messages.length)started=true}).catch(function(){}).finally(function(){historyReady=true;if(box.classList.contains('open'))welcome();inp.disabled=false;log.scrollTop=log.scrollHeight})}
function updateLanguageUI(){
 inp.placeholder=words('Type your message','अपना संदेश लिखें');inp.setAttribute('aria-label',words('Your message','आपका संदेश'));
 open.setAttribute('aria-label',words('Chat with Kabita','कबिता से बात करें'));var launcher=open.querySelector('[data-chat-open-label]');if(launcher)launcher.textContent=words('Chat with Kabita','कबिता से बात करें');
 var title=document.querySelector('[data-chat-title]'),subtitle=document.querySelector('[data-chat-subtitle]'),languageLabel=document.querySelector('[data-chat-language-label]');
 if(title)title.textContent=words('Kabita · Vijay Utsav Hall','कबिता · विजय उत्सव हॉल');if(subtitle)subtitle.textContent=words('Your Personal Hotel Receptionist · English / हिंदी','आपकी व्यक्तिगत होटल रिसेप्शनिस्ट · English / हिंदी');if(languageLabel)languageLabel.textContent=words('Chat language','चैट की भाषा');
 form.querySelector('button').textContent=words('Send','भेजें');
 document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.textContent=isHindi()?button.dataset.hi:button.dataset.en});
}
function toggle(){box.classList.toggle('open');open.setAttribute('aria-expanded',box.classList.contains('open'));
 if(box.classList.contains('open')){if(historyReady)welcome();inp.focus()}}
open.addEventListener('click',toggle);document.getElementById('chat-close').addEventListener('click',toggle);
document.querySelectorAll('[data-open-chat]').forEach(function(button){button.addEventListener('click',function(){if(!box.classList.contains('open'))toggle()})});
if(language)language.addEventListener('change',function(){try{localStorage.setItem(storageKey,language.value)}catch(e){}updateLanguageUI()});
document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.addEventListener('click',function(){if(busy)return;inp.value=isHindi()?button.dataset.hi:button.dataset.en;form.requestSubmit()})});
updateLanguageUI();
inp.disabled=true;loadHistory();
form.addEventListener('submit',function(e){e.preventDefault();var message=inp.value.trim();if(!message||busy)return;inp.value='';add(message,'u');busy=true;var typing=add(words('Typing…','लिख रहा/रही हूँ…'),'b');
 fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({message:message,language:language?language.value:'auto'})})
 .then(function(response){return response.json().then(function(data){return{ok:response.ok,data:data}})})
 .then(function(result){var data=result.data;setMessage(typing,data.reply||data.error||words('Sorry, something went wrong. Please try again.','क्षमा करें, कुछ समस्या हुई। कृपया फिर प्रयास करें।'));if(data.booking||data.link){var link=document.createElement('a');link.href=data.booking?'/booking/'+data.booking:data.link;link.textContent=data.booking?words('View booking ','बुकिंग देखें ')+data.booking:words('Review available rooms','उपलब्ध कमरे देखें');link.style.display='block';link.style.marginTop='6px';typing.insertBefore(link,typing.querySelector('.cm-time'))}})
 .catch(function(){setMessage(typing,words('Connection problem. Please try again.','कनेक्शन में समस्या है। कृपया फिर प्रयास करें।'))})
 .finally(function(){busy=false;log.scrollTop=log.scrollHeight})
});
})();