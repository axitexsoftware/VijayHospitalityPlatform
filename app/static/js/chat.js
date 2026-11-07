(function(){
var open=document.getElementById('chat-open'),box=document.getElementById('chat'),log=document.getElementById('chat-log'),form=document.getElementById('chat-form'),inp=document.getElementById('chat-in'),language=document.getElementById('chat-language');
if(!open||!box||!form||!inp)return;
var csrf=document.querySelector('meta[name=csrf]').content,busy=false,started=false,storageKey='vijay-chat-language';
try{var saved=localStorage.getItem(storageKey);if(language&&['auto','en','hi'].includes(saved))language.value=saved}catch(e){}
function isHindi(){return language&&language.value==='hi'}
function words(en,hi){return isHindi()?hi:en}
function add(text,kind){var item=document.createElement('div');item.className='cm '+kind;item.textContent=text;log.appendChild(item);log.scrollTop=log.scrollHeight;return item}
function updateLanguageUI(){
 inp.placeholder=words('Type your message','अपना संदेश लिखें');inp.setAttribute('aria-label',words('Your message','आपका संदेश'));
 open.setAttribute('aria-label',words('Chat with reception','रिसेप्शन से बात करें'));var launcher=open.querySelector('[data-chat-open-label]');if(launcher)launcher.textContent=words('Chat with reception','रिसेप्शन से बात करें');
 var title=document.querySelector('[data-chat-title]'),subtitle=document.querySelector('[data-chat-subtitle]'),languageLabel=document.querySelector('[data-chat-language-label]');
 if(title)title.textContent=words('Virtual receptionist','वर्चुअल रिसेप्शनिस्ट');if(subtitle)subtitle.textContent=words('Room help · English / हिंदी','कमरों की जानकारी · English / हिंदी');if(languageLabel)languageLabel.textContent=words('Chat language','चैट की भाषा');
 form.querySelector('button').textContent=words('Send','भेजें');
 document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.textContent=isHindi()?button.dataset.hi:button.dataset.en});
}
function toggle(){box.classList.toggle('open');open.setAttribute('aria-expanded',box.classList.contains('open'));
 if(box.classList.contains('open')&&!started){started=true;add(words('Welcome to Vijay Hospitality. I can check room availability and help with bookings. When would you like to stay?','विजय हॉस्पिटैलिटी में आपका स्वागत है। मैं कमरे की उपलब्धता और बुकिंग में मदद कर सकता/सकती हूँ। आप कब ठहरना चाहते हैं?'),'b');inp.focus()}}
open.addEventListener('click',toggle);document.getElementById('chat-close').addEventListener('click',toggle);
if(language)language.addEventListener('change',function(){try{localStorage.setItem(storageKey,language.value)}catch(e){}updateLanguageUI();if(started)add(words('Language preference updated.','भाषा की प्राथमिकता बदल दी गई है।'),'b')});
document.querySelectorAll('[data-chat-prompt]').forEach(function(button){button.addEventListener('click',function(){if(busy)return;inp.value=isHindi()?button.dataset.hi:button.dataset.en;form.requestSubmit()})});
updateLanguageUI();
form.addEventListener('submit',function(e){e.preventDefault();var message=inp.value.trim();if(!message||busy)return;inp.value='';add(message,'u');busy=true;var typing=add(words('Typing…','लिख रहा/रही हूँ…'),'b');
 fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({message:message,language:language?language.value:'auto'})})
 .then(function(response){return response.json().then(function(data){return{ok:response.ok,data:data}})})
 .then(function(result){var data=result.data;typing.textContent=data.reply||data.error||words('Sorry, something went wrong. Please try again.','क्षमा करें, कुछ समस्या हुई। कृपया फिर प्रयास करें।');if(data.booking||data.link){var link=document.createElement('a');link.href=data.booking?'/booking/'+data.booking:data.link;link.textContent=data.booking?words('View booking ','बुकिंग देखें ')+data.booking:words('Review available rooms','उपलब्ध कमरे देखें');link.style.display='block';link.style.marginTop='6px';typing.appendChild(link)}})
 .catch(function(){typing.textContent=words('Connection problem. Please try again.','कनेक्शन में समस्या है। कृपया फिर प्रयास करें।')})
 .finally(function(){busy=false;log.scrollTop=log.scrollHeight})
});
})();